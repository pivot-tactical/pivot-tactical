"""The single ``/ws`` WebSocket channel (spec §6.2, §6.3; roles per direction).

Carries three kinds of traffic on one socket:

* **JSON control** (text frames) — tune/mode/PTT lifecycle and state pushes.
* **Audio** (binary frames) — 16-bit/16 kHz mono PCM. Inbound frames are the
  operator's mic while keyed; the server taps them for the per-station recording
  and renders them per listener on the same net, pushing the result back as
  binary frames to each listener's socket (the WebSocket audio transport, §6.3).

Two connection modes: **trainee** (``?name=&trainee_id=``, bound to its own
terminal's radios) and **instructor** (``?token=``, drives any instructor
radio). Either side may run several radios on one socket — control messages
carry a ``radio_id`` and outbound audio frames are tagged with theirs — and the
server owns crypto-sync timing for both (§3.2.3).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from pivot.audio.pcm import pcm16_to_float32
from pivot.core.crypto import RadioMode
from pivot.core.radios import RadioBusyError


@dataclass
class TraineeContext:
    trainee_id: str
    primary_id: str
    active_tx: set[str]
    sync_tasks: dict[str, asyncio.Task]
    on_radios_changed: Callable[[], None]


@dataclass
class InstructorContext:
    active_tx: set[str]
    sync_tasks: dict[str, asyncio.Task]

router = APIRouter()

_AUDIO_QUEUE_MAX = 64  # ~1.3 s of 20 ms frames; drop rather than lag on a slow client

_ALLOW_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
_ALLOW_ORIGIN_REGEX_STR = r"^https?://(localhost|127\.0\.0\.1|192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+)(:[0-9]+)?\Z"
_ALLOW_ORIGIN_REGEX = re.compile(_ALLOW_ORIGIN_REGEX_STR)


def _is_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    if origin in _ALLOW_ORIGINS:
        return True
    return bool(_ALLOW_ORIGIN_REGEX.match(origin))


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not _is_origin_allowed(ws.headers.get("origin")):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Origin")
        return

    manager = ws.app.state.manager
    auth = getattr(ws.app.state, "auth", None)
    await ws.accept()

    token = ws.cookies.get("pivot_token")
    if token and auth is not None and auth.validate(token):
        await _instructor_session(ws, manager)
    else:
        await _trainee_session(ws, manager)


# --------------------------------------------------------------------------- #
# Trainee
# --------------------------------------------------------------------------- #


async def _trainee_session(ws: WebSocket, manager) -> None:
    name = ws.query_params.get("name", "TRAINEE")
    trainee_id = ws.query_params.get("trainee_id") or str(uuid.uuid4())
    info = manager.login(name, trainee_id)
    primary_id = info["radio_id"]
    login_epoch = info.get("epoch")

    queue = manager.subscribe()
    audio_out: asyncio.Queue = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
    outbound = asyncio.create_task(_pump_outbound(ws, queue))
    audio_pump = asyncio.create_task(_pump_audio(ws, audio_out))
    # A terminal may run several radios (§3.2.2), each on its own frequency and
    # each with its own PTT/crypto-sync lifecycle, so the keyed set and the sync
    # timers are tracked per radio — as they are for the instructor.
    sync_tasks: dict[str, asyncio.Task] = {}
    active_tx: set[str] = set()
    # One tagged sink per radio, so the browser can mix the terminal's radios
    # into one playback stream at independent headset volumes (and mute all but
    # one with the radio view's Focus control).
    sinks: dict[str, object] = {}

    def sync_radio_sinks() -> None:
        live = {r["radio_id"] for r in manager.trainee_radios(trainee_id)}
        for rid in list(sinks):
            if rid not in live:
                manager.unregister_audio_sink(rid, sinks.pop(rid))
        for rid in live:
            if rid not in sinks:
                sink = _tagged_sink(audio_out, rid)
                sinks[rid] = sink
                manager.register_audio_sink(rid, sink)

    sync_radio_sinks()
    await ws.send_json({"type": "welcome", "payload": {"role": "trainee", **info}})
    await ws.send_json({"type": "band_profile_update", "payload": manager.band_profile_snapshot()})

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if active_tx:
                    # One mic frame fans out to every radio this terminal has
                    # keyed; each net renders it under its own conditions.
                    pcm = pcm16_to_float32(message["bytes"])
                    for rid in active_tx:
                        manager.route_tx_frame(rid, pcm)
                continue
            data = json.loads(message["text"])
            mtype = data.get("type")
            payload = data.get("payload") or {}

            await _handle_trainee_message(
                ws,
                manager,
                mtype,
                payload,
                TraineeContext(
                    trainee_id,
                    primary_id,
                    active_tx,
                    sync_tasks,
                    sync_radio_sinks,
                ),
            )
    except WebSocketDisconnect:
        pass
    finally:
        for task in sync_tasks.values():
            _cancel(task)
        # A disconnect mid-keying must not leave radios stuck on the air.
        for rid in active_tx:
            with contextlib.suppress(Exception):
                manager.ptt_end(rid)
        for rid, sink in sinks.items():
            manager.unregister_audio_sink(rid, sink)
        await _shutdown([outbound, audio_pump], manager, queue)
        manager.disconnect(trainee_id, epoch=login_epoch)


# --------------------------------------------------------------------------- #
# Instructor
# --------------------------------------------------------------------------- #


async def _instructor_session(ws: WebSocket, manager) -> None:
    queue = manager.subscribe()
    audio_out: asyncio.Queue = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
    outbound = asyncio.create_task(_pump_outbound(ws, queue))
    audio_pump = asyncio.create_task(_pump_audio(ws, audio_out))
    # The instructor may key several radios at once (one voice on many nets).
    # Each keyed radio runs its own PTT/crypto-sync lifecycle, so the set of
    # keyed radios and the per-radio sync timers are tracked independently.
    sync_tasks: dict[str, asyncio.Task] = {}
    active_tx: set[str] = set()  # the instructor radios currently keyed

    # The instructor hears on every one of their radios. Each radio gets its own
    # sink that tags every PCM frame with the source radio_id, so the browser can
    # mix them into one playback stream at independent headset volumes (§3.2.2).
    # Radios are added/removed over REST as well as over this socket, so the
    # sink set is kept in step via the manager's change watcher rather than
    # only from this loop's own messages.
    sinks: dict[str, object] = {}

    def sync_radio_sinks() -> None:
        live = {r["radio_id"] for r in manager.instructor_radios()}
        for rid in list(sinks):
            if rid not in live:
                manager.unregister_audio_sink(rid, sinks.pop(rid))
        for rid in live:
            if rid not in sinks:
                sink = _tagged_sink(audio_out, rid)
                sinks[rid] = sink
                manager.register_audio_sink(rid, sink)

    sync_radio_sinks()
    unwatch = manager.watch_instructor_radios(sync_radio_sinks)
    await ws.send_json({"type": "welcome", "payload": {"role": "instructor"}})
    await ws.send_json({"type": "band_profile_update", "payload": manager.band_profile_snapshot()})
    await ws.send_json({"type": "instructor_radios", "payload": manager.instructor_radios()})
    await ws.send_json(
        {"type": "terminal_update", "payload": {"terminals": manager.monitor_snapshot()}}
    )

    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if active_tx:
                    # One mic frame fans out to every keyed radio: the source is
                    # decoded once, and each radio's net renders it under its own
                    # channel conditions (frequency-dependent noise, §3.2.2).
                    pcm = pcm16_to_float32(message["bytes"])
                    for rid in active_tx:
                        manager.route_tx_frame(rid, pcm)
                continue
            data = json.loads(message["text"])
            mtype = data.get("type")
            payload = data.get("payload") or {}
            await _handle_instructor_message(
                ws,
                manager,
                mtype,
                payload,
                InstructorContext(active_tx, sync_tasks),
            )
    except WebSocketDisconnect:
        pass
    finally:
        for task in sync_tasks.values():
            _cancel(task)
        # A disconnect mid-keying must not leave radios stuck on the air.
        for rid in active_tx:
            with contextlib.suppress(Exception):
                manager.ptt_end(rid)
        unwatch()
        # Only drop sinks still owned by *this* connection — a reconnected
        # instructor may already have re-bound these radios to a new sink.
        for rid, sink in sinks.items():
            manager.unregister_audio_sink(rid, sink)
        await _shutdown([outbound, audio_pump], manager, queue)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _instructor_radio_id(manager, payload: dict) -> str:
    rid = payload.get("radio_id", "")
    radio = manager.registry.get(rid)
    if radio is None or not radio.is_instructor:
        raise KeyError("not an instructor radio")
    return rid


def _trainee_radio_id(manager, trainee_id: str, payload: dict, default: str) -> str:
    """Resolve the radio a trainee message targets, defaulting to the terminal's
    own radio. A terminal may only drive radios it owns."""
    rid = payload.get("radio_id") or default
    radio = manager.registry.get(rid)
    if radio is None or radio.is_instructor or radio.owner != trainee_id:
        raise KeyError(f"not this terminal's radio: {rid}")
    return rid


async def _handle_trainee_message(
    ws: WebSocket,
    manager,
    mtype: str,
    payload: dict,
    ctx: TraineeContext,
) -> None:
    try:
        if mtype == "heartbeat":
            await ws.send_json({"type": "heartbeat", "payload": {}})
        elif mtype == "tune":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, ctx.primary_id)
            await _safe(ws, "tuned", lambda: manager.tune(rid, payload["frequency"]))
        elif mtype == "mode_change":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, ctx.primary_id)
            await _safe(
                ws, "mode_changed", lambda: manager.set_mode(rid, RadioMode(payload["mode"]))
            )
        elif mtype == "add_radio":
            radio = manager.add_trainee_radio(
                ctx.trainee_id,
                slot=payload.get("slot"),
                frequency=payload.get("frequency"),
                mode=RadioMode(payload["mode"]) if payload.get("mode") else None,
            )
            ctx.on_radios_changed()
            await ws.send_json({"type": "radio_added", "payload": radio})
        elif mtype == "remove_radio":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, "")
            _cancel(ctx.sync_tasks.pop(rid, None))
            ctx.active_tx.discard(rid)
            manager.remove_trainee_radio(ctx.trainee_id, rid)
            ctx.on_radios_changed()
            await ws.send_json({"type": "radio_removed", "payload": {"radio_id": rid}})
        elif mtype == "ptt_start":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, ctx.primary_id)
            result = manager.ptt_start(
                rid,
                frequency=payload.get("frequency"),
                tx_mode=RadioMode(payload["tx_mode"]) if payload.get("tx_mode") else None,
            )
            ctx.active_tx.add(rid)
            # radio_id lets the radio view drive each panel's PTT state
            # independently while several radios are keyed.
            await ws.send_json({"type": "ptt_started", "payload": {**result, "radio_id": rid}})
            if result["sync_applies"]:
                ctx.sync_tasks[rid] = asyncio.create_task(
                    _schedule_on_air(ws, manager, rid, result["sync_delay_ms"])
                )
        elif mtype == "ptt_end":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, ctx.primary_id)
            _cancel(ctx.sync_tasks.pop(rid, None))
            ctx.active_tx.discard(rid)
            await ws.send_json(
                {"type": "ptt_ended", "payload": {**(manager.ptt_end(rid) or {}), "radio_id": rid}}
            )
        elif mtype == "ptt_abort":
            rid = _trainee_radio_id(manager, ctx.trainee_id, payload, ctx.primary_id)
            _cancel(ctx.sync_tasks.pop(rid, None))
            ctx.active_tx.discard(rid)
            await ws.send_json(
                {
                    "type": "ptt_aborted",
                    "payload": {**(manager.ptt_abort(rid) or {}), "radio_id": rid},
                }
            )
        else:
            await ws.send_json({"type": "error", "payload": {"detail": f"unknown: {mtype}"}})
    except (RadioBusyError, KeyError, ValueError) as exc:
        await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})


async def _handle_instructor_message(
    ws: WebSocket,
    manager,
    mtype: str,
    payload: dict,
    ctx: InstructorContext,
) -> None:
    try:
        if mtype == "heartbeat":
            await ws.send_json({"type": "heartbeat", "payload": {}})
        elif mtype == "instr_tune":
            rid = _instructor_radio_id(manager, payload)
            await ws.send_json(
                {"type": "tuned", "payload": manager.tune(rid, payload["frequency"])}
            )
        elif mtype == "instr_mode":
            rid = _instructor_radio_id(manager, payload)
            await ws.send_json(
                {
                    "type": "mode_changed",
                    "payload": manager.set_mode(rid, RadioMode(payload["mode"])),
                }
            )
        elif mtype == "instr_rx_noise":
            # Per-radio receive-noise toggle (§3.1.5). The state push
            # rides on the manager's instructor_radios broadcast, so
            # every open console stays in step.
            manager.set_rx_noise(
                _instructor_radio_id(manager, payload), bool(payload.get("enabled", True))
            )
        elif mtype == "instr_add_radio":
            # Sink binding and the instructor_radios push both ride on
            # the manager's change watcher/broadcast (shared with REST).
            manager.add_instructor_radio(payload.get("label"), payload.get("frequency"))
        elif mtype == "instr_remove_radio":
            manager.remove_instructor_radio(payload.get("radio_id", ""))
        elif mtype == "instr_ptt_start":
            rid = _instructor_radio_id(manager, payload)
            result = manager.ptt_start(
                rid,
                frequency=payload.get("frequency"),
                tx_mode=RadioMode(payload["tx_mode"]) if payload.get("tx_mode") else None,
            )
            ctx.active_tx.add(rid)
            # radio_id lets the console drive each card's PTT state
            # independently while several radios are keyed.
            await ws.send_json({"type": "ptt_started", "payload": {**result, "radio_id": rid}})
            if result["sync_applies"]:
                ctx.sync_tasks[rid] = asyncio.create_task(
                    _schedule_on_air(ws, manager, rid, result["sync_delay_ms"])
                )
        elif mtype == "instr_ptt_end":
            rid = _instructor_radio_id(manager, payload)
            _cancel(ctx.sync_tasks.pop(rid, None))
            ctx.active_tx.discard(rid)
            await ws.send_json(
                {"type": "ptt_ended", "payload": {**(manager.ptt_end(rid) or {}), "radio_id": rid}}
            )
        elif mtype == "instr_ptt_abort":
            rid = _instructor_radio_id(manager, payload)
            _cancel(ctx.sync_tasks.pop(rid, None))
            ctx.active_tx.discard(rid)
            await ws.send_json(
                {
                    "type": "ptt_aborted",
                    "payload": {**(manager.ptt_abort(rid) or {}), "radio_id": rid},
                }
            )
        else:
            await ws.send_json({"type": "error", "payload": {"detail": f"unknown: {mtype}"}})
    except (RadioBusyError, KeyError, ValueError) as exc:
        await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})


def _tagged_sink(audio_out: asyncio.Queue, radio_id: str):
    """A sink that prefixes each PCM frame with its source radio.

    One connection's several radios share one playback stream, so each frame is
    tagged ``[1-byte id length][radio_id ascii][PCM16LE…]`` and the browser
    scales it to that radio's headset volume (mirrored in
    ``frontend/src/audio.ts: parseTaggedAudio``). Used for both roles — the
    instructor's radio cards and a trainee terminal's radios. It drops a frame
    rather than lag when the client is backed up.
    """
    raw = radio_id.encode("ascii")
    header = bytes([len(raw)]) + raw

    def put(data: bytes) -> None:
        try:
            audio_out.put_nowait(header + data)
        except asyncio.QueueFull:
            pass

    return put


async def _pump_outbound(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Forward JSON broadcast messages from the manager to this client."""
    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _pump_audio(ws: WebSocket, audio_out: asyncio.Queue) -> None:
    """Forward rendered PCM frames to this client as binary."""
    try:
        while True:
            data = await audio_out.get()
            await ws.send_bytes(data)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _safe(ws: WebSocket, ok_type: str, action) -> None:
    try:
        await ws.send_json({"type": ok_type, "payload": action()})
    except (RadioBusyError, KeyError, ValueError) as exc:
        await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})


async def _schedule_on_air(ws: WebSocket, manager, radio_id: str, delay_ms: int) -> None:
    """After the crypto sync delay, put the station on air (§3.2.3)."""
    await asyncio.sleep(delay_ms / 1000.0)
    manager.ptt_sync_complete(radio_id)
    await ws.send_json({"type": "secure_tx", "payload": {"radio_id": radio_id}})


async def _shutdown(tasks: list[asyncio.Task], manager, queue: asyncio.Queue) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    manager.unsubscribe(queue)


def _cancel(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()
    return None

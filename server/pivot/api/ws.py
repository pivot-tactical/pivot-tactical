"""The single ``/ws`` WebSocket channel (spec §6.2, §6.3; roles per direction).

Two connection modes, distinguished at connect time:

* **Trainee** (``?name=&trainee_id=``) — bound to its own radio. Handles
  ``tune``/``mode_change``/``ptt_*``/``heartbeat`` and receives state pushes.
* **Instructor** (``?token=<bearer>``) — authenticated; receives the same state
  pushes (so the live event log, monitor and band updates stream in) and may
  drive any *instructor* radio via ``instr_*`` messages.

The server owns crypto-sync timing for both: a Cypher ``ptt_start`` schedules the
station on-air after the configured delay, and an abort arriving first cancels it
(§3.2.3). Voice media travels over WebRTC, not this socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pivot.core.crypto import RadioMode
from pivot.core.radios import RadioBusyError

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    manager = ws.app.state.manager
    auth = getattr(ws.app.state, "auth", None)
    await ws.accept()

    token = ws.query_params.get("token")
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
    radio_id = info["radio_id"]

    queue = manager.subscribe()
    outbound = asyncio.create_task(_pump_outbound(ws, queue))
    sync_task: asyncio.Task | None = None

    await ws.send_json({"type": "welcome", "payload": {"role": "trainee", **info}})
    await ws.send_json({"type": "band_profile_update", "payload": manager.band_profile_snapshot()})

    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")
            payload = data.get("payload") or {}

            if mtype == "heartbeat":
                await ws.send_json({"type": "heartbeat", "payload": {}})
            elif mtype == "tune":
                await _safe(ws, "tuned", lambda p=payload: manager.tune(radio_id, p["frequency"]))
            elif mtype == "mode_change":
                await _safe(ws, "mode_changed",
                            lambda p=payload: manager.set_mode(radio_id, RadioMode(p["mode"])))
            elif mtype == "ptt_start":
                result = manager.ptt_start(
                    radio_id,
                    frequency=payload.get("frequency"),
                    tx_mode=RadioMode(payload["tx_mode"]) if payload.get("tx_mode") else None,
                )
                await ws.send_json({"type": "ptt_started", "payload": result})
                if result["sync_applies"]:
                    sync_task = asyncio.create_task(
                        _schedule_on_air(ws, manager, radio_id, result["sync_delay_ms"])
                    )
            elif mtype == "ptt_end":
                sync_task = _cancel(sync_task)
                await ws.send_json({"type": "ptt_ended", "payload": manager.ptt_end(radio_id) or {}})
            elif mtype == "ptt_abort":
                sync_task = _cancel(sync_task)
                await ws.send_json({"type": "ptt_aborted", "payload": manager.ptt_abort(radio_id) or {}})
            elif mtype in ("webrtc_offer", "webrtc_answer", "webrtc_ice"):
                await _handle_signalling(ws, manager, radio_id, mtype, payload)
            else:
                await ws.send_json({"type": "error", "payload": {"detail": f"unknown: {mtype}"}})
    except WebSocketDisconnect:
        pass
    finally:
        _cancel(sync_task)
        await _shutdown(outbound, manager, queue)
        manager.disconnect(trainee_id)


# --------------------------------------------------------------------------- #
# Instructor
# --------------------------------------------------------------------------- #


async def _instructor_session(ws: WebSocket, manager) -> None:
    queue = manager.subscribe()
    outbound = asyncio.create_task(_pump_outbound(ws, queue))
    sync_task: asyncio.Task | None = None

    await ws.send_json({"type": "welcome", "payload": {"role": "instructor"}})
    await ws.send_json({"type": "band_profile_update", "payload": manager.band_profile_snapshot()})
    await ws.send_json({"type": "instructor_radios", "payload": manager.instructor_radios()})
    await ws.send_json({"type": "terminal_update", "payload": {"terminals": manager.monitor_snapshot()}})

    def radio_id_of(payload: dict) -> str:
        rid = payload.get("radio_id", "")
        radio = manager.registry.get(rid)
        if radio is None or not radio.is_instructor:
            raise KeyError("not an instructor radio")
        return rid

    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")
            payload = data.get("payload") or {}
            try:
                if mtype == "heartbeat":
                    await ws.send_json({"type": "heartbeat", "payload": {}})
                elif mtype == "instr_tune":
                    rid = radio_id_of(payload)
                    await ws.send_json({"type": "tuned", "payload": manager.tune(rid, payload["frequency"])})
                elif mtype == "instr_mode":
                    rid = radio_id_of(payload)
                    await ws.send_json({"type": "mode_changed",
                                        "payload": manager.set_mode(rid, RadioMode(payload["mode"]))})
                elif mtype == "instr_add_radio":
                    radio = manager.add_instructor_radio(payload.get("label"),
                                                         payload.get("frequency", "145.500 MHz"))
                    await ws.send_json({"type": "instructor_radios",
                                        "payload": manager.instructor_radios()})
                    _ = radio
                elif mtype == "instr_remove_radio":
                    manager.remove_instructor_radio(payload.get("radio_id", ""))
                    await ws.send_json({"type": "instructor_radios",
                                        "payload": manager.instructor_radios()})
                elif mtype == "instr_ptt_start":
                    rid = radio_id_of(payload)
                    result = manager.ptt_start(rid, frequency=payload.get("frequency"),
                                               tx_mode=RadioMode(payload["tx_mode"]) if payload.get("tx_mode") else None)
                    await ws.send_json({"type": "ptt_started", "payload": result})
                    if result["sync_applies"]:
                        sync_task = asyncio.create_task(
                            _schedule_on_air(ws, manager, rid, result["sync_delay_ms"]))
                elif mtype == "instr_ptt_end":
                    sync_task = _cancel(sync_task)
                    await ws.send_json({"type": "ptt_ended",
                                        "payload": manager.ptt_end(radio_id_of(payload)) or {}})
                elif mtype == "instr_ptt_abort":
                    sync_task = _cancel(sync_task)
                    await ws.send_json({"type": "ptt_aborted",
                                        "payload": manager.ptt_abort(radio_id_of(payload)) or {}})
                else:
                    await ws.send_json({"type": "error", "payload": {"detail": f"unknown: {mtype}"}})
            except (RadioBusyError, KeyError, ValueError) as exc:
                await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})
    except WebSocketDisconnect:
        pass
    finally:
        _cancel(sync_task)
        await _shutdown(outbound, manager, queue)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


async def _pump_outbound(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Forward broadcast messages from the manager to this client."""
    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
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


async def _handle_signalling(ws: WebSocket, manager, radio_id: str, mtype: str, payload: dict) -> None:
    """Tunnel WebRTC signalling to the audio router if one is attached (§6.3)."""
    router_obj = getattr(ws.app.state, "audio_router", None)
    if router_obj is None:
        await ws.send_json({"type": "webrtc_unavailable", "payload": {"detail": "audio router not started"}})
        return
    answer = await router_obj.handle_signalling(radio_id, mtype, payload)
    if answer is not None:
        await ws.send_json(answer)


async def _shutdown(outbound: asyncio.Task, manager, queue: asyncio.Queue) -> None:
    outbound.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await outbound
    manager.unsubscribe(queue)


def _cancel(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()
    return None

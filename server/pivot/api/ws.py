"""The single ``/ws`` WebSocket channel (spec §6.2, §6.3).

Carries the ``{type, payload}`` envelope for:

* **state sync** — server pushes ``band_profile_update``, ``scenario_event``,
  ``timezone_update``, ``session_started/ended``, ``terminal_update`` etc.;
* **PTT control** — ``tune``, ``mode_change``, ``ptt_start``, ``ptt_end``,
  ``ptt_abort``, ``heartbeat`` from the client. The server owns crypto-sync
  timing: on a Cypher ``ptt_start`` it schedules the station on-air after the
  configured delay, and an ``ptt_abort`` arriving first cancels it (§3.2.3);
* **WebRTC signalling** — SDP offer/answer and ICE candidates are tunnelled here
  and handed to the audio router when present (§6.3). On a LAN, ICE uses host
  candidates only (no STUN/TURN).

The actual voice media travels over WebRTC, not this socket.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from pivot.core.crypto import RadioMode
from pivot.core.radios import RadioBusyError

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    manager = ws.app.state.manager
    await ws.accept()

    name = ws.query_params.get("name", "TRAINEE")
    trainee_id = ws.query_params.get("trainee_id")
    if not trainee_id:
        import uuid

        trainee_id = str(uuid.uuid4())
    info = manager.login(name, trainee_id)
    radio_id = info["radio_id"]

    queue = manager.subscribe()
    sync_task: asyncio.Task | None = None

    async def pump_outbound() -> None:
        """Forward broadcast messages from the manager to this client."""
        try:
            while True:
                msg = await queue.get()
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass

    outbound = asyncio.create_task(pump_outbound())

    # Initial state snapshot for this client (§7.2 needs freq/mode/profile/tz).
    await ws.send_json({"type": "welcome", "payload": info})
    await ws.send_json({"type": "band_profile_update", "payload": manager.band_profile_snapshot()})

    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")
            payload = data.get("payload") or {}

            if mtype == "heartbeat":
                await ws.send_json({"type": "heartbeat", "payload": {}})

            elif mtype == "tune":
                try:
                    radio = manager.tune(radio_id, payload["frequency"])
                    await ws.send_json({"type": "tuned", "payload": radio})
                except (RadioBusyError, KeyError, ValueError) as exc:
                    await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})

            elif mtype == "mode_change":
                try:
                    radio = manager.set_mode(radio_id, RadioMode(payload["mode"]))
                    await ws.send_json({"type": "mode_changed", "payload": radio})
                except (RadioBusyError, KeyError, ValueError) as exc:
                    await ws.send_json({"type": "error", "payload": {"detail": str(exc)}})

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
                event = manager.ptt_end(radio_id)
                await ws.send_json({"type": "ptt_ended", "payload": event or {}})

            elif mtype == "ptt_abort":
                sync_task = _cancel(sync_task)
                event = manager.ptt_abort(radio_id)
                await ws.send_json({"type": "ptt_aborted", "payload": event or {}})

            elif mtype in ("webrtc_offer", "webrtc_answer", "webrtc_ice"):
                await _handle_signalling(ws, manager, radio_id, mtype, payload)

            else:
                await ws.send_json(
                    {"type": "error", "payload": {"detail": f"unknown message: {mtype}"}}
                )
    except WebSocketDisconnect:
        pass
    finally:
        _cancel(sync_task)
        outbound.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await outbound
        manager.unsubscribe(queue)
        manager.disconnect(trainee_id)


async def _schedule_on_air(ws: WebSocket, manager, radio_id: str, delay_ms: int) -> None:
    """After the crypto sync delay, put the station on air (§3.2.3)."""
    try:
        await asyncio.sleep(delay_ms / 1000.0)
        manager.ptt_sync_complete(radio_id)
        await ws.send_json({"type": "secure_tx", "payload": {"radio_id": radio_id}})
    except asyncio.CancelledError:  # PTT released during sync → abort path
        raise


async def _handle_signalling(ws: WebSocket, manager, radio_id: str, mtype: str, payload: dict) -> None:
    """Tunnel WebRTC signalling to the audio router if one is attached (§6.3)."""
    router_obj = getattr(ws.app.state, "audio_router", None)
    if router_obj is None:
        await ws.send_json(
            {"type": "webrtc_unavailable", "payload": {"detail": "audio router not started"}}
        )
        return
    answer = await router_obj.handle_signalling(radio_id, mtype, payload)
    if answer is not None:
        await ws.send_json(answer)


def _cancel(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()
    return None

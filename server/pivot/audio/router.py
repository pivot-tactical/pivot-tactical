"""WebRTC audio router (spec §6.3, Appendix A).

The server is the single audio endpoint: it holds one WebRTC peer connection per
terminal, receives each transmitter's mic stream when keyed, taps it for the
clean per-station recording, and renders exactly what each listener on the same
frequency should hear (clear / hash / collision) before encoding it back over
that listener's own connection. Per-listener rendering is the natural model here
rather than a special case (Appendix A.2).

This module orchestrates aiortc; aiortc + PyAV are the ``audio`` extra and are
imported lazily so the rest of the server (and CI) runs without the native media
stack. The pure render/grouping core lives in :mod:`pivot.audio.mixer` and is
fully unit-tested; this class adds peer-connection lifecycle, the recording tap,
and the real-time fan-out loop on top of it.

Signal flow (Appendix A.3), for station A keyed on frequency F::

    Browser A mic --WebRTC--> Server
        |-- [recording tap]            -> clean WAV + freq/mode metadata
        |-- per-listener render loop, for each station tuned to F:
                Cypher listener: clean voice -> freq DSP  -> Opus -> listener
                Plain  listener: voice env  -> HASH -> DSP -> Opus -> listener
                on collision:    plain mix  / crypto-jam render

Transmitting stations are half-duplex: they receive nothing while keyed.
"""

from __future__ import annotations

import logging

import numpy as np

from pivot.audio.mixer import distinct_renders, group_renders, render_net_frame
from pivot.config import RECORDING_SAMPLE_RATE
from pivot.dsp.engine import DspEngine
from pivot.runtime.manager import SessionManager

log = logging.getLogger("pivot.audio.router")

# Opus on a LAN; narrowband/wideband consistent with the simulated voice
# bandwidth (§6.3). 20 ms frames at the recording rate.
FRAME_MS = 20
FRAME_SAMPLES = RECORDING_SAMPLE_RATE * FRAME_MS // 1000


def aiortc_available() -> bool:
    try:
        import aiortc  # noqa: F401

        return True
    except Exception:
        return False


class AudioRouter:
    """Owns the WebRTC peer connections and the per-listener render loop.

    Constructed by the app when the ``audio`` extra is present and attached to
    ``app.state.audio_router`` so the WebSocket signalling handler can reach it
    (:mod:`pivot.api.ws`).
    """

    def __init__(self, manager: SessionManager, engine: DspEngine | None = None) -> None:
        self.manager = manager
        self.engine = engine or DspEngine(sample_rate=RECORDING_SAMPLE_RATE)
        self._pcs: dict[str, object] = {}  # radio_id -> RTCPeerConnection
        self._inbound: dict[str, object] = {}  # radio_id -> active mic track
        self._outbound: dict[str, object] = {}  # radio_id -> per-listener track
        self._started = False

    # -- lifecycle --------------------------------------------------------- #

    async def start(self) -> None:
        if not aiortc_available():
            raise RuntimeError(
                "aiortc is not installed; install the 'audio' extra to start the "
                "WebRTC audio router (the rest of the server runs without it)."
            )
        self._started = True
        log.info("audio router started (sr=%d, frame=%dms)", self.engine.sample_rate, FRAME_MS)

    async def stop(self) -> None:
        for pc in list(self._pcs.values()):
            try:
                await pc.close()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                log.exception("error closing peer connection")
        self._pcs.clear()
        self._inbound.clear()
        self._outbound.clear()
        self._started = False

    # -- signalling (called from the WebSocket, §6.2/§6.3) ----------------- #

    async def handle_signalling(self, radio_id: str, mtype: str, payload: dict) -> dict | None:
        """Handle one signalling message; return a message to send back, if any.

        On a LAN, ICE uses host candidates only — no STUN/TURN server is
        configured (§6.3). The offer/answer exchange wires the mic (inbound) and
        a per-listener outbound track for this terminal.
        """
        if not self._started:
            return {"type": "webrtc_unavailable", "payload": {"detail": "router not started"}}

        from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription

        if mtype == "webrtc_offer":
            pc = RTCPeerConnection()
            self._pcs[radio_id] = pc
            self._wire_peer(pc, radio_id)
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
            )
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            return {
                "type": "webrtc_answer",
                "payload": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            }

        if mtype == "webrtc_ice":
            pc = self._pcs.get(radio_id)
            if pc is not None and payload.get("candidate"):
                await pc.addIceCandidate(RTCIceCandidate(**payload["candidate"]))
            return None

        return None

    def _wire_peer(self, pc, radio_id: str) -> None:
        """Attach track/connection handlers to a fresh peer connection."""

        @pc.on("connectionstatechange")
        async def _on_state():  # pragma: no cover - network callback
            log.info("peer %s state=%s", radio_id, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                self._pcs.pop(radio_id, None)

        @pc.on("track")
        def _on_track(track):  # pragma: no cover - media callback
            if track.kind == "audio":
                self._inbound[radio_id] = track
                # The recording tap + render fan-out run while this radio is on
                # air; see _consume_inbound for the per-frame loop.
                import asyncio

                asyncio.ensure_future(self._consume_inbound(radio_id, track))

    # -- the render loop (Appendix A.3) ------------------------------------ #

    async def _consume_inbound(self, radio_id: str, track) -> None:  # pragma: no cover - media
        """Pull mic frames, tap for recording, and fan out per-listener renders.

        Runs for the duration of a transmission. Each 20 ms block:
          1. decode the transmitter's clean frame,
          2. tap it to the recording accumulator (clean, pre-DSP, §3.5.1),
          3. compute the net's render map (who hears what) from the registry,
          4. render each *distinct* reception once (fan-out, A.4),
          5. push each rendered frame to the matching listeners' outbound tracks.
        """
        from av import AudioFrame  # noqa: F401 (PyAV; ensures extra present)

        radio = self.manager.registry.get(radio_id)
        while radio is not None and radio.on_air:
            try:
                frame = await track.recv()
            except Exception:
                break
            clean = _frame_to_mono_f32(frame)
            self.manager.push_tx_audio(radio_id, clean)

            freq = radio.frequency_hz
            render_map = self.manager.registry.render_map_for_net(freq)
            conditions = self.manager.band_profile.conditions_at(freq)
            active = {
                t.radio_id: clean
                for t in self.manager.registry.active_transmitters_on_net(freq)
            }
            # Instructor radios with their noise toggle off get a separate
            # noiseless render pass (same crypto rules, no channel noise).
            for sub_map, cond in self.manager.split_rx_noise(render_map, conditions):
                needed = distinct_renders(sub_map)
                if not needed:
                    continue
                rendered = render_net_frame(active, cond, needed, self.engine)
                self._dispatch(sub_map, rendered)
            radio = self.manager.registry.get(radio_id)

    def _dispatch(self, render_map, rendered) -> None:  # pragma: no cover - media
        """Send each rendered frame to the listeners grouped under it (A.4)."""
        for reception, listeners in group_renders(render_map).items():
            frame = rendered.get(reception)
            if frame is None:
                continue
            for listener_id in listeners:
                out = self._outbound.get(listener_id)
                if out is not None:
                    out.push(frame)


def _frame_to_mono_f32(frame) -> np.ndarray:  # pragma: no cover - media
    """Convert a PyAV ``AudioFrame`` to mono float32 in [-1, 1]."""
    arr = frame.to_ndarray()
    if arr.ndim > 1:
        arr = arr.mean(axis=0)
    if np.issubdtype(arr.dtype, np.integer):
        arr = arr.astype(np.float32) / np.iinfo(arr.dtype).max
    return arr.astype(np.float32)

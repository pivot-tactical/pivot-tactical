"""Continuous ambient band-noise broadcaster (spec §3.2.2, §4.1.1).

A real receiver hisses on a tuned channel even when no one is talking — open
squelch. This task supplies that "hash": ~50 times a second it asks the manager
to render one ambient noise-floor frame per active net and fan it out to the idle
listeners on that net. It is deliberately server-side and per-frequency so that

* every operator on a net hears the *same* floor (training consistency), and
* a transmission's recording carries the matching band conditions, so the
  instructor's AAR playback has the same noise the trainees heard live.

The loop runs on the event loop (the audio sinks are ``asyncio.Queue`` writers,
which are not thread-safe) and is paced by the loop clock so the stream stays at
real time without drifting into latency. It never raises out of the loop — a bad
tick is logged and the cadence continues.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from pivot.config import RECORDING_SAMPLE_RATE

log = logging.getLogger("pivot.audio.noise")

FRAME_MS = 20  # matches the transmission frame size and the player worklet


class NoiseBroadcaster:
    """Paces the manager's idle-noise tick at a steady real-time cadence."""

    def __init__(self, manager, frame_ms: int = FRAME_MS) -> None:
        self.manager = manager
        self.frame_ms = frame_ms
        self.frame_samples = RECORDING_SAMPLE_RATE * frame_ms // 1000
        self._task: asyncio.Task | None = None
        # radio_ids that already have their jitter cushion; owned here, mutated
        # by the manager tick so a re-keyed station re-primes after its TX.
        self._primed: set[str] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="noise-broadcaster")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        period = self.frame_ms / 1000.0
        next_t = loop.time()
        while True:
            try:
                if self.manager.session_active:
                    self.manager.render_idle_noise_tick(self.frame_samples, self._primed)
                else:
                    self._primed.clear()  # re-prime jitter cushion when next session starts
            except Exception:  # never let the ambient stream die
                log.exception("idle-noise tick failed")
            next_t += period
            delay = next_t - loop.time()
            if delay < -0.5:
                # Fell badly behind (e.g. the loop was blocked) — resync rather
                # than burst-emit a backlog of frames.
                next_t = loop.time()
                delay = period
            await asyncio.sleep(max(0.0, delay))

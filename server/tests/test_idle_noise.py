"""Continuous ambient band noise / open-squelch 'hash' (spec §3.2.2, §4.1.1).

Covers the DSP idle-noise floor and the manager's per-tick fan-out policy: one
identical frame per net to its idle listeners, nothing to keyed stations or to
nets with a station on air, and one frame per shared sink (instructor).
"""

from __future__ import annotations

import numpy as np
import pytest

from pivot.core.bands import BandProfile, JammingSpan
from pivot.dsp.engine import DspEngine
from pivot.dsp.filters import rms
from pivot.runtime.manager import SessionManager

FRAME = 320  # 20 ms at 16 kHz


@pytest.fixture
def manager(database, settings):
    return SessionManager(database, settings)


# --- DSP: the noise floor itself ------------------------------------------- #


def _conditions(profile: BandProfile, mhz: float):
    return profile.conditions_at(mhz * 1e6)


def test_idle_noise_has_expected_length_and_energy():
    eng = DspEngine(sample_rate=16_000)
    cond = BandProfile().conditions_at(14_250_000.0)
    out = eng.render_idle_noise(FRAME, cond, rng=np.random.default_rng(1))
    assert out.shape == (FRAME,)
    assert rms(out) > 0.0  # it actually hisses
    assert np.max(np.abs(out)) <= 1.0  # soft-clipped, never overdriven


def test_idle_noise_louder_on_noisy_band_than_clean():
    eng = DspEngine(sample_rate=16_000)
    prof = BandProfile()
    rng = np.random.default_rng(0)
    low_hf = eng.render_idle_noise(FRAME, _conditions(prof, 3.0), rng=rng)   # noisy
    uhf = eng.render_idle_noise(FRAME, _conditions(prof, 400.0), rng=rng)    # clean
    assert rms(low_hf) > rms(uhf)


def test_idle_noise_zero_length():
    eng = DspEngine(sample_rate=16_000)
    cond = BandProfile().conditions_at(145_500_000.0)
    assert eng.render_idle_noise(0, cond).size == 0


def test_jamming_makes_idle_noise_loud():
    eng = DspEngine(sample_rate=16_000)
    prof = BandProfile()
    clean = eng.render_idle_noise(FRAME, _conditions(prof, 145.5), rng=np.random.default_rng(2))
    prof.jamming = [JammingSpan(140e6, 150e6)]
    jammed = eng.render_idle_noise(FRAME, _conditions(prof, 145.5), rng=np.random.default_rng(2))
    assert rms(jammed) > rms(clean)


# --- manager: per-tick fan-out policy -------------------------------------- #


class _Sink:
    def __init__(self):
        self.frames: list[bytes] = []

    def __call__(self, data: bytes):
        self.frames.append(data)


def _idle_listener(manager, name, tid, freq):
    info = manager.login(name, tid)
    rid = info["radio_id"]
    manager.tune(rid, freq)
    sink = _Sink()
    manager.register_audio_sink(rid, sink)
    return rid, sink


def test_same_net_listeners_get_identical_frame(manager):
    manager.start_session("EX")
    _, a = _idle_listener(manager, "ALPHA", "t-1", "14.250 MHz")
    _, b = _idle_listener(manager, "BRAVO", "t-2", "14.250 MHz")
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)   # primes (extra cushion frames)
    manager.render_idle_noise_tick(FRAME, primed)   # steady: one shared frame each
    # The last frame each received this tick is the same shared per-net buffer.
    assert a.frames[-1] == b.frames[-1]
    assert len(a.frames[-1]) == FRAME * 2  # 16-bit samples


def test_different_nets_get_their_own_frames(manager):
    manager.start_session("EX")
    _, a = _idle_listener(manager, "ALPHA", "t-1", "7.100 MHz")
    _, b = _idle_listener(manager, "BRAVO", "t-2", "145.500 MHz")
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)
    manager.render_idle_noise_tick(FRAME, primed)
    assert a.frames and b.frames
    assert a.frames[-1] != b.frames[-1]  # different bands -> different floors


def test_keyed_station_gets_no_idle_noise(manager):
    manager.start_session("EX")
    tx_rid, tx_sink = _idle_listener(manager, "TX", "tx", "14.250 MHz")
    _, rx_sink = _idle_listener(manager, "RX", "rx", "14.250 MHz")
    manager.ptt_start(tx_rid)  # plain -> straight on air
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)
    manager.render_idle_noise_tick(FRAME, primed)
    # A net with a station on air is served by the live TX render, not idle hash.
    assert tx_sink.frames == []
    assert rx_sink.frames == []


def test_idle_resumes_after_transmission_ends(manager):
    manager.start_session("EX")
    tx_rid, _ = _idle_listener(manager, "TX", "tx", "14.250 MHz")
    _, rx_sink = _idle_listener(manager, "RX", "rx", "14.250 MHz")
    manager.ptt_start(tx_rid)
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)
    assert rx_sink.frames == []
    manager.ptt_end(tx_rid)
    manager.render_idle_noise_tick(FRAME, primed)
    assert rx_sink.frames  # hiss returns once the channel goes quiet


def test_shared_sink_gets_one_frame_per_tick(manager):
    # An instructor binds several radios to one queue; a tick must not flood it.
    manager.start_session("EX")
    a = manager.login("ALPHA", "t-1")["radio_id"]
    b = manager.login("BRAVO", "t-2")["radio_id"]
    manager.tune(a, "7.100 MHz")
    manager.tune(b, "145.500 MHz")
    shared = _Sink()
    manager.register_audio_sink(a, shared)
    manager.register_audio_sink(b, shared)
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)  # priming may add cushion once
    shared.frames.clear()
    manager.render_idle_noise_tick(FRAME, primed)  # steady tick
    assert len(shared.frames) == 1  # one frame for the shared sink, not two


def test_no_sinks_is_a_noop(manager):
    manager.start_session("EX")
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)  # must not raise
    assert primed == set()


# --- end to end: the broadcaster actually reaches the WebSocket ------------- #


def test_ambient_noise_reaches_an_idle_trainee(tmp_path):
    """Hash reaches an idle trainee only after a scenario has been started."""
    from fastapi.testclient import TestClient

    from pivot.api.app import create_app
    from pivot.api.deps import require_instructor
    from pivot.config import Settings

    settings = Settings(data_dir=tmp_path / "data", ambient_noise=True)
    app = create_app(settings)
    app.dependency_overrides[require_instructor] = lambda: None
    with TestClient(app) as c:
        c.post("/api/admin/session/start", json={"name": "EX"})
        with c.websocket_connect("/ws?name=HISS&trainee_id=n-1") as ws:
            got_bytes = False
            for _ in range(100):  # tolerate JSON (welcome/profile) interleaving
                msg = ws.receive()
                if msg.get("type") == "websocket.close":
                    break
                if msg.get("bytes") is not None:
                    got_bytes = True
                    break
            assert got_bytes, "expected ambient noise PCM frames on an idle channel"


def test_no_noise_tick_without_session(manager):
    """render_idle_noise_tick must not emit any frames before a session starts."""
    rid, sink = _idle_listener(manager, "QUIET", "t-q", "145.500 MHz")
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)
    assert sink.frames == [], "no hash should flow before a scenario is running"


def test_noise_stops_after_session_ends(manager):
    """Hash stops as soon as the session is ended."""
    manager.start_session("EX")
    rid, sink = _idle_listener(manager, "ALPHA", "t-1", "145.500 MHz")
    primed: set[str] = set()
    manager.render_idle_noise_tick(FRAME, primed)
    assert sink.frames, "hash should flow during the session"
    manager.end_session()
    sink.frames.clear()
    manager.render_idle_noise_tick(FRAME, primed)
    assert sink.frames == [], "hash must stop after session ends"

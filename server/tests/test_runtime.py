"""Integration tests for the live SessionManager (spec §3.2-§3.5)."""

import numpy as np
import pytest

from pivot.audio.render import AarCryptoView, PlaybackMode, render_event
from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.db import repository as repo
from pivot.runtime.manager import SessionManager


@pytest.fixture
def manager(database, settings):
    return SessionManager(database, settings)


def tone(seconds=0.3, sr=16000, freq=440):
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_login_creates_radio_on_default_freq(manager):
    info = manager.login("ALPHA", "t-1")
    assert info["mode"] == "Plain"
    assert manager.registry.get("t-1") is not None


def test_plain_tx_to_listener_is_heard_and_recorded(manager):
    manager.start_session("EX1")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "14.250 MHz")
    manager.tune("t-2", "14.250 MHz")

    start = manager.ptt_start("t-1")
    assert start["sync_applies"] is False  # plain → immediate
    event = manager.ptt_end("t-1", audio=tone())

    assert event["audibility"] == Audibility.HEARD.value
    assert event["sync_status"] == SyncStatus.COMPLETED.value
    assert event["band_region"] == "High HF"
    assert event["duration_ms"] > 0
    # Recording exists on disk.
    path = manager.settings.recordings_dir / event["audio_path"]
    assert path.exists()


def test_transmission_with_no_listeners_is_unheard(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.tune("t-1", "7.100 MHz")
    manager.ptt_start("t-1")
    event = manager.ptt_end("t-1", audio=tone())
    assert event["audibility"] == Audibility.UNHEARD_NO_LISTENERS.value


def test_cypher_keying_applies_sync_then_on_air(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "14.250 MHz")
    manager.tune("t-2", "14.250 MHz")
    manager.set_mode("t-1", RadioMode.CYPHER)

    start = manager.ptt_start("t-1")
    assert start["sync_applies"] is True
    assert start["sync_delay_ms"] == 1500
    # Not on air yet during sync.
    assert manager.registry.get("t-1").on_air is False
    manager.ptt_sync_complete("t-1")
    assert manager.registry.get("t-1").on_air is True
    event = manager.ptt_end("t-1", audio=tone())
    assert event["tx_mode"] == RadioMode.CYPHER.value
    assert event["sync_status"] == SyncStatus.COMPLETED.value


def test_crypto_sync_abort_still_recorded(manager):
    """PTT released during sync: not on air, but recorded + flagged (§3.2.3)."""
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.set_mode("t-1", RadioMode.CYPHER)
    manager.ptt_start("t-1")
    event = manager.ptt_abort("t-1", audio=tone())
    assert event["sync_status"] == SyncStatus.ABORTED.value
    path = manager.settings.recordings_dir / event["audio_path"]
    assert path.exists()


def test_plain_collision_audibility(manager):
    manager.start_session("EX")
    for cs, tid in [("ALPHA", "t-1"), ("BRAVO", "t-2"), ("RX", "t-3")]:
        manager.login(cs, tid)
        manager.tune(tid, "30.100 MHz")
    manager.ptt_start("t-1")
    manager.ptt_start("t-2")  # overlap
    e1 = manager.ptt_end("t-1", audio=tone())
    e2 = manager.ptt_end("t-2", audio=tone())
    assert e1["audibility"] == Audibility.PLAIN_COLLISION.value
    assert e2["audibility"] == Audibility.PLAIN_COLLISION.value


def test_cypher_collision_suppresses_second(manager):
    manager.start_session("EX")
    for cs, tid in [("ALPHA", "t-1"), ("BRAVO", "t-2"), ("RX", "t-3")]:
        manager.login(cs, tid)
        manager.tune(tid, "14.250 MHz")
        manager.set_mode(tid, RadioMode.CYPHER)
    # First keyer goes on air...
    manager.ptt_start("t-1")
    manager.ptt_sync_complete("t-1")
    # Second keyer collides.
    manager.ptt_start("t-2")
    manager.ptt_sync_complete("t-2")
    e2 = manager.ptt_end("t-2", audio=tone())
    e1 = manager.ptt_end("t-1", audio=tone())
    assert e1["audibility"] == Audibility.HEARD.value
    assert e2["audibility"] == Audibility.CYPHER_SUPPRESSED.value


def test_mode_persists_across_reconnect(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.set_mode("t-1", RadioMode.CYPHER)
    manager.tune("t-1", "7.055 MHz")
    manager.disconnect("t-1")
    # Reconnect: same trainee_id, mode + frequency restored (§3.4.4, §8.3).
    info = manager.login("ALPHA", "t-1")
    assert info["mode"] == RadioMode.CYPHER.value
    assert "7.055" in info["frequency"]


def test_instructor_radio_lifecycle(manager):
    r = manager.add_instructor_radio("Radio 1", "40.000 MHz")
    assert r["is_instructor"] is True
    assert r["name"].startswith("INSTRUCTOR")
    assert len(manager.instructor_radios()) == 1
    assert manager.remove_instructor_radio(r["radio_id"]) is True
    assert manager.instructor_radios() == []


def test_aar_rerender_clean_and_dirty(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "2.000 MHz")  # low HF: heavy DSP
    manager.tune("t-2", "2.000 MHz")
    manager.ptt_start("t-1")
    event = manager.ptt_end("t-1", audio=tone(seconds=0.5))

    with manager.db.session() as s:
        row = repo.get_event(s, event["event_id"])
        clean, sr = render_event(row, manager.settings.recordings_dir, PlaybackMode.CLEAN)
        dirty, _ = render_event(
            row, manager.settings.recordings_dir, PlaybackMode.DIRTY, AarCryptoView.CYPHER
        )
    assert clean.size > 0
    assert dirty.size >= clean.size  # transients added
    assert not np.array_equal(clean, dirty[: clean.size])  # DSP changed it


def test_scenario_atmospheric_and_jamming(manager):
    manager.set_atmospheric(2.0)
    assert manager.band_profile.atmospheric_multiplier == 2.0
    manager.toggle_jamming(14_200_000, 14_300_000, on=True)
    assert manager.band_profile.conditions_at(14_250_000).jammed is True
    manager.toggle_jamming(14_200_000, 14_300_000, on=False)
    assert manager.band_profile.conditions_at(14_250_000).jammed is False


def test_kick_removes_terminal(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    assert manager.kick("t-1") is True
    assert manager.registry.get("t-1") is None


def test_monitor_snapshot_shows_freq_and_mode(manager):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.tune("t-1", "145.500 MHz")
    manager.set_mode("t-1", RadioMode.CYPHER)
    snap = manager.monitor_snapshot()
    me = next(t for t in snap if t["radio_id"] == "t-1")
    assert me["mode"] == "Cypher"
    assert "145.500" in me["frequency"]
    assert me["band_region"] == "VHF"

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


def test_running_session_resumes_after_restart(database, settings):
    """A scenario running when the server goes down (e.g. an update restart)
    must come back active, not silently ended — otherwise the ambient hash
    stops and trainees are dropped from the live session."""
    first = SessionManager(database, settings)
    started = first.start_session("EX1")

    # A fresh manager on the same DB models the process restarting.
    resumed = SessionManager(database, settings)
    assert resumed.session_active
    assert resumed.current_session_id == started["id"]
    # The name must come back too, so the console box isn't blank after a restart.
    assert resumed.current_session_name == started["name"]


def test_ended_session_does_not_resume_after_restart(database, settings):
    first = SessionManager(database, settings)
    first.start_session("EX1")
    first.end_session()

    resumed = SessionManager(database, settings)
    assert not resumed.session_active
    assert resumed.current_session_id is None


def test_login_creates_radio_on_default_freq(manager):
    info = manager.login("ALPHA", "t-1")
    assert info["mode"] == "Plain"
    assert info["frequency_hz"] == 7_000_000.0  # default start frequency
    assert manager.registry.get("t-1") is not None


def test_default_start_frequency_config_drives_login_and_instructor_radio(database, settings):
    """The operator-configured default_frequency_hz is the power-on frequency
    for both trainee logins and newly added instructor radios."""
    from pivot.db.config_store import ConfigStore

    with database.session() as s:
        ConfigStore(s).set("default_frequency_hz", 14_250_000.0)

    manager = SessionManager(database, settings)
    info = manager.login("ALPHA", "t-1")
    assert info["frequency_hz"] == 14_250_000.0

    radio = manager.add_instructor_radio()  # no explicit frequency
    assert radio["frequency_hz"] == 14_250_000.0
    # An explicit frequency still wins over the configured default.
    explicit = manager.add_instructor_radio("Radio X", "40.000 MHz")
    assert explicit["frequency_hz"] == 40_000_000.0


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
    assert event["band_region"] == "HF"
    assert event["duration_ms"] > 0
    # Recording exists on disk.
    path = manager.settings.recordings_dir / event["audio_path"]
    assert path.exists()


def test_tune_snaps_to_12_5khz_channel(manager):
    manager.login("ALPHA", "t-1")
    r = manager.tune("t-1", "145.513 MHz")  # off-grid -> nearest 12.5 kHz channel
    assert r["frequency_hz"] % 12_500 == 0
    assert r["frequency"] == "145.5125 MHz"


def test_event_without_audio_is_skipped_not_pending(manager):
    # No captured audio -> transcription is terminal (Skipped), not stuck Pending.
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.ptt_start("t-1")
    event = manager.ptt_end("t-1")  # no audio argument
    assert event["transcription_status"] == "Skipped"


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
    manager.tune("t-1", "7.050 MHz")  # on the 12.5 kHz raster
    manager.disconnect("t-1")
    # Reconnect: same trainee_id, mode + frequency restored (§3.4.4, §8.3).
    info = manager.login("ALPHA", "t-1")
    assert info["mode"] == RadioMode.CYPHER.value
    assert "7.050" in info["frequency"]


def test_stale_disconnect_keeps_reconnected_radio(manager):
    """A reconnect bumps the epoch; the stale connection's late teardown must
    not remove the freshly re-logged-in radio (the no-hash-after-restart bug)."""
    manager.start_session("EX")
    first = manager.login("ALPHA", "t-1")
    # Browser restarts: new connection logs in again under the same id.
    second = manager.login("ALPHA", "t-1")
    assert second["epoch"] > first["epoch"]
    # The stale connection now tears down with its *old* epoch.
    manager.disconnect("t-1", epoch=first["epoch"])
    # The live radio (and terminal) for the new connection survives.
    assert manager.registry.get("t-1") is not None
    assert "t-1" in manager.terminals


def test_stale_unregister_keeps_reconnected_sink(manager):
    """A reconnect re-registers a new sink for the same radio; the stale
    connection's teardown must not clobber it, or the new session goes silent
    (no live audio, no ambient hash)."""
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    old_frames, new_frames = [], []
    old_sink = old_frames.append
    new_sink = new_frames.append
    manager.register_audio_sink("t-1", old_sink)
    # Reconnect rebinds the radio to the new connection's sink.
    manager.register_audio_sink("t-1", new_sink)
    # Stale teardown passes its own (old) sink — must be a no-op now.
    manager.unregister_audio_sink("t-1", old_sink)
    assert manager._audio_sinks.get("t-1") is not None
    # And it really is the *new* sink that survives.
    manager._audio_sinks["t-1"](b"x")
    assert new_frames == [b"x"] and old_frames == []


def test_route_tx_frame_records_and_renders_to_listeners(manager):
    manager.start_session("EX")
    manager.login("TX", "tx")
    manager.login("RX", "rx")
    manager.tune("tx", "14.250 MHz")
    manager.tune("rx", "14.250 MHz")

    rx_frames, tx_frames = [], []
    manager.register_audio_sink("rx", rx_frames.append)
    manager.register_audio_sink("tx", tx_frames.append)  # transmitter is half-duplex

    manager.ptt_start("tx")  # plain -> on air immediately
    frame = (0.3 * np.sin(2 * np.pi * 440 * np.arange(320) / 16000)).astype(np.float32)
    manager.route_tx_frame("tx", frame)

    # Listener received a rendered PCM frame; the transmitter heard nothing.
    assert rx_frames and isinstance(rx_frames[0], (bytes, bytearray))
    assert tx_frames == []

    # The frame was tapped for the recording; on release the event has audio.
    event = manager.ptt_end("tx")
    assert event["duration_ms"] > 0
    assert (manager.settings.recordings_dir / event["audio_path"]).exists()


def test_route_tx_frame_ignored_when_not_on_air(manager):
    manager.start_session("EX")
    manager.login("TX", "tx")
    manager.tune("tx", "14.250 MHz")
    got = []
    manager.register_audio_sink("tx", got.append)
    # Not keyed -> frame is ignored (no recording, no render).
    frame = np.zeros(320, dtype=np.float32)
    manager.route_tx_frame("tx", frame)
    assert got == []


def test_route_tx_frame_records_crypto_sync_lead_in(manager):
    """Cypher lead-in: mic frames arriving before the station is on air (during
    the crypto-sync delay) are still tapped for the recording, so the start of
    the transmission is not clipped off the WAV (§3.5.1). Nothing is rendered to
    listeners until on air, though."""
    manager.start_session("EX")
    manager.login("TX", "tx")
    manager.login("RX", "rx")
    manager.tune("tx", "14.250 MHz")
    manager.tune("rx", "14.250 MHz")
    manager.set_mode("tx", RadioMode.CYPHER)
    rx_frames = []
    manager.register_audio_sink("rx", rx_frames.append)

    start = manager.ptt_start("tx")
    assert start["sync_applies"] is True
    assert manager.registry.get("tx").on_air is False  # still in crypto sync

    frame = (0.3 * np.sin(2 * np.pi * 440 * np.arange(320) / 16000)).astype(np.float32)
    manager.route_tx_frame("tx", frame)          # arrives DURING sync (not on air)
    assert rx_frames == []                        # nothing rendered while syncing
    manager.ptt_sync_complete("tx")
    manager.route_tx_frame("tx", frame)          # arrives once on air

    event = manager.ptt_end("tx")                # no explicit audio -> uses the tap
    # Both 20 ms frames captured (sync lead-in + on-air), not just the on-air one.
    assert event["duration_ms"] >= 39


def test_instructor_radio_lifecycle(manager):
    r = manager.add_instructor_radio("Radio 1", "40.000 MHz")
    assert r["is_instructor"] is True
    assert r["name"].startswith("INSTRUCTOR")
    assert len(manager.instructor_radios()) == 1
    assert manager.remove_instructor_radio(r["radio_id"]) is True
    assert manager.instructor_radios() == []


def test_remove_instructor_radio_renumbers_default_labels(manager):
    """Removing a radio re-fits default "Radio N" labels to list position, so
    the console's card numbers (1, 2, 3…) and the names stay in agreement.
    Custom labels are left alone."""
    r1 = manager.add_instructor_radio()
    manager.add_instructor_radio()
    manager.add_instructor_radio("EAGLE EYE")
    assert [r["name"] for r in manager.instructor_radios()] == [
        "INSTRUCTOR (Radio 1)", "INSTRUCTOR (Radio 2)", "INSTRUCTOR (EAGLE EYE)"]

    manager.remove_instructor_radio(r1["radio_id"])
    assert [r["name"] for r in manager.instructor_radios()] == [
        "INSTRUCTOR (Radio 1)", "INSTRUCTOR (EAGLE EYE)"]
    # The next default label continues from the list position, no duplicates.
    added = manager.add_instructor_radio()
    assert added["name"] == "INSTRUCTOR (Radio 3)"

    # The renumbering reached the DB too, so it survives a restart.
    resumed = SessionManager(manager.db, manager.settings)
    assert [r["name"] for r in resumed.instructor_radios()] == [
        "INSTRUCTOR (Radio 1)", "INSTRUCTOR (EAGLE EYE)", "INSTRUCTOR (Radio 3)"]


def test_instructor_radio_watcher_fires_and_removal_detaches_sink(manager):
    """Radios can be added/removed over REST, outside the instructor WS loop:
    the change watcher lets the live socket bind a sink for a new radio, and a
    removal detaches the radio's sink so it doesn't linger registered."""
    fired = []
    unwatch = manager.watch_instructor_radios(lambda: fired.append(True))

    r = manager.add_instructor_radio()
    assert fired, "watcher must fire on add"

    manager.register_audio_sink(r["radio_id"], lambda data: None)
    manager.remove_instructor_radio(r["radio_id"])
    assert r["radio_id"] not in manager._audio_sinks

    unwatch()
    fired.clear()
    manager.add_instructor_radio()
    assert fired == []


def test_rx_noise_toggle_is_instructor_only(manager):
    r = manager.add_instructor_radio("Radio 1", "14.250 MHz")
    assert r["rx_noise"] is True
    assert manager.set_rx_noise(r["radio_id"], False)["rx_noise"] is False
    assert manager.instructor_radios()[0]["rx_noise"] is False

    manager.login("ALPHA", "t-1")
    with pytest.raises(KeyError):
        manager.set_rx_noise("t-1", False)


def test_rx_noise_off_hears_through_jamming_others_unaffected(manager):
    """An instructor radio with its receive-noise toggle off hears the voice
    clean even on a jammed channel, while a trainee on the same net still gets
    the wall of jammer noise — the toggle changes only that radio's receive."""
    from pivot.audio.pcm import pcm16_to_float32

    manager.start_session("EX")
    manager.login("TX", "tx")
    manager.login("RX", "rx")
    manager.tune("tx", "14.250 MHz")
    manager.tune("rx", "14.250 MHz")
    instr = manager.add_instructor_radio("Radio 1", "14.250 MHz")
    manager.set_net_scenario("14.250 MHz", jammed=True)
    manager.set_rx_noise(instr["radio_id"], False)

    rx_frames, instr_frames = [], []
    manager.register_audio_sink("rx", rx_frames.append)
    manager.register_audio_sink(instr["radio_id"], instr_frames.append)

    manager.ptt_start("tx")
    voice = tone(seconds=0.02)  # one 20 ms frame
    manager.route_tx_frame("tx", voice)
    assert rx_frames and instr_frames

    def similarity(data: bytes) -> float:
        return abs(float(np.corrcoef(pcm16_to_float32(data), voice)[0, 1]))

    # The toggled radio's frame is essentially the voice; the trainee's copy is
    # buried in jammer noise (and the two renders are genuinely distinct).
    assert instr_frames[0] != rx_frames[0]
    assert similarity(instr_frames[0]) > 0.9
    assert similarity(rx_frames[0]) < 0.7


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


def test_scenario_jamming(manager):
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

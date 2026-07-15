"""Tests for the data layer (spec §5, §9.3)."""

import pytest

from pivot.core.bands import BandProfile, JammingSpan
from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.db.migrations import CURRENT_SCHEMA_VERSION, crosses_migration_boundary
from pivot.db.models import ConfigRow, EventRow, TranscriptionStatus


def test_initialise_seeds_config_and_band_profile(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        assert cfg.get("schema_version") == CURRENT_SCHEMA_VERSION
        assert cfg.get("whisper_model") == "small"
        assert cfg.display_timezone() == "UTC"
        profile = repo.load_band_profile(s)
        assert isinstance(profile, BandProfile)


def test_config_store_roundtrip(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("display_timezone", "America/New_York")
        cfg.set("crypto_delay_ms", 2000)
    with database.session() as s:
        cfg = ConfigStore(s)
        assert cfg.display_timezone() == "America/New_York"
        assert cfg.crypto_delay_ms() == 2000


def test_config_store_all_overlays_defaults(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("whisper_model", "large-v3")
        allcfg = cfg.all()
        assert allcfg["whisper_model"] == "large-v3"
        assert allcfg["update_channel"] == "stable"  # default still present


def test_band_profile_persistence(database):
    with database.session() as s:
        profile = BandProfile(jamming=[JammingSpan(14e6, 14.1e6)])
        repo.save_band_profile(s, profile)
    with database.session() as s:
        loaded = repo.load_band_profile(s)
        # Jamming is live state, not persisted on the row; curve + globals are.
        # Compare at 7 MHz, outside the jam span, where both must agree.
        assert loaded.conditions_at(7e6).snr_db == pytest.approx(
            profile.conditions_at(7e6).snr_db, abs=0.5
        )


def test_session_and_event_lifecycle(database):
    with database.session() as s:
        repo.upsert_trainee(s, "t-1", "ALPHA")
        sess = repo.start_session(s, "Exercise BRAVO")
        session_id = sess.id

    with database.session() as s:
        _ = repo.create_event(
            s,
            session_id=session_id,
            trainee_name="ALPHA",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.CYPHER,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=3200,
            audio_path=f"{session_id}/evt.wav",
            dsp_profile={"snr_db": 16.0, "region": "HF"},
        )

    with database.session() as s:
        events = repo.list_events(s, session_id)
        assert len(events) == 1
        e = events[0]
        assert e.tx_mode is RadioMode.CYPHER
        assert e.audibility is Audibility.HEARD
        assert e.transcription_status is TranscriptionStatus.PENDING
        d = e.to_dict()
        assert d["tx_mode"] == "Plain" or d["tx_mode"] == "Cypher"
        assert d["audibility"] == "Heard"


def test_set_transcription(database):
    with database.session() as s:
        sess = repo.start_session(s, "S")
        ev = repo.create_event(
            s,
            session_id=sess.id,
            trainee_name="ALPHA",
            frequency="145.500 MHz",
            band_region="VHF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=1500,
            audio_path="x.wav",
            dsp_profile={},
        )
        eid = ev.event_id
    with database.session() as s:
        repo.set_transcription(
            s, eid, text_value="SITREP FOLLOWS", confidence=0.91, status=TranscriptionStatus.DONE
        )
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "SITREP FOLLOWS"
        assert e.transcription_confidence == pytest.approx(0.91)
        assert e.transcription_status is TranscriptionStatus.DONE


def _make_event(s, **overrides):
    """Insert a minimal event and return its id (test helper)."""
    sess = repo.start_session(s, "S")
    kwargs = dict(
        session_id=sess.id,
        trainee_name="ALPHA",
        frequency="145.500 MHz",
        band_region="VHF",
        tx_mode=RadioMode.PLAIN,
        audibility=Audibility.HEARD,
        sync_status=SyncStatus.COMPLETED,
        timestamp_start="2026-06-05T12:00:00+00:00",
        duration_ms=1500,
        audio_path="x.wav",
        dsp_profile={},
    )
    kwargs.update(overrides)
    return repo.create_event(s, **kwargs).event_id


def test_edit_transcription_preserves_machine_text(database):
    """A manual edit keeps the machine transcription for diffing and flags the row."""
    with database.session() as s:
        eid = _make_event(s)
        repo.set_transcription(
            s, eid, text_value="SITREP FOLOWS", confidence=0.42, status=TranscriptionStatus.DONE
        )
    with database.session() as s:
        row = repo.edit_transcription(s, eid, "SITREP FOLLOWS OVER")
        assert row is not None
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "SITREP FOLLOWS OVER"
        assert e.transcription_original == "SITREP FOLOWS"  # machine text preserved
        assert bool(e.transcription_edited) is True
        assert e.transcription_status is TranscriptionStatus.DONE
        d = e.to_dict()
        assert d["transcription_original"] == "SITREP FOLOWS"
        assert d["transcription_edited"] is True


def test_edit_transcription_reedit_keeps_original(database):
    """Editing twice keeps the *machine* text as the diff baseline, not the last edit."""
    with database.session() as s:
        eid = _make_event(s)
        repo.set_transcription(
            s, eid, text_value="MACHINE", confidence=0.5, status=TranscriptionStatus.DONE
        )
    with database.session() as s:
        repo.edit_transcription(s, eid, "FIRST EDIT")
    with database.session() as s:
        repo.edit_transcription(s, eid, "SECOND EDIT")
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "SECOND EDIT"
        assert e.transcription_original == "MACHINE"


def test_edit_transcription_revert_clears_flag(database):
    """Restoring the machine text drops the edited marker (a full revert)."""
    with database.session() as s:
        eid = _make_event(s)
        repo.set_transcription(
            s, eid, text_value="MACHINE", confidence=0.5, status=TranscriptionStatus.DONE
        )
    with database.session() as s:
        repo.edit_transcription(s, eid, "CHANGED")
    with database.session() as s:
        repo.edit_transcription(s, eid, "MACHINE")
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "MACHINE"
        assert e.transcription_original is None
        assert bool(e.transcription_edited) is False


def test_edit_transcription_on_skipped_event(database):
    """The instructor can type a transcript for an event the machine skipped."""
    with database.session() as s:
        eid = _make_event(s)
        repo.set_transcription(
            s, eid, text_value=None, confidence=None, status=TranscriptionStatus.SKIPPED
        )
    with database.session() as s:
        repo.edit_transcription(s, eid, "MANUAL ENTRY")
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "MANUAL ENTRY"
        assert e.transcription_original is None  # nothing machine-made to preserve
        assert bool(e.transcription_edited) is True
        assert e.transcription_status is TranscriptionStatus.DONE


def test_set_transcription_never_clobbers_a_manual_edit(database):
    """A late machine transcription must not overwrite a hand-corrected row."""
    with database.session() as s:
        eid = _make_event(s)
    with database.session() as s:
        repo.edit_transcription(s, eid, "HUMAN TRUTH")
    with database.session() as s:
        # Simulate the worker landing after the edit.
        repo.set_transcription(
            s, eid, text_value="ROBOT GUESS", confidence=0.9, status=TranscriptionStatus.DONE
        )
    with database.session() as s:
        e = repo.get_event(s, eid)
        assert e.transcription == "HUMAN TRUTH"
        assert bool(e.transcription_edited) is True


def test_edit_transcription_unknown_event_returns_none(database):
    with database.session() as s:
        assert repo.edit_transcription(s, "no-such-id", "x") is None


def test_delete_session_cascades_events(database):
    with database.session() as s:
        sess = repo.start_session(s, "S")
        sid = sess.id
        repo.create_event(
            s,
            session_id=sid,
            trainee_name="A",
            frequency="7.1 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.UNHEARD_NO_LISTENERS,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=900,
            audio_path="y.wav",
            dsp_profile={},
        )
    with database.session() as s:
        assert repo.delete_session(s, sid) is True
    with database.session() as s:
        assert s.query(EventRow).count() == 0


def test_instructor_radio_crud(database):
    with database.session() as s:
        r = repo.add_instructor_radio(s, "Radio 1", "30.000 MHz")
        rid = r.id
    with database.session() as s:
        radios = repo.list_instructor_radios(s)
        assert len(radios) == 1 and radios[0].label == "Radio 1"
    with database.session() as s:
        assert repo.remove_instructor_radio(s, rid) is True
    with database.session() as s:
        assert repo.list_instructor_radios(s) == []


def test_reconcile_orphan_transcriptions(database, settings):
    with database.session() as s:
        sess = repo.start_session(s, "S")
        ev = repo.create_event(
            s,
            session_id=sess.id,
            trainee_name="A",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=0,
            audio_path="missing/none.wav",  # no file on disk -> Pending orphan
            dsp_profile={},
        )
        eid = ev.event_id
    with database.session() as s:
        assert repo.reconcile_orphan_transcriptions(s, settings.recordings_dir) == 1
    with database.session() as s:
        assert repo.get_event(s, eid).transcription_status is TranscriptionStatus.SKIPPED


def test_migration_boundary_check():
    assert crosses_migration_boundary(2, 1) is True
    assert crosses_migration_boundary(1, 1) is False
    assert crosses_migration_boundary(1, 2) is False


def test_v3_migration_adds_transcript_edit_columns_idempotently():
    """The v3 step adds the manual-edit columns to a pre-existing events table
    and is safe to run twice (§3.5.3)."""
    from sqlalchemy import create_engine, text

    from pivot.db.migrations import _migrate_v3_transcript_edits

    engine = create_engine("sqlite://")  # in-memory
    with engine.begin() as conn:
        # An "old" events table without the manual-edit columns.
        conn.execute(text("CREATE TABLE events (event_id TEXT PRIMARY KEY, transcription TEXT)"))
        _migrate_v3_transcript_edits(conn)
        _migrate_v3_transcript_edits(conn)  # idempotent — second run is a no-op
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(events)"))}
        assert "transcription_original" in cols
        assert "transcription_edited" in cols


def test_list_sessions_with_event_count(database):
    """Ensure session listing handles 0 events properly and returns correct counts."""
    with database.session() as s:
        # Session with 0 events
        empty_session = repo.start_session(s, "Empty Session")

        # Session with 2 events
        populated_session = repo.start_session(s, "Populated Session")
        repo.create_event(
            s,
            session_id=populated_session.id,
            trainee_name="Trainee 1",
            frequency="14.0",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2023-01-01T12:00:00Z",
            duration_ms=1000,
            audio_path="",
            dsp_profile={},
        )
        repo.create_event(
            s,
            session_id=populated_session.id,
            trainee_name="Trainee 2",
            frequency="14.0",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2023-01-01T12:00:00Z",
            duration_ms=1000,
            audio_path="",
            dsp_profile={},
        )
        s.commit()

    with database.session() as s:
        results = repo.list_sessions_with_event_count(s)

        # Ensure we have both sessions returned
        assert len(results) >= 2

        # Build dictionary for easier assertions
        counts_by_id = {row.id: count for row, count in results}

        assert empty_session.id in counts_by_id
        assert counts_by_id[empty_session.id] == 0

        assert populated_session.id in counts_by_id
        assert counts_by_id[populated_session.id] == 2

def test_config_store_all_ignores_invalid_json(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        # Direct insert of bad JSON
        s.add(ConfigRow(key="bad_key", value="{bad json"))
        s.commit()

        allcfg = cfg.all()
        assert "bad_key" not in allcfg

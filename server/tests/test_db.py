"""Tests for the data layer (spec §5, §9.3)."""

import pytest

from pivot.core.bands import BandProfile, JammingSpan
from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.db.config_store import ConfigStore
from pivot.db.migrations import CURRENT_SCHEMA_VERSION, crosses_migration_boundary
from pivot.db.models import EventRow, TranscriptionStatus
from pivot.db import repository as repo


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
        profile = BandProfile(atmospheric_multiplier=1.7, jamming=[JammingSpan(14e6, 14.1e6)])
        repo.save_band_profile(s, profile)
    with database.session() as s:
        loaded = repo.load_band_profile(s)
        assert loaded.atmospheric_multiplier == pytest.approx(1.7)
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
        ev = repo.create_event(
            s,
            session_id=session_id,
            trainee_name="ALPHA",
            frequency="14.250 MHz",
            band_region="High HF",
            tx_mode=RadioMode.CYPHER,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=3200,
            audio_path=f"{session_id}/evt.wav",
            dsp_profile={"snr_db": 16.0, "region": "High HF"},
        )
        event_id = ev.event_id

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


def test_delete_session_cascades_events(database):
    with database.session() as s:
        sess = repo.start_session(s, "S")
        sid = sess.id
        repo.create_event(
            s,
            session_id=sid,
            trainee_name="A",
            frequency="7.1 MHz",
            band_region="Low HF",
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
            band_region="High HF",
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

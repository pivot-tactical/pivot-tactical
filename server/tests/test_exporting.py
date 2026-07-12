import csv
import io
import zipfile
from pathlib import Path

from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.db.models import TranscriptionStatus
from pivot.exporting import export_csv, export_text, export_zip


def test_export_text(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("display_timezone", "UTC")
        sess = repo.start_session(s, "Test Session")
        sid = sess.id
        event = repo.create_event(
            s,
            session_id=sid,
            trainee_name="T-1",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=1000,
            audio_path="test.wav",
            dsp_profile={},
        )
        repo.set_transcription(
            s,
            event.event_id,
            text_value="Hello World",
            confidence=1.0,
            status=TranscriptionStatus.DONE,
        )

    text = export_text(database, sid)
    assert "PIVOT session transcript — Test Session" in text
    assert "Display timezone: UTC" in text
    assert "[12:00:00] T-1 14.250 MHz (HF, PLAIN, Heard): Hello World" in text


def test_export_marks_edited_transcripts(database):
    """A hand-corrected transcript is flagged in both text and CSV exports."""
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("display_timezone", "UTC")
        sess = repo.start_session(s, "Test Session")
        sid = sess.id
        event = repo.create_event(
            s,
            session_id=sid,
            trainee_name="T-1",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=1000,
            audio_path="test.wav",
            dsp_profile={},
        )
        repo.set_transcription(
            s, event.event_id, text_value="helo wrld", confidence=0.4,
            status=TranscriptionStatus.DONE,
        )
        repo.edit_transcription(s, event.event_id, "Hello World")

    text = export_text(database, sid)
    assert "Hello World [edited]" in text

    reader = csv.DictReader(io.StringIO(export_csv(database, sid)))
    row = next(iter(reader))
    assert row["transcription"] == "Hello World"
    assert row["transcription_original"] == "helo wrld"
    assert row["transcription_edited"] == "True"


def test_export_csv(database):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("display_timezone", "UTC")
        sess = repo.start_session(s, "Test Session")
        sid = sess.id
        event = repo.create_event(
            s,
            session_id=sid,
            trainee_name="T-1",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=1000,
            audio_path="test.wav",
            dsp_profile={},
        )
        repo.set_transcription(
            s,
            event.event_id,
            text_value="Hello World",
            confidence=1.0,
            status=TranscriptionStatus.DONE,
        )

    csv_data = export_csv(database, sid)
    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["trainee_name"] == "T-1"
    assert row["frequency"] == "14.250 MHz"
    assert row["transcription"] == "Hello World"


def test_export_zip(database, settings, tmp_path):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("display_timezone", "UTC")
        sess = repo.start_session(s, "Test Session")
        sid = sess.id
        event = repo.create_event(
            s,
            session_id=sid,
            trainee_name="T-1",
            frequency="14.250 MHz",
            band_region="HF",
            tx_mode=RadioMode.PLAIN,
            audibility=Audibility.HEARD,
            sync_status=SyncStatus.COMPLETED,
            timestamp_start="2026-06-05T12:00:00+00:00",
            duration_ms=1000,
            audio_path=f"{sid}/test.wav",
            dsp_profile={},
        )
        repo.set_transcription(
            s,
            event.event_id,
            text_value="Hello World",
            confidence=1.0,
            status=TranscriptionStatus.DONE,
        )

    # Create dummy wav file
    rec_dir = Path(settings.recordings_dir) / sid
    rec_dir.mkdir(parents=True, exist_ok=True)
    wav_path = rec_dir / "test.wav"
    wav_path.write_bytes(b"dummy wav data")

    zip_bytes = export_zip(database, settings, sid)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert f"{sid}/transcript.txt" in names
        assert f"{sid}/events.csv" in names
        assert f"{sid}/recordings/test.wav" in names

        wav_data = zf.read(f"{sid}/recordings/test.wav")
        assert wav_data == b"dummy wav data"

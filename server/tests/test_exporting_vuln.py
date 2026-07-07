import io
import zipfile
from pathlib import Path

from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.exporting import export_zip

def test_export_zip_path_traversal(database, settings, tmp_path):
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
            audio_path="../secret.txt",
            dsp_profile={},
        )

    rec_dir = Path(settings.recordings_dir)
    rec_dir.mkdir(parents=True, exist_ok=True)

    secret_path = rec_dir.parent / "secret.txt"
    secret_path.write_bytes(b"secret data")

    zip_bytes = export_zip(database, settings, sid)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert f"{sid}/recordings/secret.txt" not in names

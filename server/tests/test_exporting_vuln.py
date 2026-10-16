import io
import zipfile
from pathlib import Path

from pivot.audio.recording import session_dir_name
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
        started_at = sess.started_at
        for idx, path in enumerate(["../secret.txt", "sub/../../secret.txt", "/etc/passwd"]):
            repo.create_event(
                s,
                session_id=sid,
                trainee_name=f"T-{idx}",
                frequency="14.250 MHz",
                band_region="HF",
                tx_mode=RadioMode.PLAIN,
                audibility=Audibility.HEARD,
                sync_status=SyncStatus.COMPLETED,
                timestamp_start="2026-06-05T12:00:00+00:00",
                duration_ms=1000,
                audio_path=path,
                dsp_profile={},
            )

    rec_dir = Path(settings.recordings_dir)
    rec_dir.mkdir(parents=True, exist_ok=True)

    secret_path = rec_dir.parent / "secret.txt"
    secret_path.write_bytes(b"secret data")

    zip_bytes = export_zip(database, settings, sid)

    root = session_dir_name("Test Session", started_at)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert f"{root}/recordings/secret.txt" not in names
        assert not any("secret.txt" in name or "passwd" in name for name in names)

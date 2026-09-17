import pytest
from fastapi import HTTPException
from pivot.db import repository as repo
from pivot.api.rest import event_audio
from pivot.audio.render import AarCryptoView, PlaybackMode
from pivot.core.crypto import RadioMode, Audibility, SyncStatus

class MockSettings:
    def __init__(self, tmp_path):
        self.recordings_dir = tmp_path

class MockManager:
    def __init__(self, db, tmp_path):
        self.db = db
        self.settings = MockSettings(tmp_path)

def test_event_audio_path_traversal(database, settings, tmp_path):
    with database.session() as s:
        sess = repo.start_session(s, "Test Session")
        sid = sess.id

        ev = repo.create_event(
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
            audio_path="/etc/passwd",
            dsp_profile={},
        )
        s.commit()
        ev_id = ev.event_id

    mgr = MockManager(database, tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        event_audio(ev_id, PlaybackMode.CLEAN, AarCryptoView.PLAIN, mgr)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid audio path"

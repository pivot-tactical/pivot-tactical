"""`recording_path` refuses anything that does not land inside the tree (§3.5.3)."""

import pytest

from pivot.audio.recording import UnsafeRecordingPath, recording_path


def test_normal_relative_path_resolves_inside_the_tree(tmp_path):
    wav = tmp_path / "sess_2026-07-11_14-00-00Z" / "clip.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"")
    got = recording_path(tmp_path, "sess_2026-07-11_14-00-00Z/clip.wav")
    assert got == wav.resolve()
    assert got.exists()


def test_a_missing_but_well_formed_path_still_resolves(tmp_path):
    # Callers decide what a missing file means; resolving it is not an error.
    got = recording_path(tmp_path, "sess/none.wav")
    assert got == (tmp_path / "sess" / "none.wav").resolve()
    assert not got.exists()


@pytest.mark.parametrize(
    "audio_path",
    [
        "/etc/passwd",  # absolute: pathlib would discard the base
        "../../etc/passwd",  # walks up out of the tree
        "sess/../../../etc/passwd",  # walks up after a valid-looking first part
        "..",
    ],
)
def test_paths_that_escape_the_tree_are_refused(tmp_path, audio_path):
    with pytest.raises(UnsafeRecordingPath):
        recording_path(tmp_path, audio_path)


def test_absolute_path_would_otherwise_escape_via_pathlib(tmp_path):
    # The bug this guards: `base / "/etc/passwd"` is "/etc/passwd", not a child.
    from pathlib import Path

    assert Path(tmp_path) / "/etc/passwd" == Path("/etc/passwd")
    with pytest.raises(UnsafeRecordingPath):
        recording_path(tmp_path, "/etc/passwd")


def test_symlink_out_of_the_tree_is_refused(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.wav").write_bytes(b"")
    base = tmp_path / "recordings"
    base.mkdir()
    (base / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeRecordingPath):
        recording_path(base, "escape/secret.wav")


def test_symlink_inside_the_tree_is_allowed(tmp_path):
    base = tmp_path / "recordings"
    real = base / "real"
    real.mkdir(parents=True)
    (real / "clip.wav").write_bytes(b"")
    (base / "alias").symlink_to(real, target_is_directory=True)
    assert recording_path(base, "alias/clip.wav") == (real / "clip.wav").resolve()


def test_orphan_reconcile_skips_a_row_whose_path_escapes(database, tmp_path):
    """An `audio_path` pointing outside the tree is orphaned, not "still pending".

    `(recordings_dir / audio_path)` follows an absolute value straight out of
    the tree, so a row naming a file that happens to exist on the host (say
    `/etc/passwd`) looked like a healthy recording and sat in `Pending`
    forever. It is refused now, which makes it orphaned.
    """
    from pivot.core.crypto import Audibility, RadioMode, SyncStatus
    from pivot.db import repository as repo
    from pivot.db.models import TranscriptionStatus

    with database.session() as s:
        sess = repo.start_session(s, "Reconcile")
        repo.create_event(
            s,
            session_id=sess.id,
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
            transcription_status=TranscriptionStatus.PENDING,
        )
        s.commit()

        changed = repo.reconcile_orphan_transcriptions(s, tmp_path)
        assert changed == 1
        rows = repo.list_recent_events(s)
        assert rows[0].transcription_status is TranscriptionStatus.SKIPPED

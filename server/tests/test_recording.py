from pathlib import Path

import numpy as np
import soundfile as sf

from pivot.audio.recording import (
    duration_ms,
    read_recording,
    relative_audio_path,
    session_dir_name,
    write_recording,
)


def test_duration_ms_empty():
    audio = np.array([])
    assert duration_ms(audio) == 0


def test_duration_ms_one_second():
    audio = np.zeros(16000)
    assert duration_ms(audio, sample_rate=16000) == 1000


def test_duration_ms_half_second():
    audio = np.zeros(8000)
    assert duration_ms(audio, sample_rate=16000) == 500


def test_duration_ms_custom_sample_rate():
    audio = np.zeros(44100)
    assert duration_ms(audio, sample_rate=44100) == 1000


def test_duration_ms_rounding():
    # 24 samples at 16000 Hz = 1.5 ms -> rounds to 2 ms
    audio = np.zeros(24)
    assert duration_ms(audio, sample_rate=16000) == 2

    # 8 samples at 16000 Hz = 0.5 ms -> rounds to 0 ms
    audio = np.zeros(8)
    assert duration_ms(audio, sample_rate=16000) == 0


EVENT_ID = "a1b2c3d4-e5f6-7890-abcd-ef0123456789"
SESSION_START = "2026-07-11T14:00:00+00:00"
EVENT_START = "2026-07-11T14:30:22.123456+00:00"


def test_session_dir_name_uses_name_and_dtg():
    assert session_dir_name("Night Nav Ex", SESSION_START) == "night-nav-ex_2026-07-11_14-00-00Z"


def test_relative_audio_path_is_human_readable():
    path = relative_audio_path(
        "Night Nav Ex", SESSION_START, EVENT_START, "John Smith", EVENT_ID
    )
    assert (
        path
        == "night-nav-ex_2026-07-11_14-00-00Z/2026-07-11_14-30-22Z_john-smith_a1b2c3d4.wav"
    )


def test_relative_audio_path_all_events_share_session_folder():
    """Two events in the same session land in one folder regardless of trainee."""
    a = relative_audio_path("Ex", SESSION_START, EVENT_START, "Alpha", EVENT_ID)
    b = relative_audio_path(
        "Ex", SESSION_START, "2026-07-11T14:31:00+00:00", "Bravo", "ffffffff-0000-4000-8000-000000000000"
    )
    assert a.split("/")[0] == b.split("/")[0]
    assert a != b


def test_relative_audio_path_dtg_sorts_chronologically():
    early = relative_audio_path("Ex", SESSION_START, "2026-07-11T14:30:00+00:00", "A", EVENT_ID)
    late = relative_audio_path("Ex", SESSION_START, "2026-07-11T14:45:00+00:00", "A", EVENT_ID)
    assert Path(early).name < Path(late).name


def test_naming_sanitizes_and_blocks_traversal():
    path = relative_audio_path("../../etc", "junk", "junk", "../../secret", EVENT_ID)
    assert ".." not in path
    assert path.count("/") == 1  # exactly the session-dir separator


def test_naming_falls_back_on_blank_fields():
    path = relative_audio_path("", SESSION_START, EVENT_START, "   ", EVENT_ID)
    assert path.startswith("session_")
    assert "_unknown_" in path


def test_read_recording_mono(tmp_path: Path):
    path = tmp_path / "test_mono.wav"
    audio = np.array([-0.5, 0.0, 0.5], dtype=np.float32)
    write_recording(path, audio, sample_rate=16000)

    data, sr = read_recording(path)
    assert sr == 16000
    assert data.ndim == 1
    assert data.dtype == np.float32
    np.testing.assert_allclose(data, audio, atol=1e-4)


def test_read_recording_stereo(tmp_path: Path):
    path = tmp_path / "test_stereo.wav"
    stereo_data = np.array([[0.0, 1.0], [0.5, 0.5], [-1.0, 1.0]], dtype=np.float32)
    sf.write(str(path), stereo_data, samplerate=44100, subtype="PCM_16")

    data, sr = read_recording(path)
    assert sr == 44100
    assert data.ndim == 1
    assert data.dtype == np.float32
    expected_mono = np.array([0.5, 0.5, 0.0], dtype=np.float32)
    np.testing.assert_allclose(data, expected_mono, atol=1e-4)

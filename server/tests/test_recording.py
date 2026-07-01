from pathlib import Path

import numpy as np
import soundfile as sf

from pivot.audio.recording import duration_ms, read_recording, write_recording


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

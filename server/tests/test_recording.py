import numpy as np

from pivot.audio.recording import duration_ms


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

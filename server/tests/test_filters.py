import numpy as np
from pivot.dsp.filters import normalise_rms, rms

def test_normalise_rms_zeros():
    x = np.zeros(100, dtype=np.float32)
    out = normalise_rms(x)
    assert np.array_equal(out, x)

def test_normalise_rms_sine_wave():
    t = np.linspace(0, 1, 44100, endpoint=False)
    x = np.sin(2 * np.pi * 440 * t)
    out = normalise_rms(x, target=0.5)
    assert np.isclose(rms(out), 0.5, rtol=1e-5)

def test_normalise_rms_noise():
    rng = np.random.default_rng(42)
    x = rng.standard_normal(44100)
    out = normalise_rms(x, target=2.0)
    assert np.isclose(rms(out), 2.0, rtol=1e-5)

def test_normalise_rms_very_quiet():
    x = np.ones(100, dtype=np.float32) * 1e-13
    out = normalise_rms(x, target=1.0)
    assert np.array_equal(out, x)

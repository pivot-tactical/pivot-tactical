import numpy as np
import pytest

from pivot.dsp.filters import (
    _cached_butter,
    bandpass,
    bandstop,
    highpass,
    lowpass,
    normalise_rms,
    rms,
    slow_random,
)

SR = 16000

@pytest.fixture(autouse=True)
def clear_filter_cache():
    _cached_butter.cache_clear()
    yield
    _cached_butter.cache_clear()

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

def test_highpass_attenuates_low_frequencies():
    t = np.arange(SR) / SR
    low_freq = np.sin(2 * np.pi * 100 * t)  # 100 Hz
    high_freq = np.sin(2 * np.pi * 5000 * t) # 5000 Hz
    signal = low_freq + high_freq

    filtered = highpass(signal, cutoff_hz=1000, sample_rate=SR)

    fft_original = np.abs(np.fft.rfft(signal))
    fft_filtered = np.abs(np.fft.rfft(filtered))

    orig_low = np.max(fft_original[90:110])
    orig_high = np.max(fft_original[4990:5010])

    filt_low = np.max(fft_filtered[90:110])
    filt_high = np.max(fft_filtered[4990:5010])

    assert filt_low < orig_low * 0.1, "Low frequency was not significantly attenuated"
    assert filt_high > orig_high * 0.9, "High frequency was significantly attenuated"

def test_lowpass_attenuates_high_frequencies():
    t = np.arange(SR) / SR
    low_freq = np.sin(2 * np.pi * 100 * t)  # 100 Hz
    high_freq = np.sin(2 * np.pi * 5000 * t) # 5000 Hz
    signal = low_freq + high_freq

    filtered = lowpass(signal, cutoff_hz=1000, sample_rate=SR)

    fft_original = np.abs(np.fft.rfft(signal))
    fft_filtered = np.abs(np.fft.rfft(filtered))

    orig_low = np.max(fft_original[90:110])
    orig_high = np.max(fft_original[4990:5010])

    filt_low = np.max(fft_filtered[90:110])
    filt_high = np.max(fft_filtered[4990:5010])

    assert filt_high < orig_high * 0.1, "High frequency was not significantly attenuated"
    assert filt_low > orig_low * 0.9, "Low frequency was significantly attenuated"

def test_bandstop_attenuates_mid_frequencies():
    t = np.arange(SR) / SR
    low_freq = np.sin(2 * np.pi * 100 * t)   # 100 Hz
    mid_freq = np.sin(2 * np.pi * 1000 * t)  # 1000 Hz
    high_freq = np.sin(2 * np.pi * 5000 * t) # 5000 Hz
    signal = low_freq + mid_freq + high_freq

    filtered = bandstop(signal, low_hz=800, high_hz=1200, sample_rate=SR)

    fft_original = np.abs(np.fft.rfft(signal))
    fft_filtered = np.abs(np.fft.rfft(filtered))

    orig_low = np.max(fft_original[90:110])
    orig_mid = np.max(fft_original[990:1010])
    orig_high = np.max(fft_original[4990:5010])

    filt_low = np.max(fft_filtered[90:110])
    filt_mid = np.max(fft_filtered[990:1010])
    filt_high = np.max(fft_filtered[4990:5010])

    assert filt_mid < orig_mid * 0.1, "Mid frequency was not significantly attenuated"
    assert filt_low > orig_low * 0.9, "Low frequency was significantly attenuated"
    assert filt_high > orig_high * 0.9, "High frequency was significantly attenuated"

def test_bandpass_attenuates_out_of_band_frequencies():
    t = np.arange(SR) / SR
    low_freq = np.sin(2 * np.pi * 100 * t)   # 100 Hz
    mid_freq = np.sin(2 * np.pi * 1000 * t)  # 1000 Hz
    high_freq = np.sin(2 * np.pi * 5000 * t) # 5000 Hz
    signal = low_freq + mid_freq + high_freq

    filtered = bandpass(signal, low_hz=800, high_hz=1200, sample_rate=SR)

    fft_original = np.abs(np.fft.rfft(signal))
    fft_filtered = np.abs(np.fft.rfft(filtered))

    orig_low = np.max(fft_original[90:110])
    orig_mid = np.max(fft_original[990:1010])
    orig_high = np.max(fft_original[4990:5010])

    filt_low = np.max(fft_filtered[90:110])
    filt_mid = np.max(fft_filtered[990:1010])
    filt_high = np.max(fft_filtered[4990:5010])

    assert filt_mid > orig_mid * 0.9, "Mid frequency was significantly attenuated"
    assert filt_low < orig_low * 0.1, "Low frequency was not significantly attenuated"
    assert filt_high < orig_high * 0.1, "High frequency was not significantly attenuated"

def test_filters_handle_empty_arrays():
    empty = np.array([], dtype=np.float32)
    assert highpass(empty, 1000, SR).size == 0
    assert lowpass(empty, 1000, SR).size == 0
    assert bandpass(empty, 800, 1200, SR).size == 0
    assert bandstop(empty, 800, 1200, SR).size == 0

def test_bandstop_short_input():
    # Test fallback to sosfilt for short arrays in _safe_filtfilt
    # _safe_filtfilt padlen default is 3 * (2 * sos.shape[0] + 1)
    # For order=2, sos.shape[0] should be 1, so padlen = 9
    # If len <= 9, it falls back to sosfilt. Let's use len=8.
    short_input = np.ones(8, dtype=np.float32)
    filtered = bandstop(short_input, low_hz=800, high_hz=1200, sample_rate=SR, order=2)
    assert filtered.shape == (8,)
    # Just checking it returns an array of the same shape without crashing

def test_bandstop_frequency_rejection():
    """Verify correct frequency rejection for bandstop (notch) filter."""
    t = np.arange(SR) / SR
    # Passband low, stopband, passband high
    f_pass1 = 500
    f_stop = 1500
    f_pass2 = 3000

    s_pass1 = np.sin(2 * np.pi * f_pass1 * t)
    s_stop = np.sin(2 * np.pi * f_stop * t)
    s_pass2 = np.sin(2 * np.pi * f_pass2 * t)

    signal = s_pass1 + s_stop + s_pass2

    # Notch out 1000 - 2000 Hz
    filtered = bandstop(signal, low_hz=1000, high_hz=2000, sample_rate=SR, order=4)

    fft_in = np.abs(np.fft.rfft(signal))
    fft_out = np.abs(np.fft.rfft(filtered))

    # Verify passband low is preserved (> 90%)
    assert fft_out[f_pass1] > fft_in[f_pass1] * 0.90

    # Verify stopband is attenuated (< 5%)
    assert fft_out[f_stop] < fft_in[f_stop] * 0.05

    # Verify passband high is preserved (> 90%)
    assert fft_out[f_pass2] > fft_in[f_pass2] * 0.90
def test_slow_random_empty():
    rng = np.random.default_rng(42)
    out = slow_random(0, 16000, 1.0, rng)
    assert out.size == 0
    assert out.dtype == np.float32

def test_slow_random_shape_and_type():
    rng = np.random.default_rng(42)
    out = slow_random(16000, 16000, 1.0, rng)
    assert out.shape == (16000,)
    assert out.dtype == np.float32

def test_slow_random_reproducibility():
    rng1 = np.random.default_rng(42)
    out1 = slow_random(16000, 16000, 1.0, rng1)

    rng2 = np.random.default_rng(42)
    out2 = slow_random(16000, 16000, 1.0, rng2)

    assert np.array_equal(out1, out2)

def test_slow_random_different_rates():
    rng1 = np.random.default_rng(42)
    fast = slow_random(16000, 16000, 10.0, rng1)

    rng2 = np.random.default_rng(42)
    slow = slow_random(16000, 16000, 0.1, rng2)

    fast_diff = np.mean(np.abs(np.diff(fast)))
    slow_diff = np.mean(np.abs(np.diff(slow)))
    assert fast_diff > slow_diff * 10

def test_slow_random_bounds():
    rng = np.random.default_rng(42)
    out = slow_random(16000, 16000, 1.0, rng)
    assert not np.allclose(out, 0)
    assert np.max(out) < 10.0
    assert np.min(out) > -10.0

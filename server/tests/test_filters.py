import numpy as np
import pytest
from pivot.dsp.filters import highpass, lowpass, bandstop, bandpass, _cached_butter

SR = 16000

@pytest.fixture(autouse=True)
def clear_filter_cache():
    _cached_butter.cache_clear()
    yield
    _cached_butter.cache_clear()

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

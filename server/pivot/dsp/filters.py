"""Shared, dependency-light filter helpers (numpy + scipy, both BSD).

Zero-phase filtering (``sosfiltfilt``) is used for offline/whole-buffer renders
(AAR re-render, tests). The real-time router can swap these for streaming
``sosfilt`` with retained state; the coefficient design is identical.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy import signal


@lru_cache(maxsize=128)
def _cached_butter(order: int, Wn: float | tuple[float, float], btype: str, output: str) -> np.ndarray:
    """Cache the relatively expensive Butterworth filter coefficient generation."""
    return signal.butter(order, Wn, btype=btype, output=output)


def _normalise_band(low_hz: float, high_hz: float, sample_rate: int) -> tuple[float, float]:
    nyq = sample_rate / 2.0
    low = max(1.0, min(low_hz, nyq - 1.0))
    high = max(low + 1.0, min(high_hz, nyq - 1.0))
    return low / nyq, high / nyq


def bandpass(x: np.ndarray, low_hz: float, high_hz: float, sample_rate: int, order: int = 4) -> np.ndarray:
    """Voice bandpass (≈300 Hz – 3 kHz), zero-phase (§4.1.1)."""
    if x.size == 0:
        return x
    wl, wh = _normalise_band(low_hz, high_hz, sample_rate)
    sos = _cached_butter(order, (wl, wh), btype="bandpass", output="sos")
    return _safe_filtfilt(sos, x)


def lowpass(x: np.ndarray, cutoff_hz: float, sample_rate: int, order: int = 4) -> np.ndarray:
    if x.size == 0:
        return x
    nyq = sample_rate / 2.0
    wc = max(1e-4, min(cutoff_hz / nyq, 0.999))
    sos = _cached_butter(order, wc, btype="low", output="sos")
    return _safe_filtfilt(sos, x)


def highpass(x: np.ndarray, cutoff_hz: float, sample_rate: int, order: int = 4) -> np.ndarray:
    if x.size == 0:
        return x
    nyq = sample_rate / 2.0
    wc = max(1e-4, min(cutoff_hz / nyq, 0.999))
    sos = _cached_butter(order, wc, btype="high", output="sos")
    return _safe_filtfilt(sos, x)


def bandstop(x: np.ndarray, low_hz: float, high_hz: float, sample_rate: int, order: int = 2) -> np.ndarray:
    """Notch used to approximate frequency-selective fading (§4.1.1)."""
    if x.size == 0:
        return x
    wl, wh = _normalise_band(low_hz, high_hz, sample_rate)
    sos = _cached_butter(order, (wl, wh), btype="bandstop", output="sos")
    return _safe_filtfilt(sos, x)


def _safe_filtfilt(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    """filtfilt requires a minimum length; fall back to one-pass for short input."""
    # padlen default is 3 * (2*len(sos)+1); guard short buffers.
    padlen = 3 * (2 * sos.shape[0] + 1)
    if x.size <= padlen:
        return signal.sosfilt(sos, x).astype(np.float32, copy=False)
    return signal.sosfiltfilt(sos, x).astype(np.float32, copy=False)


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def normalise_rms(x: np.ndarray, target: float = 1.0) -> np.ndarray:
    r = rms(x)
    if r < 1e-12:
        return x.astype(np.float32, copy=False)
    return (x * (target / r)).astype(np.float32, copy=False)


def soft_clip(x: np.ndarray) -> np.ndarray:
    """Tanh soft-limiter to keep renders inside [-1, 1] without harsh clipping."""
    return np.tanh(x).astype(np.float32, copy=False)


def slow_random(n: int, sample_rate: int, rate_hz: float, rng: np.random.Generator) -> np.ndarray:
    """A smooth, band-limited random process at ``rate_hz`` (control points
    interpolated). Stable for any length and very low rates — used for fading
    and other slow modulations where a low-cutoff IIR would be ill-conditioned.
    """
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    step = max(1, int(sample_rate / max(rate_hz, 1e-3)))
    n_ctrl = n // step + 2
    ctrl = rng.standard_normal(n_ctrl)
    x_ctrl = np.arange(n_ctrl) * step
    x = np.arange(n)
    return np.interp(x, x_ctrl, ctrl).astype(np.float32)

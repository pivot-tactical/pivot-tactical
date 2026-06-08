"""Frequency-interpolated fading model (spec §4.1.1).

Deep, slow fading at low HF easing to light flutter at VHF and essentially
nothing at UHF. ``fading_depth_db`` and ``fading_rate_hz`` come from the band
curve. Selective (frequency-dependent) fading is engaged only in the HF region
and is approximated by fading the low and high halves of the voice band with
decorrelated envelopes, producing a moving spectral notch.
"""

from __future__ import annotations

import numpy as np

from pivot.dsp.filters import highpass, lowpass, slow_random


def flat_fading_gain(
    n: int,
    sample_rate: int,
    depth_db: float,
    rate_hz: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """A smooth multiplicative gain envelope with ~``depth_db`` peak dips.

    Built from a band-limited Gaussian process (Rayleigh-inspired): normalised
    to unit standard deviation, scaled so ~3σ spans the requested depth, and
    clamped so it dips rather than boosts.
    """
    if n == 0 or depth_db <= 0.01:
        return np.ones(max(n, 0), dtype=np.float32)
    g = slow_random(n, sample_rate, max(rate_hz, 0.05), rng)
    std = float(np.std(g)) or 1.0
    g = g / std
    gain_db = g * (depth_db / 3.0)
    gain_db = np.clip(gain_db, -depth_db, 3.0)
    return (10.0 ** (gain_db / 20.0)).astype(np.float32)


def apply_fading(
    signal_in: np.ndarray,
    sample_rate: int,
    depth_db: float,
    rate_hz: float,
    selective: bool,
    rng: np.random.Generator,
    crossover_hz: float = 1200.0,
) -> np.ndarray:
    """Apply fading to a voice buffer.

    For non-selective (VHF/UHF) fading a single envelope multiplies the signal.
    For selective (HF) fading the band is split at ``crossover_hz`` and each half
    gets its own decorrelated envelope, so the spectral tilt wanders — the
    audible signature of HF selective fading.
    """
    if signal_in.size == 0 or depth_db <= 0.01:
        return signal_in.astype(np.float32, copy=False)

    n = signal_in.size
    if not selective:
        gain = flat_fading_gain(n, sample_rate, depth_db, rate_hz, rng)
        return (signal_in * gain).astype(np.float32)

    low = lowpass(signal_in, crossover_hz, sample_rate)
    high = highpass(signal_in, crossover_hz, sample_rate)
    g_low = flat_fading_gain(n, sample_rate, depth_db, rate_hz, rng)
    g_high = flat_fading_gain(n, sample_rate, depth_db, rate_hz * 1.3, rng)
    return (low * g_low + high * g_high).astype(np.float32)

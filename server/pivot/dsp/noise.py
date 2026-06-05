"""Frequency-interpolated noise generation (spec §4.1.1).

Pink+Gaussian weighting is strongest at low HF and fades toward UHF — the
``pink_weight`` comes from :class:`pivot.core.bands.BandConditions`. Low-level
QRM carrier tones are added only in the HF region.
"""

from __future__ import annotations

import numpy as np

from pivot.dsp.filters import normalise_rms


def white_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-RMS Gaussian (white) noise."""
    return normalise_rms(rng.standard_normal(n).astype(np.float32))


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Unit-RMS pink (1/f) noise via spectral shaping of white noise."""
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    freqs = np.arange(spectrum.size, dtype=np.float64)
    freqs[0] = 1.0  # avoid div-by-zero at DC
    spectrum = spectrum / np.sqrt(freqs)  # -3 dB/octave
    pink = np.fft.irfft(spectrum, n=n)
    return normalise_rms(pink.astype(np.float32))


def band_noise(
    n: int,
    sample_rate: int,
    pink_weight: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Unit-RMS noise blending pink and white per ``pink_weight`` (0..1)."""
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    w = float(np.clip(pink_weight, 0.0, 1.0))
    mix = w * pink_noise(n, rng) + (1.0 - w) * white_noise(n, rng)
    return normalise_rms(mix)


def qrm_tones(
    n: int,
    sample_rate: int,
    rng: np.random.Generator,
    n_tones: int = 3,
    level: float = 0.15,
) -> np.ndarray:
    """Low-level interfering carrier tones ('QRM'), HF only (§4.1.1).

    A handful of faint, slowly-drifting heterodyne tones in the voice band.
    """
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n) / sample_rate
    out = np.zeros(n, dtype=np.float64)
    for _ in range(n_tones):
        f0 = rng.uniform(500.0, 2500.0)
        drift = rng.uniform(-0.5, 0.5)  # Hz/s heterodyne drift
        amp = level * rng.uniform(0.5, 1.0)
        phase = rng.uniform(0, 2 * np.pi)
        out += amp * np.sin(2 * np.pi * (f0 * t + 0.5 * drift * t * t) + phase)
    return out.astype(np.float32)


def add_noise_for_snr(
    voice: np.ndarray,
    noise: np.ndarray,
    snr_db: float,
) -> np.ndarray:
    """Mix unit-RMS ``noise`` into ``voice`` to achieve ``snr_db``.

    The voice level is measured; noise is scaled so signal-to-noise equals the
    band's SNR. Lower SNR (low HF, jamming, bad atmospherics) buries the voice.
    """
    from pivot.dsp.filters import rms

    sig_rms = rms(voice)
    if sig_rms < 1e-9:
        return voice
    noise_rms_target = sig_rms / (10.0 ** (snr_db / 20.0))
    return (voice + noise * noise_rms_target).astype(np.float32)

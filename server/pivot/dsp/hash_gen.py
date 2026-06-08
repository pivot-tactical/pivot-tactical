"""Encrypted hash generator (spec §4.4, §3.4.2).

Synthesises what a Plain-mode receiver hears when a Cypher transmission is on the
air: a digital-style warble that follows the *cadence* of speech but conveys none
of its *content* — similar to an unsynchronised KY-57. Three ingredients:

* a base narrow-band noise (~600–1800 Hz) with rapid amplitude modulation at
  30–80 Hz (the digital warble),
* a low-amplitude metallic tone cluster at non-musical (inharmonic) intervals,
  modulated by the same warble, and
* an envelope follower of the original voice that gates the whole thing, so the
  rhythm of speech is audible but the words are not.

The result is routed through the band DSP chain by the engine, so HF/VHF/UHF
cypher garble each take on their region's character (§3.4.2).
"""

from __future__ import annotations

import numpy as np

from pivot.dsp.filters import bandpass, lowpass, normalise_rms

# Inharmonic ("metallic") partials — deliberately non-musical ratios.
_METALLIC_PARTIALS_HZ = (713.0, 1097.0, 1471.0, 1933.0)


def envelope_follower(
    signal_in: np.ndarray,
    sample_rate: int,
    smooth_hz: float = 30.0,
) -> np.ndarray:
    """Extract a smooth loudness envelope (0..1) from a voice buffer.

    Full-wave rectify then low-pass smooth; normalise to peak 1. This is the
    cadence carrier that makes the hash track speech rhythm (§4.4).
    """
    if signal_in.size == 0:
        return np.zeros(0, dtype=np.float32)
    rectified = np.abs(signal_in).astype(np.float32)
    env = lowpass(rectified, smooth_hz, sample_rate)
    env = np.clip(env, 0.0, None)
    peak = float(np.max(env)) or 1.0
    return (env / peak).astype(np.float32)


def encrypted_hash(
    voice: np.ndarray,
    sample_rate: int,
    rng: np.random.Generator,
    warble_hz: float | None = None,
    harmonic_level: float = 0.25,
) -> np.ndarray:
    """Generate the encrypted-hash signal for a clean ``voice`` buffer.

    The output has the same length as ``voice`` and unit-ish RMS, ready to be
    fed through the band DSP chain (noise/fading) by the engine.
    """
    n = voice.size
    if n == 0:
        return np.zeros(0, dtype=np.float32)

    t = np.arange(n) / sample_rate

    # --- digital warble AM (30–80 Hz), slowly wandering within the band ---
    if warble_hz is None:
        warble_hz = float(rng.uniform(30.0, 80.0))
    wobble = 1.0 + 0.2 * np.sin(2 * np.pi * 0.7 * t)  # slow drift of the warble
    am = 0.5 * (1.0 + np.sin(2 * np.pi * warble_hz * wobble * t))
    am = 0.25 + 0.75 * am  # keep some floor so it never fully gates to silence

    # --- base narrow-band noise 600–1800 Hz ---
    base = bandpass(rng.standard_normal(n).astype(np.float32), 600.0, 1800.0, sample_rate)
    base = normalise_rms(base) * am

    # --- metallic inharmonic tone cluster ---
    metallic = np.zeros(n, dtype=np.float64)
    for f0 in _METALLIC_PARTIALS_HZ:
        phase = rng.uniform(0, 2 * np.pi)
        metallic += np.sin(2 * np.pi * f0 * t + phase)
    metallic = normalise_rms(metallic.astype(np.float32)) * am * harmonic_level

    # --- gate by the voice cadence so rhythm survives, content does not ---
    env = envelope_follower(voice, sample_rate)
    hashed = (base + metallic) * env
    return normalise_rms(hashed)

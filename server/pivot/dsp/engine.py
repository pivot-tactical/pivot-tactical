"""The DSP chain assembly and per-reception dispatch (spec §4).

Given a clean voice buffer and the :class:`~pivot.core.bands.BandConditions` at a
frequency, render exactly what a listener with a given
:class:`~pivot.core.crypto.Reception` should hear. The same engine serves the
live router and the AAR Dirty re-render (§4.5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pivot.core.bands import BandConditions
from pivot.core.crypto import Reception
from pivot.dsp.fading import apply_fading
from pivot.dsp.filters import bandpass, normalise_rms, soft_clip
from pivot.dsp.hash_gen import encrypted_hash
from pivot.dsp.noise import add_noise_for_snr, band_noise, qrm_tones
from pivot.dsp.tone import ptt_click, squelch_tail


@dataclass
class DspEngine:
    """Stateless renderer (apart from sample rate). Pass an ``rng`` for
    deterministic output under test; omit for fresh randomness per render."""

    sample_rate: int = 16_000

    def _rng(self, rng: np.random.Generator | None) -> np.random.Generator:
        return rng if rng is not None else np.random.default_rng()

    # -- band chain shared by clear & hash --------------------------------- #

    def _band_chain(
        self,
        carrier: np.ndarray,
        conditions: BandConditions,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Bandpass → fading → frequency-shaped noise (to the band SNR) → QRM."""
        sr = self.sample_rate
        x = bandpass(carrier, conditions.bandpass_low_hz, conditions.bandpass_high_hz, sr)
        x = apply_fading(
            x,
            sr,
            conditions.fading_depth_db,
            conditions.fading_rate_hz,
            conditions.selective_fading,
            rng,
        )
        noise = band_noise(x.size, sr, conditions.pink_weight, rng)
        if conditions.qrm:
            noise = normalise_rms(noise + 0.6 * qrm_tones(x.size, sr, rng))
        out = add_noise_for_snr(x, noise, conditions.snr_db)
        return out

    # -- individual renders ------------------------------------------------ #

    def render_clear(self, voice, conditions, rng=None):
        rng = self._rng(rng)
        return soft_clip(self._band_chain(np.asarray(voice, dtype=np.float32), conditions, rng))

    def render_hash(self, voice, conditions, rng=None):
        rng = self._rng(rng)
        hashed = encrypted_hash(np.asarray(voice, dtype=np.float32), self.sample_rate, rng)
        return soft_clip(self._band_chain(hashed, conditions, rng))

    def render_plain_collision(self, voices, conditions, rng=None):
        """Overlap two+ clean plain voices (chaotic doubling), then band chain."""
        rng = self._rng(rng)
        mixed = _sum_aligned(voices)
        return soft_clip(self._band_chain(mixed, conditions, rng))

    def render_crypto_jam(self, n_samples, conditions, rng=None):
        """The cypher-collision jam: heavy modulated garble for the overlap."""
        rng = self._rng(rng)
        if n_samples <= 0:
            return np.zeros(0, dtype=np.float32)
        sr = self.sample_rate
        t = np.arange(n_samples) / sr
        # Aggressive multi-rate warble over band noise — clearly "jammed".
        carrier = bandpass(rng.standard_normal(n_samples).astype(np.float32), 400.0, 2600.0, sr)
        am = 0.5 * (1 + np.sin(2 * np.pi * 90 * t)) * (0.5 + 0.5 * np.sin(2 * np.pi * 13 * t))
        jam = normalise_rms(carrier * (0.3 + 0.7 * am).astype(np.float32))
        # Mostly noise: drive SNR negative so nothing intelligible survives.
        jam_conditions = conditions
        out = self._band_chain(jam, jam_conditions, rng)
        return soft_clip(out * 1.2)

    # -- dispatch ---------------------------------------------------------- #

    def render(
        self,
        reception: Reception,
        voice: np.ndarray | None = None,
        *,
        voices: list[np.ndarray] | None = None,
        conditions: BandConditions,
        rng: np.random.Generator | None = None,
        with_transients: bool = False,
    ) -> np.ndarray:
        """Render a buffer for ``reception``.

        For ``CLEAR``/``HASH`` pass ``voice``; for ``PLAIN_COLLISION`` pass
        ``voices``; for ``CRYPTO_JAM`` pass ``voice`` (only its length is used).
        ``with_transients`` adds a PTT click and squelch tail around the buffer —
        used for whole-event AAR playback (§3.2.4, §4.1.1).
        """
        if reception is Reception.SILENCE:
            n = voice.size if voice is not None else 0
            return np.zeros(n, dtype=np.float32)
        if reception is Reception.CLEAR:
            out = self.render_clear(_require(voice, "voice"), conditions, rng)
        elif reception is Reception.HASH:
            out = self.render_hash(_require(voice, "voice"), conditions, rng)
        elif reception is Reception.PLAIN_COLLISION:
            out = self.render_plain_collision(_require(voices, "voices"), conditions, rng)
        elif reception is Reception.CRYPTO_JAM:
            n = voice.size if voice is not None else (len(voices[0]) if voices else 0)
            out = self.render_crypto_jam(n, conditions, rng)
        else:  # pragma: no cover - exhaustive
            raise ValueError(f"unhandled reception: {reception}")

        if with_transients:
            out = self._wrap_transients(out, conditions)
        return out

    def _wrap_transients(self, out: np.ndarray, conditions: BandConditions) -> np.ndarray:
        sr = self.sample_rate
        click = ptt_click(sr)
        tail = squelch_tail(sr, conditions.squelch_tail_ms)
        return np.concatenate([click, out, click, tail]).astype(np.float32)


def render_reception(
    reception: Reception,
    voice: np.ndarray,
    conditions: BandConditions,
    sample_rate: int = 16_000,
    rng: np.random.Generator | None = None,
    with_transients: bool = False,
) -> np.ndarray:
    """Convenience for the common single-stream case (AAR re-render, §4.5)."""
    engine = DspEngine(sample_rate=sample_rate)
    return engine.render(
        reception,
        voice,
        conditions=conditions,
        rng=rng,
        with_transients=with_transients,
    )


# --------------------------------------------------------------------------- #


def _sum_aligned(voices: list[np.ndarray]) -> np.ndarray:
    """Sum buffers of differing length (zero-extended), for collision mixing."""
    arrs = [np.asarray(v, dtype=np.float32) for v in voices if v is not None and len(v)]
    if not arrs:
        return np.zeros(0, dtype=np.float32)
    n = max(a.size for a in arrs)
    acc = np.zeros(n, dtype=np.float32)
    for a in arrs:
        acc[: a.size] += a
    # Normalise back toward a single-voice level so doubling doesn't just clip.
    peak = float(np.max(np.abs(acc))) or 1.0
    if peak > 1.0:
        acc = acc / peak
    return acc


def _require(value, name: str):
    if value is None:
        raise ValueError(f"{name} is required for this reception type")
    return value

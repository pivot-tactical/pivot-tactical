"""The DSP chain assembly and per-reception dispatch (spec §4).

Given a clean voice buffer and the :class:`~pivot.core.bands.BandConditions` at a
frequency, render exactly what a listener with a given
:class:`~pivot.core.crypto.Reception` should hear. The same engine serves the
live router and the AAR Dirty re-render (§4.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pivot.core.bands import BandConditions, net_key_for
from pivot.core.crypto import Reception
from pivot.dsp.fading import apply_fading
from pivot.dsp.filters import bandpass, normalise_rms, soft_clip
from pivot.dsp.hash_gen import encrypted_hash
from pivot.dsp.noise import NoiseTexture, add_noise_for_snr, idle_noise_amplitude
from pivot.dsp.tone import ptt_click, squelch_tail

# Per-net noise textures kept alive at once; oldest-touched are evicted so a
# long session of tuning around never grows the engine unboundedly.
_MAX_TEXTURES = 64


@dataclass
class DspEngine:
    """Renderer with per-net noise texture state (spec §4.1.1).

    The renders themselves are pure given a texture; the only state is the
    per-net :class:`NoiseTexture` map that keeps the layered channel noise
    continuous from one 20 ms frame to the next. Pass an ``rng`` for
    deterministic output under test — that path uses a fresh, seeded texture
    instead of the shared per-net one.
    """

    sample_rate: int = 16_000
    _textures: dict[int, NoiseTexture] = field(default_factory=dict, repr=False)
    _texture_use: dict[int, int] = field(default_factory=dict, repr=False)
    _use_counter: int = 0

    def _rng(self, rng: np.random.Generator | None) -> np.random.Generator:
        return rng if rng is not None else np.random.default_rng()

    def _texture(
        self, conditions: BandConditions, rng: np.random.Generator | None
    ) -> NoiseTexture:
        """The net's persistent texture, or a fresh seeded one when ``rng`` is
        given (deterministic tests and whole-buffer AAR re-renders)."""
        if rng is not None:
            return NoiseTexture(self.sample_rate, rng)
        key = net_key_for(conditions.freq_hz)
        texture = self._textures.get(key)
        if texture is None:
            texture = NoiseTexture(self.sample_rate)
            self._textures[key] = texture
            if len(self._textures) > _MAX_TEXTURES:
                oldest = min(self._textures, key=lambda k: self._texture_use.get(k, 0))
                self._textures.pop(oldest, None)
                self._texture_use.pop(oldest, None)
        self._use_counter += 1
        self._texture_use[key] = self._use_counter
        return texture

    # -- band chain shared by clear & hash --------------------------------- #

    def _band_chain(
        self,
        carrier: np.ndarray,
        conditions: BandConditions,
        rng: np.random.Generator,
        texture: NoiseTexture | None = None,
    ) -> np.ndarray:
        """Bandpass → fading → layered P.372 noise (to the band SNR)."""
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
        texture = texture if texture is not None else self._texture(conditions, rng)
        noise = texture.render(x.size, conditions)
        out = add_noise_for_snr(x, noise, conditions.snr_db)
        return out

    def render_idle_noise(self, n_samples, conditions, rng=None):
        """The continuous ambient noise floor of an idle (un-keyed) channel.

        This is the open-squelch "hash" a listener hears between transmissions.
        It reuses the band's layered noise texture (the same components as the
        in-transmission chain) with no voice carrier, scaled by an SNR-derived
        level so noisy/jammed/interfered bands hiss louder than clean ones. The
        frame is *not* re-normalised: the texture's static crashes and
        interference swells ride above the floor, so an idle channel audibly
        lives and changes. It is generated server-side per frequency, so every
        listener on a net hears the identical floor and the recordings carry
        the matching conditions.
        """
        if n_samples <= 0:
            return np.zeros(0, dtype=np.float32)
        sr = self.sample_rate
        texture = self._texture(conditions, rng)
        noise = texture.render(n_samples, conditions)
        # Sit the hiss in the voice passband so it matches where speech lands.
        noise = bandpass(noise, conditions.bandpass_low_hz, conditions.bandpass_high_hz, sr)
        level = idle_noise_amplitude(conditions.snr_db, conditions.jammed)
        return soft_clip((noise * level).astype(np.float32))

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

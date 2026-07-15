"""MELP-flavoured digital voice for Cypher mode (AN/PRC-160 baseline, §3.4.1).

A Cypher-mode receiver decoding a Cypher transmission hears *digital* voice,
not the analog channel: a set like the AN/PRC-160 carries voice as an encrypted
MELPe bitstream (STANAG 4591) over the RF link, so what reaches the operator's
ear is the local vocoder's reconstruction — never the channel itself. Three
audible consequences drive this module:

* **Cleaner when it decodes.** Channel noise never rides under the voice: a
  codec frame either decodes (clean vocoded speech over a silent background)
  or it doesn't. On a channel where analog copy is already hissy, decoded
  digital voice is still clean.
* **Cuts a few dB further through noise.** Forward error correction keeps
  frames decoding below the SNR where analog voice has become hard copy — the
  price is a hard cliff instead of analog's graceful fade.
* **Fails digitally.** Past the cliff the decoder mutes frames (audio clips
  out), repeats the last good one, or reconstructs speech from corrupted
  parameters — the classic pitched-up "Donald Duck" squawk — rather than
  sinking into noise. Under jamming essentially nothing decodes: near-silence
  broken by the odd garbled squawk.

The vocoder colour is a light *emulation*, not a bit-exact MELPe codec:
narrowband (≈250–3400 Hz), the per-band spectral envelope held constant across
each codec frame (parametric "steppiness"), and gentle companding. Speech stays
fully intelligible while sounding unmistakably like a vocoder, not an open mic.

One :class:`DigitalVoice` instance per net keeps the band-filter and
frame-error state continuous across the live router's 20 ms frames; a fresh,
seeded instance renders a whole AAR buffer deterministically (mirroring
:class:`pivot.dsp.noise.NoiseTexture`).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sp_signal

from pivot.dsp.filters import rms

# The codec frame the decode simulation works in. Real MELPe frames are
# 22.5 ms; the engine's native block is 20 ms and the 2.5 ms difference is
# inaudible, so the native block doubles as the codec frame (no rebuffering
# latency on the live path).
CODEC_FRAME_MS = 20.0

# Vocoder passband (MELPe is a narrowband coder).
_BAND_LOW_HZ = 250.0
_BAND_HIGH_HZ = 3400.0
_N_BANDS = 12

# The digital cliff. Frame-error probability follows a steep logistic in the
# frame's effective SNR: ~2% at +6 dB (analog copy is already noisy there —
# this is the "cuts through" margin), 50% at 0 dB, ~98% by −6 dB. Fading
# wobbles the effective SNR so errors arrive in bursts, as they do on HF.
_CLIFF_MID_DB = 0.0
_CLIFF_WIDTH_DB = 1.5

# Once a frame errors, the next is likelier to (interleaver/burst behaviour).
_BURST_CARRY = 0.35

# How errored frames manifest, per §3.4.1: parameter garble (the duck), a
# frame repeat, or a hard mute. Deep past the cliff (jamming) the decoder
# mostly stays muted — squelch silence with the odd squawk.
_P_GARBLE, _P_REPEAT = 0.45, 0.25
_DEEP_LOSS_P_ERR = 0.95     # past this, treat as "nothing is getting through"
_DEEP_P_GARBLE = 0.12       # rare squawks over silence

# Boundary ramp so mutes/garbles clip in and out without clicks (~3 ms).
_RAMP_MS = 3.0


def _frame_error_probability(snr_eff_db: float) -> float:
    """Steep logistic decode-failure curve around the digital cliff."""
    x = (_CLIFF_MID_DB - snr_eff_db) / _CLIFF_WIDTH_DB
    return 1.0 / (1.0 + math.exp(-x))


class DigitalVoice:
    """Streaming MELP-style vocoder + frame-loss channel for one net."""

    def __init__(self, sample_rate: int, rng: np.random.Generator | None = None) -> None:
        self.sample_rate = sample_rate
        self.rng = rng if rng is not None else np.random.default_rng()
        self.frame = max(1, int(sample_rate * CODEC_FRAME_MS / 1000.0))
        self._bands_buf = np.empty((_N_BANDS, self.frame), dtype=np.float64)

        # Sharp streaming pre-filter: everything outside the vocoder passband is
        # gone before the analysis banks (whose own skirts are gentle).
        nyq = sample_rate / 2.0
        self._pre_sos = sp_signal.butter(
            4, (_BAND_LOW_HZ / nyq, _BAND_HIGH_HZ / nyq), btype="bandpass", output="sos"
        )
        self._pre_zi = np.zeros((self._pre_sos.shape[0], 2))

        # Log-spaced analysis bands across the vocoder passband, each a 2nd-order
        # Butterworth bandpass run with retained state so the chain streams.
        edges = np.geomspace(_BAND_LOW_HZ, _BAND_HIGH_HZ, _N_BANDS + 1)
        self._sos: list[np.ndarray] = []
        self._zi: list[np.ndarray] = []
        for lo, hi in zip(edges[:-1], edges[1:], strict=False):
            sos = sp_signal.butter(2, (lo / nyq, min(hi / nyq, 0.999)),
                                   btype="bandpass", output="sos")
            self._sos.append(sos)
            self._zi.append(np.zeros((sos.shape[0], 2)))
        # Per-band fast envelope followers (~8 ms) for the intra-frame flatten.
        self._follow = np.zeros(_N_BANDS)
        self._alpha = math.exp(-1.0 / (0.008 * sample_rate))

        # Fading wobble on the effective SNR: an Ornstein–Uhlenbeck walk stepped
        # once per codec frame, so decode failures bunch up in fades.
        self._wobble = 0.0
        self._wobble_tau_s = 1.2

        # Decode state.
        self._last_good: np.ndarray | None = None
        self._prev_errored = False
        self._ramp = int(sample_rate * _RAMP_MS / 1000.0)

    # -- vocoder colour ------------------------------------------------------ #

    def _vocode(self, x: np.ndarray) -> np.ndarray:
        """Narrowband, spectrally-stepped, gently companded 'MELP' voice."""
        n = x.size
        pre, self._pre_zi = sp_signal.sosfilt(self._pre_sos, x, zi=self._pre_zi)
        a = self._alpha

        bands = self._bands_buf if n == self.frame else np.empty((_N_BANDS, n), dtype=np.float64)
        for b in range(_N_BANDS):
            bands[b], self._zi[b] = sp_signal.sosfilt(self._sos[b], pre, zi=self._zi[b])

        ab = np.abs(bands)

        # Fast streaming one-pole envelope on |band| (state carried).
        zi = (a * self._follow).reshape(_N_BANDS, 1)
        env, _ = sp_signal.lfilter([1.0 - a], [1.0, -a], ab, axis=-1, zi=zi)
        self._follow = env[:, -1]

        # ...flattened to the frame's average level: the band's envelope is
        # a per-frame parameter (zero-order hold), which is exactly the
        # steppy, parametric character of a frame-based vocoder.
        frame_level = np.mean(ab, axis=-1, keepdims=True)
        gain = np.clip(frame_level / (env + 1e-6), 0.0, 4.0)

        acc = np.sum(bands * gain, axis=0)
        y = np.tanh(1.8 * acc)
        # Hold the decoded loudness at the talker's level (the codec transmits
        # gain as a parameter; it does not inherit the channel's). The makeup
        # gain is capped so content the vocoder barely captured — anything
        # outside its narrowband — stays attenuated instead of being pumped
        # back up to the input level. The 0.6 sits the decoded voice at about
        # the analog chain's loudness, so toggling Plain/Cypher doesn't jump.
        in_rms, out_rms = rms(x), rms(y.astype(np.float32))
        if out_rms > 1e-6 and in_rms > 1e-6:
            y *= min(0.6 * in_rms / out_rms, 4.0)
        return y.astype(np.float32)

    # -- frame-error channel -------------------------------------------------- #

    def _snr_wobble(self, conditions) -> float:
        """OU step of the fading-driven SNR wobble for this frame."""
        dt = self.frame / self.sample_rate
        a = math.exp(-dt / self._wobble_tau_s)
        sigma = max(0.0, float(conditions.fading_depth_db)) / 4.0
        self._wobble = a * self._wobble + sigma * math.sqrt(
            max(0.0, 1.0 - a * a)
        ) * float(self.rng.standard_normal())
        return self._wobble

    def _garble(self, source: np.ndarray) -> np.ndarray:
        """Corrupt-parameter reconstruction: the pitched-up 'duck' squawk."""
        n = source.size
        if n == 0:
            return source
        rate = float(self.rng.uniform(1.35, 1.8))  # read fast -> pitch up
        idx = (np.arange(n) * rate) % max(1, n - 1)
        duck = np.interp(idx, np.arange(n), source.astype(np.float64))
        # Warble + hard digital clipping: it squawks, it doesn't hiss.
        t = np.arange(n) / self.sample_rate
        duck *= 1.0 + 0.6 * np.sin(2.0 * np.pi * 42.0 * t + self.rng.uniform(0, 2 * np.pi))
        peak = float(np.max(np.abs(duck))) or 1.0
        level = rms(source) or 0.05
        duck = np.clip(duck, -0.6 * peak, 0.6 * peak)
        duck_rms = rms(duck.astype(np.float32))
        if duck_rms > 1e-6:
            duck *= level / duck_rms
        return duck.astype(np.float32)

    def _apply_ramps(self, frame: np.ndarray, fade_in: bool, fade_out: bool) -> np.ndarray:
        r = min(self._ramp, frame.size // 2)
        if r <= 0:
            return frame
        out = frame.copy()
        if fade_in:
            out[:r] *= np.linspace(0.0, 1.0, r, dtype=np.float32)
        if fade_out:
            out[-r:] *= np.linspace(1.0, 0.0, r, dtype=np.float32)
        return out

    def _process_frame(self, chunk: np.ndarray, conditions) -> np.ndarray:
        vocoded = self._vocode(chunk)
        snr_eff = float(conditions.snr_db) + self._snr_wobble(conditions)
        p_err = _frame_error_probability(snr_eff)
        if self._prev_errored:
            p_err = min(0.98, p_err + _BURST_CARRY)

        errored = bool(self.rng.random() < p_err)
        if not errored:
            out = self._apply_ramps(vocoded, fade_in=self._prev_errored, fade_out=False)
            self._last_good = vocoded
            self._prev_errored = False
            return out

        # Errored frame: deep past the cliff the decoder mostly mutes (squelch
        # silence with rare squawks); around the cliff it garbles/repeats/mutes.
        deep = p_err >= _DEEP_LOSS_P_ERR
        roll = float(self.rng.random())
        if deep:
            mode = "garble" if roll < _DEEP_P_GARBLE else "mute"
        elif roll < _P_GARBLE:
            mode = "garble"
        elif roll < _P_GARBLE + _P_REPEAT:
            mode = "repeat"
        else:
            mode = "mute"

        if mode == "mute" or (mode == "repeat" and self._last_good is None):
            out = np.zeros_like(vocoded)
        elif mode == "repeat":
            held = self._last_good
            out = 0.8 * held[: vocoded.size]
            if out.size < vocoded.size:
                out = np.pad(out, (0, vocoded.size - out.size))
            out = self._apply_ramps(out.astype(np.float32), fade_in=True, fade_out=True)
        else:  # garble
            source = vocoded if rms(vocoded) > 1e-4 else (
                self._last_good if self._last_good is not None else vocoded
            )
            out = self._apply_ramps(self._garble(source[: vocoded.size]),
                                    fade_in=True, fade_out=True)
        self._prev_errored = True
        return out.astype(np.float32)

    # -- public --------------------------------------------------------------- #

    def render(self, voice: np.ndarray, conditions) -> np.ndarray:
        """Vocode + decode-simulate ``voice`` under ``conditions``.

        Accepts any length: the live router's 20 ms blocks map one-to-one onto
        codec frames; a whole AAR buffer is chunked internally. Output length
        always equals input length.
        """
        x = np.asarray(voice, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return x
        out = np.empty_like(x)
        for start in range(0, x.size, self.frame):
            end = min(start + self.frame, x.size)
            chunk = x[start:end]
            if chunk.size < self.frame:
                chunk = np.pad(chunk, (0, self.frame - chunk.size))
            rendered = self._process_frame(chunk, conditions)
            out[start:end] = rendered[: end - start]
        return out

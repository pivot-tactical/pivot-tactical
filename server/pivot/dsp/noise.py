"""Frequency-interpolated, layered noise generation (spec §4.1.1).

The composite channel noise follows Recommendation ITU-R P.372 ("Radio
noise"): what a receiver hears is the power sum of distinct components, each
with its own frequency dependence and audible character:

* **atmospheric** (lightning sferics) — impulsive static crashes over a low
  rumble; dominant at low HF and falling steeply with frequency,
* **man-made** — broadband hash, power-line buzz harmonics and ignition-style
  ticks; median ``Fam = c − d·log10(f/MHz)`` (P.372 Table 2),
* **galactic / receiver** — the smooth Gaussian hiss floor that is all that
  remains at VHF/UHF.

:func:`noise_component_weights` resolves the P.372 median figures into relative
powers at a frequency; :class:`NoiseTexture` renders the layered mix with slow,
stateful time variation (storm activity ebbs and flows, crashes arrive at
random, heterodynes drift) so successive frames are continuous and the channel
audibly *changes* rather than hissing statically. Instructor-induced
interference and jamming add their own layers on top.

Pink+Gaussian weighting is strongest at low HF and fades toward UHF — the
``pink_weight`` comes from :class:`pivot.core.bands.BandConditions`. Low-level
QRM carrier tones are added only in the HF region.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import signal as sp_signal

from pivot.dsp.filters import lowpass, normalise_rms


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


# --------------------------------------------------------------------------- #
# ITU-R P.372 layered noise model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoiseWeights:
    """Relative powers of the noise components at one frequency (sum = 1)."""

    atmospheric: float
    man_made: float
    galactic: float  # includes the receiver's own thermal floor


def noise_component_weights(freq_hz: float) -> NoiseWeights:
    """Resolve the ITU-R P.372 median noise figures into relative powers.

    Median external noise figures Fam (dB above kT0b) at f in MHz:

    * man-made, residential: ``Fam = 72.5 − 27.7·log10(f)`` (P.372 Table 2)
    * galactic:              ``Fam = 52.0 − 23.0·log10(f)`` (P.372 Table 2)
    * atmospheric (lightning, mid-latitude median): approximated as a steep
      ``95 − 65·log10(f)`` decay, dominant below ~5 MHz and gone past ~30 MHz
      (P.372 Figs. 2/3 envelope).

    A constant receiver noise figure (~8 dB) is folded into the galactic term
    so the mix never collapses to silence at UHF where external noise is below
    the set's own floor. The figures are converted to linear power and
    normalised: only the *blend* matters here — absolute loudness stays driven
    by the band curve's SNR (§4.1).
    """
    f_mhz = max(0.1, freq_hz / 1e6)
    lf = math.log10(f_mhz)
    atmospheric_db = 95.0 - 65.0 * lf
    man_made_db = 72.5 - 27.7 * lf
    galactic_db = 52.0 - 23.0 * lf
    receiver_db = 8.0

    p_atmo = 10.0 ** (atmospheric_db / 10.0) if atmospheric_db > 0.0 else 0.0
    p_man = 10.0 ** (man_made_db / 10.0) if man_made_db > 0.0 else 0.0
    p_gal = 10.0 ** (galactic_db / 10.0) if galactic_db > 0.0 else 0.0
    p_gal += 10.0 ** (receiver_db / 10.0)

    total = p_atmo + p_man + p_gal
    return NoiseWeights(
        atmospheric=p_atmo / total,
        man_made=p_man / total,
        galactic=p_gal / total,
    )


class _SlowEnvelope:
    """A scalar Ornstein–Uhlenbeck process sampled once per frame.

    Returns a per-sample envelope linearly interpolated from the previous
    frame's value, so the modulation is continuous across frame boundaries.
    """

    def __init__(self, tau_s: float, sigma: float, mean: float = 0.0) -> None:
        self.tau_s = tau_s
        self.sigma = sigma
        self.mean = mean
        self.value = mean

    def step(self, n: int, sample_rate: int, rng: np.random.Generator) -> np.ndarray:
        prev = self.value
        dt = n / sample_rate
        a = math.exp(-dt / self.tau_s)
        self.value = (
            self.mean + (prev - self.mean) * a
            + self.sigma * math.sqrt(max(0.0, 1.0 - a * a)) * float(rng.standard_normal())
        )
        return np.linspace(prev, self.value, n, endpoint=False, dtype=np.float32)


class _Heterodyne:
    """One slowly drifting interfering carrier with a finite lifetime."""

    def __init__(self, rng: np.random.Generator) -> None:
        self.freq_hz = float(rng.uniform(500.0, 2500.0))
        self.drift_hz_s = float(rng.uniform(-0.5, 0.5))
        self.amp = float(rng.uniform(0.5, 1.0))
        self.phase = float(rng.uniform(0.0, 2.0 * np.pi))
        self.life_s = float(rng.uniform(15.0, 45.0))

    def render(self, n: int, sample_rate: int) -> np.ndarray:
        dt = 1.0 / sample_rate
        freqs = self.freq_hz + self.drift_hz_s * dt * np.arange(n)
        phases = self.phase + 2.0 * np.pi * np.cumsum(freqs) * dt
        self.phase = float(phases[-1] % (2.0 * np.pi))
        self.freq_hz = float(freqs[-1])
        self.life_s -= n * dt
        return (self.amp * np.sin(phases)).astype(np.float32)


class NoiseTexture:
    """Stateful layered noise for one net, continuous across frames (§4.1.1).

    ``render`` returns noise calibrated to ~unit RMS over time but *not*
    re-normalised per frame: static crashes, storm surges and interference
    swells ride a few dB above the floor and quiet spells dip below it, which
    is exactly the time variation a real channel has. One instance per net
    keeps every layer's phase/envelope continuous between 20 ms frames; a
    fresh instance renders a whole AAR buffer the same way.
    """

    def __init__(self, sample_rate: int, rng: np.random.Generator | None = None) -> None:
        self.sample_rate = sample_rate
        self.rng = rng if rng is not None else np.random.default_rng()
        self._t = 0  # absolute sample counter (keeps oscillators continuous)
        # Storm activity: how lively the atmospheric layer is right now. Wanders
        # over ~half a minute so a bad patch of static comes and goes.
        self._storm = _SlowEnvelope(tau_s=25.0, sigma=0.5, mean=1.0)
        # Man-made activity: mild ebb and flow of the local hash.
        self._man = _SlowEnvelope(tau_s=12.0, sigma=0.25, mean=1.0)
        # Interference swell: induced interference surges rather than droning.
        self._surge = _SlowEnvelope(tau_s=5.0, sigma=0.45, mean=1.0)
        self._crash_carry = 0.0     # exponential tail of the last static crash
        self._tones: list[_Heterodyne] = []
        # Power-line buzz: fixed random harmonic phases for this net.
        self._buzz_phases = self.rng.uniform(0.0, 2.0 * np.pi, size=32)

    # -- component layers --------------------------------------------------- #

    def _atmospheric(self, n: int) -> np.ndarray:
        """Lightning sferics: pink rumble plus impulsive static crashes."""
        sr = self.sample_rate
        storm = np.clip(self._storm.step(n, sr, self.rng), 0.15, 2.5)
        rumble = pink_noise(n, self.rng) * 0.5

        # Crashes arrive at random (a few per second on an active band) and
        # decay over ~120 ms; the tail carries across frame boundaries.
        rate_hz = 1.5 * float(np.mean(storm))
        impulses = np.zeros(n, dtype=np.float64)
        n_crashes = self.rng.poisson(rate_hz * n / sr)
        if n_crashes:
            at = self.rng.integers(0, n, size=n_crashes)
            impulses[at] += self.rng.lognormal(mean=1.0, sigma=0.5, size=n_crashes)
        decay = math.exp(-1.0 / (sr * 0.12))
        env, zi = sp_signal.lfilter(
            [1.0], [1.0, -decay], impulses, zi=[self._crash_carry * decay]
        )
        self._crash_carry = float(env[-1])
        crashes = env.astype(np.float32) * white_noise(n, self.rng)
        return (rumble + crashes) * storm

    def _man_made(self, n: int) -> np.ndarray:
        """Local hash: broadband pinkish noise, mains buzz, ignition ticks."""
        sr = self.sample_rate
        activity = np.clip(self._man.step(n, sr, self.rng), 0.4, 1.8)
        hash_ = (0.7 * pink_noise(n, self.rng) + 0.3 * white_noise(n, self.rng))

        # Power-line buzz: odd-ish harmonics of 100 Hz through the voice band.
        t = (self._t + np.arange(n)) / sr
        buzz = np.zeros(n, dtype=np.float64)
        for k in range(3, 29, 2):  # 300 Hz .. 2.8 kHz
            buzz += (1.0 / k) * np.sin(2.0 * np.pi * 100.0 * k * t + self._buzz_phases[k])
        buzz = 0.25 * buzz / max(1e-6, float(np.max(np.abs(buzz))))

        # Sparse ignition-style ticks.
        ticks = np.zeros(n, dtype=np.float32)
        n_ticks = self.rng.poisson(4.0 * n / sr)
        if n_ticks:
            at = self.rng.integers(0, max(1, n - 8), size=n_ticks)
            for i in at:
                ticks[i : i + 8] += np.float32(self.rng.uniform(1.5, 3.0))
            ticks = lowpass(ticks * white_noise(n, self.rng), 3000.0, sr)
        return (hash_ + buzz.astype(np.float32) + ticks) * activity

    def _qrm(self, n: int) -> np.ndarray:
        """Persistent drifting heterodynes (HF QRM); tones live for ~half a
        minute then are replaced, so the interfering carriers wander rather
        than re-randomising every frame."""
        while len(self._tones) < 3:
            self._tones.append(_Heterodyne(self.rng))
        out = np.zeros(n, dtype=np.float32)
        for tone in self._tones:
            out += tone.render(n, self.sample_rate)
        self._tones = [t for t in self._tones if t.life_s > 0.0]
        return 0.15 * out

    def _interference(self, n: int, level: float) -> np.ndarray:
        """Instructor-induced interference: a swept carrier over surging noise.

        The swell envelope makes it ebb and flow so trainees hear the channel
        degrade in waves before deciding to change frequency.
        """
        sr = self.sample_rate
        surge = np.clip(self._surge.step(n, sr, self.rng), 0.2, 2.2)
        t = (self._t + np.arange(n)) / sr
        # Classic sweeper: triangle sweep 500–2500 Hz about twice a second.
        tri = 2.0 * np.abs(((0.4 * t) % 1.0) - 0.5)  # 0..1 triangle at 0.4 Hz
        inst_freq = 500.0 + 2000.0 * tri
        sweep = np.sin(2.0 * np.pi * np.cumsum(inst_freq) / sr)
        rough = white_noise(n, self.rng) * 0.8
        return level * (0.9 * sweep.astype(np.float32) + rough) * surge

    def _jam(self, n: int) -> np.ndarray:
        """Full jammer: an aggressive multi-rate AM warble riding on a strong,
        *continuous* broadband bed — unmistakably deliberate, and nothing
        intelligible survives.

        The bed is the masker: it never gaps, so a listener cannot hear the
        wanted voice through the amplitude dips of the warble (the classic
        "listening in the dips" that lets speech leak through a heavily
        modulated jammer). The warble is a separate layer on top purely for the
        deliberate-jammer character — even at its lowest the total level stays
        high, because the steady bed underneath it does not dip."""
        sr = self.sample_rate
        t = (self._t + np.arange(n)) / sr
        am = 0.5 * (1.0 + np.sin(2.0 * np.pi * 90.0 * t)) * (
            0.5 + 0.5 * np.sin(2.0 * np.pi * 13.0 * t)
        )
        # Continuous broadband bed (the actual masker) + a deeply-modulated
        # warble on top (the audible menace). Independent carriers so the bed
        # keeps hissing right through the warble's dips: the warble's peaks
        # still tower over the bed (so it is plainly a deliberate jammer), but
        # the trough never falls to a gap the voice could be heard through.
        bed = white_noise(n, self.rng) * 0.4
        warble = white_noise(n, self.rng) * (0.15 + 0.85 * am) * 1.7
        return (bed + warble).astype(np.float32)

    # -- composite ----------------------------------------------------------- #

    def render(self, n: int, conditions) -> np.ndarray:
        """One frame of the net's composite noise at ~unit long-term RMS."""
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        w = noise_component_weights(conditions.freq_hz)
        # Amplitude weights (sqrt of power) keep the power-sum interpretation.
        mix = math.sqrt(w.galactic) * white_noise(n, self.rng)
        if w.man_made > 1e-4:
            mix = mix + math.sqrt(w.man_made) * self._man_made(n)
        if w.atmospheric > 1e-4:
            mix = mix + math.sqrt(w.atmospheric) * self._atmospheric(n)
        if conditions.qrm:
            mix = mix + self._qrm(n)
        if conditions.interference > 0.0:
            mix = mix + self._interference(n, conditions.interference)
        if conditions.jammed:
            mix = 0.25 * mix + self._jam(n)
        self._t += n
        return mix.astype(np.float32)


def add_noise_for_snr(
    voice: np.ndarray,
    noise: np.ndarray,
    snr_db: float,
) -> np.ndarray:
    """Mix unit-RMS ``noise`` into ``voice`` at the target ``snr_db``.

    The signal is always carried at full strength; what a worse channel changes
    is how much *competing* noise rides with it. The voice level is measured and
    the noise scaled so signal-to-noise equals the band's SNR, so a low SNR (low
    HF, severe interference, jamming) means the noise dominates and *masks* the
    voice — the voice is not turned down, it is buried under the competing hash.

    The combined stream is then held near the signal's own level, standing in
    for a receiver's AGC: a swamped channel comes back as a wall of noise at
    ordinary loudness (matching the idle floor) rather than an ever-louder blast
    as the noise is piled on — and, crucially, never as a still-clean voice
    sitting quietly on top of the noise. Levelling preserves the signal-to-noise
    ratio, so it changes loudness, not intelligibility.
    """
    from pivot.dsp.filters import rms

    sig_rms = rms(voice)
    if sig_rms < 1e-9:
        return voice
    noise_rms_target = sig_rms / (10.0 ** (snr_db / 20.0))
    mixed = voice + noise * noise_rms_target
    mixed_rms = rms(mixed)
    if mixed_rms > sig_rms:
        mixed = mixed * (sig_rms / mixed_rms)
    return mixed.astype(np.float32)


# Open-squelch idle "hash": the ambient receiver noise floor heard on a tuned
# channel between transmissions. Its loudness tracks the band — a noisy low-HF
# or jammed net hisses hard, clean UHF barely whispers — so an operator can tell
# a live channel from a dead one by ear (§3.2.2). These are RMS amplitudes; the
# idle floor fades linearly (in SNR) between them.
IDLE_NOISE_RMS_NOISY = 0.22   # loudest hiss, at/below the low-HF SNR floor
IDLE_NOISE_RMS_CLEAN = 0.035  # faint hiss on a clean UHF channel
_IDLE_SNR_FLOOR_DB = 6.0
_IDLE_SNR_CEIL_DB = 32.0


def idle_noise_amplitude(snr_db: float, jammed: bool = False) -> float:
    """Target RMS for the idle noise floor at a band's ``snr_db`` (§4.1.1).

    Louder on noisy (low-SNR) bands, quieter on clean ones; jamming pins it to
    the loud end so a jammed net is unmistakably a wall of noise.
    """
    span = _IDLE_SNR_CEIL_DB - _IDLE_SNR_FLOOR_DB
    t = (snr_db - _IDLE_SNR_FLOOR_DB) / span
    t = min(1.0, max(0.0, t))
    level = IDLE_NOISE_RMS_NOISY + (IDLE_NOISE_RMS_CLEAN - IDLE_NOISE_RMS_NOISY) * t
    if jammed:
        level = max(level, IDLE_NOISE_RMS_NOISY)
    return level

"""The layered ITU-R P.372 noise model (spec §4.1.1).

Asserts the defining structural properties: the component blend follows the
P.372 frequency dependence (atmospheric dominates low HF, galactic/receiver
hiss is all that remains at UHF), the texture is continuous and time-varying
across frames, and the instructor's per-net interference/jam layers audibly
load the channel.
"""

from __future__ import annotations

import numpy as np
import pytest

from pivot.core.bands import BandProfile
from pivot.dsp.engine import DspEngine
from pivot.dsp.filters import rms
from pivot.dsp.noise import NoiseTexture, noise_component_weights

SR = 16_000
FRAME = 320  # 20 ms

NET = 14_250_000.0


def _conditions(mhz: float, **scenario):
    profile = BandProfile()
    if scenario:
        profile.set_net_scenario(mhz * 1e6, **scenario)
    return profile.conditions_at(mhz * 1e6)


# --- P.372 component weights ------------------------------------------------ #


def test_weights_sum_to_one_across_range():
    for f in (1.6e6, 7e6, 30e6, 145e6, 440e6, 2.4e9):
        w = noise_component_weights(f)
        assert w.atmospheric + w.man_made + w.galactic == pytest.approx(1.0)
        assert min(w.atmospheric, w.man_made, w.galactic) >= 0.0


def test_atmospheric_dominates_low_hf_and_vanishes_above():
    low = noise_component_weights(2e6)
    vhf = noise_component_weights(145e6)
    assert low.atmospheric > 0.5            # lightning rules the bottom of HF
    assert vhf.atmospheric == pytest.approx(0.0, abs=1e-6)


def test_atmospheric_share_falls_with_frequency():
    shares = [noise_component_weights(f).atmospheric for f in (2e6, 5e6, 10e6, 20e6)]
    assert all(b <= a for a, b in zip(shares, shares[1:], strict=False))


def test_galactic_receiver_floor_dominates_uhf():
    uhf = noise_component_weights(2.4e9)
    assert uhf.galactic > 0.8


def test_man_made_peaks_in_mid_band():
    # Man-made hash outweighs both lightning and the galactic floor around VHF.
    w = noise_component_weights(50e6)
    assert w.man_made > w.atmospheric and w.man_made > w.galactic


# --- texture: continuity and time variation ---------------------------------- #


def test_texture_frames_are_finite_and_calibrated():
    texture = NoiseTexture(SR, np.random.default_rng(1))
    cond = _conditions(14.25)
    frames = [texture.render(FRAME, cond) for _ in range(100)]
    whole = np.concatenate(frames)
    assert np.isfinite(whole).all()
    # Calibrated near unit RMS over time (per-frame RMS may swing — see below).
    assert 0.3 < rms(whole) < 3.0


def test_texture_level_changes_over_time():
    """The channel audibly lives: per-frame energy varies, it never sits at a
    re-normalised constant."""
    texture = NoiseTexture(SR, np.random.default_rng(7))
    cond = _conditions(3.0)  # storm-prone low HF
    levels = np.array([rms(texture.render(FRAME, cond)) for _ in range(150)])
    assert levels.std() / levels.mean() > 0.05


def test_texture_is_continuous_across_frame_boundaries():
    """Two consecutive frames join without a step discontinuity bigger than the
    signal's own sample-to-sample movement allows."""
    texture = NoiseTexture(SR, np.random.default_rng(3))
    cond = _conditions(7.1)
    a = texture.render(FRAME, cond)
    b = texture.render(FRAME, cond)
    step = abs(float(b[0]) - float(a[-1]))
    typical = float(np.mean(np.abs(np.diff(np.concatenate([a, b])))))
    assert step < 20.0 * typical + 0.5


def test_texture_character_differs_by_frequency():
    """Low HF noise is low-frequency-weighted (rumble + crashes); UHF is flat
    hiss — the audible signature of the P.372 blend."""
    rng = np.random.default_rng(5)
    n = SR  # 1 s
    hf = NoiseTexture(SR, rng).render(n, _conditions(2.0))
    uhf = NoiseTexture(SR, np.random.default_rng(6)).render(n, _conditions(2400.0))

    def low_share(x):
        spec = np.abs(np.fft.rfft(x.astype(np.float64))) ** 2
        cut = int(500 / (SR / 2) * (spec.size - 1))
        return spec[:cut].sum() / spec.sum()

    assert low_share(hf) > low_share(uhf)


def test_interference_layer_loads_the_channel():
    cond_clean = _conditions(145.5)
    cond_hit = _conditions(145.5, interference=1.0)
    clean = NoiseTexture(SR, np.random.default_rng(2)).render(SR, cond_clean)
    hit = NoiseTexture(SR, np.random.default_rng(2)).render(SR, cond_hit)
    assert rms(hit) > rms(clean)


def test_jam_layer_is_strongly_modulated():
    """The jammer warble has far more envelope movement than the plain floor."""
    cond_clean = _conditions(145.5)
    cond_jam = _conditions(145.5, jammed=True)

    def env_var(x):
        env = np.abs(x.astype(np.float64))
        # ~2 ms smoothing: short enough to resolve the 90 Hz warble.
        k = SR // 500
        env = np.convolve(env, np.ones(k) / k, mode="valid")
        return env.std() / env.mean()

    clean = NoiseTexture(SR, np.random.default_rng(4)).render(SR, cond_clean)
    jam = NoiseTexture(SR, np.random.default_rng(4)).render(SR, cond_jam)
    assert env_var(jam) > 1.8 * env_var(clean)


# --- engine integration ------------------------------------------------------ #


def test_engine_reuses_texture_per_net_and_prunes():
    eng = DspEngine(sample_rate=SR)
    cond = _conditions(14.25)
    t1 = eng._texture(cond, None)
    t2 = eng._texture(cond, None)
    assert t1 is t2  # same net -> same continuous texture
    other = eng._texture(_conditions(145.5), None)
    assert other is not t1


def test_engine_seeded_rng_stays_deterministic():
    eng = DspEngine(sample_rate=SR)
    cond = _conditions(14.25)
    a = eng.render_idle_noise(FRAME, cond, rng=np.random.default_rng(9))
    b = eng.render_idle_noise(FRAME, cond, rng=np.random.default_rng(9))
    assert np.array_equal(a, b)


def test_idle_noise_louder_with_interference():
    eng = DspEngine(sample_rate=SR)
    rng = np.random.default_rng(8)
    quiet = eng.render_idle_noise(SR, _conditions(145.5), rng=rng)
    loud = eng.render_idle_noise(SR, _conditions(145.5, interference=1.0),
                                 rng=np.random.default_rng(8))
    assert rms(loud) > rms(quiet)


def test_clear_render_degrades_with_interference():
    """Acceptance: induced interference buries the voice, pushing trainees to
    change frequency."""
    from pivot.core.crypto import Reception
    from pivot.dsp.engine import render_reception

    t = np.arange(SR) / SR
    voice = (0.5 * np.sin(2 * np.pi * 440 * t) * np.clip(np.sin(2 * np.pi * 3 * t), 0, 1)).astype(
        np.float32
    )

    def corr(x):
        a = x - x.mean()
        b = voice - voice.mean()
        return float(np.sum(a * b) / np.sqrt(np.sum(a * a) * np.sum(b * b)))

    clean = render_reception(Reception.CLEAR, voice, _conditions(145.5), SR,
                             rng=np.random.default_rng(1))
    hit = render_reception(Reception.CLEAR, voice, _conditions(145.5, interference=1.0), SR,
                           rng=np.random.default_rng(1))
    assert corr(clean) > corr(hit)


def _voiced(seconds=2.0):
    t = np.arange(int(seconds * SR)) / SR
    return (0.5 * np.sin(2 * np.pi * 440 * t) * np.clip(np.sin(2 * np.pi * 3 * t), 0, 1)).astype(
        np.float32
    )


def _abs_corr(x, voice):
    a = x[: voice.size] - x[: voice.size].mean()
    b = voice - voice.mean()
    return abs(float(np.sum(a * b) / np.sqrt(np.sum(a * a) * np.sum(b * b))))


def test_jamming_buries_the_voice():
    """Acceptance: with jamming on, the full-strength voice is masked by
    competing noise to the point of being essentially inaudible — not a still-
    clean layer sitting quietly over the hash (spec §4.2)."""
    from pivot.core.crypto import Reception
    from pivot.dsp.engine import render_reception

    voice = _voiced()
    clean = render_reception(Reception.CLEAR, voice, _conditions(145.5), SR,
                             rng=np.random.default_rng(1))
    jammed = render_reception(Reception.CLEAR, voice, _conditions(145.5, jammed=True), SR,
                              rng=np.random.default_rng(1))
    hit = render_reception(Reception.CLEAR, voice, _conditions(145.5, interference=1.0), SR,
                           rng=np.random.default_rng(1))

    # The clean channel carries the voice; the jammed one barely correlates.
    assert _abs_corr(clean, voice) > 0.4
    assert _abs_corr(jammed, voice) < 0.1
    # Jamming buries the voice at least as hard as the strongest interference.
    assert _abs_corr(jammed, voice) <= _abs_corr(hit, voice)


def test_masking_is_competing_noise_not_a_louder_blast():
    """Burying the voice must come from competing noise, not from cranking the
    gain: the receiver's AGC keeps a jammed render near the clean render's
    loudness rather than an ear-splitting wall many times louder (spec §4.1.1)."""
    from pivot.core.crypto import Reception
    from pivot.dsp.engine import render_reception

    voice = _voiced()
    clean = render_reception(Reception.CLEAR, voice, _conditions(145.5), SR,
                             rng=np.random.default_rng(2))
    jammed = render_reception(Reception.CLEAR, voice, _conditions(145.5, jammed=True), SR,
                              rng=np.random.default_rng(2))
    assert rms(jammed) <= 2.0 * rms(clean)


def test_add_noise_for_snr_agc_holds_output_level():
    """``add_noise_for_snr`` levels the combined stream to the signal's own RMS:
    a deeply negative SNR piles on masking noise without the output ever growing
    louder than the signal it carries, and a clean channel is left untouched."""
    from pivot.dsp.filters import rms as _rms
    from pivot.dsp.noise import add_noise_for_snr, white_noise

    voice = 0.3 * white_noise(SR, np.random.default_rng(0))  # unit-RMS -> sig ~0.3
    noise = white_noise(SR, np.random.default_rng(1))
    sig = _rms(voice)
    for snr_db in (40.0, 0.0, -20.0, -40.0):
        assert _rms(add_noise_for_snr(voice, noise, snr_db)) <= sig * 1.05
    # A clean (high-SNR) channel comes back at essentially the signal's level.
    assert _rms(add_noise_for_snr(voice, noise, 40.0)) == pytest.approx(sig, rel=0.05)

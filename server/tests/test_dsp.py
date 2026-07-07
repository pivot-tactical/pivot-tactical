"""Tests for the DSP engine (spec §4).

These assert *behavioural* properties (length preservation, boundedness,
determinism) and the spec's defining audio characteristics: noise worsens toward
low HF (acceptance #4), and the encrypted hash is unintelligible yet follows
speech cadence (acceptance #10).
"""

import numpy as np
import pytest

from pivot.core.bands import BandProfile
from pivot.core.crypto import Reception
from pivot.dsp.engine import DspEngine, render_reception
from pivot.dsp.hash_gen import encrypted_hash, envelope_follower
from pivot.dsp.noise import band_noise, pink_noise, white_noise
from pivot.dsp.tone import crypto_sync_tone

SR = 16_000


def speech_like(seconds=1.0, sr=SR, seed=0):
    """A voiced signal with syllable-rate amplitude cadence (a stand-in voice)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    # Formant-ish tones.
    voice = (
        np.sin(2 * np.pi * 220 * t)
        + 0.5 * np.sin(2 * np.pi * 600 * t)
        + 0.3 * np.sin(2 * np.pi * 1500 * t)
    )
    # Syllable cadence at ~4 Hz with pauses.
    cadence = np.clip(np.sin(2 * np.pi * 4 * t), 0, 1) ** 0.5
    voice = voice * cadence + 0.01 * rng.standard_normal(t.size)
    return (voice / np.max(np.abs(voice))).astype(np.float32)


def norm_corr(a, b):
    a = a[: min(len(a), len(b))].astype(np.float64)
    b = b[: min(len(a), len(b))].astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
    return float(np.sum(a * b) / denom) if denom > 1e-9 else 0.0


# --- noise generators ------------------------------------------------------ #


def test_noise_generators_unit_rms_and_length():
    rng = np.random.default_rng(1)
    for gen in (white_noise(2048, rng), pink_noise(2048, rng), band_noise(2048, SR, 0.5, rng)):
        assert gen.shape == (2048,)
        assert np.isfinite(gen).all()
        assert abs(float(np.sqrt(np.mean(gen**2))) - 1.0) < 0.05


def test_pink_noise_is_lower_frequency_weighted_than_white():
    rng = np.random.default_rng(2)
    n = 16384
    pink = np.abs(np.fft.rfft(pink_noise(n, rng)))
    white = np.abs(np.fft.rfft(white_noise(n, rng)))
    # Ratio of low-band to high-band energy is higher for pink.
    lo = slice(1, n // 16)
    hi = slice(n // 4, n // 2)
    assert (pink[lo].mean() / pink[hi].mean()) > (white[lo].mean() / white[hi].mean())


# --- clear render ---------------------------------------------------------- #


def test_clear_render_preserves_length_and_bounds():
    voice = speech_like()
    profile = BandProfile()
    out = render_reception(Reception.CLEAR, voice, profile.conditions_at(145e6), SR)
    assert out.shape == voice.shape
    assert np.isfinite(out).all()
    assert np.max(np.abs(out)) <= 1.0 + 1e-5


def test_render_is_deterministic_with_seed():
    voice = speech_like()
    cond = BandProfile().conditions_at(14e6)
    a = render_reception(Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(42))
    b = render_reception(Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(42))
    assert np.array_equal(a, b)


def test_uhf_cleaner_than_low_hf():
    """Acceptance #4: UHF render tracks the clean voice better than low HF."""
    voice = speech_like()
    profile = BandProfile()
    uhf = render_reception(
        Reception.CLEAR, voice, profile.conditions_at(440e6), SR, rng=np.random.default_rng(7)
    )
    low_hf = render_reception(
        Reception.CLEAR, voice, profile.conditions_at(2e6), SR, rng=np.random.default_rng(7)
    )
    assert norm_corr(uhf, voice) > norm_corr(low_hf, voice)


# --- encrypted hash -------------------------------------------------------- #


def test_envelope_follower_tracks_cadence():
    voice = speech_like()
    env = envelope_follower(voice, SR)
    assert env.shape == voice.shape
    assert 0.0 <= env.min() and env.max() <= 1.0 + 1e-6
    # The envelope correlates with the rectified voice it was derived from.
    assert norm_corr(env, np.abs(voice)) > 0.5


def test_hash_is_unintelligible_but_follows_cadence():
    """Acceptance #10: cypher→plain garble follows cadence, not content."""
    voice = speech_like()
    rng = np.random.default_rng(11)
    hashed = encrypted_hash(voice, SR, rng)
    # Waveform correlation with the source is near zero (content destroyed)...
    assert abs(norm_corr(hashed, voice)) < 0.1
    # ...but the loudness envelope still tracks the speech cadence.
    assert norm_corr(envelope_follower(hashed, SR), envelope_follower(voice, SR)) > 0.4


def test_hash_render_less_intelligible_than_clear():
    voice = speech_like()
    cond = BandProfile().conditions_at(145e6)
    clear = render_reception(Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(5))
    hashed = render_reception(Reception.HASH, voice, cond, SR, rng=np.random.default_rng(5))
    assert norm_corr(clear, voice) > norm_corr(hashed, voice)


# --- collisions ------------------------------------------------------------ #


def test_plain_collision_mixes_voices():
    a = speech_like(seed=1)
    b = speech_like(seed=2)
    cond = BandProfile().conditions_at(145e6)
    engine = DspEngine(SR)
    out = engine.render(
        Reception.PLAIN_COLLISION, voices=[a, b], conditions=cond, rng=np.random.default_rng(0)
    )
    assert out.shape == a.shape
    # The mix correlates with neither source as strongly as a clean render would.
    assert norm_corr(out, a) < 0.9 and norm_corr(out, b) < 0.9


def test_crypto_jam_has_requested_length_and_is_noise():
    voice = speech_like()
    cond = BandProfile().conditions_at(14e6)
    engine = DspEngine(SR)
    out = engine.render(Reception.CRYPTO_JAM, voice, conditions=cond, rng=np.random.default_rng(0))
    assert out.shape == voice.shape
    assert abs(norm_corr(out, voice)) < 0.1


def test_silence_render_is_zeros():
    voice = speech_like()
    cond = BandProfile().conditions_at(14e6)
    out = DspEngine(SR).render(Reception.SILENCE, voice, conditions=cond)
    assert np.array_equal(out, np.zeros_like(voice))


def test_with_transients_extends_buffer():
    voice = speech_like(seconds=0.25)
    cond = BandProfile().conditions_at(2e6)
    plain = render_reception(Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(0))
    wrapped = render_reception(
        Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(0), with_transients=True
    )
    assert wrapped.size > plain.size  # click + tail added


# --- crypto sync tone ------------------------------------------------------ #


def test_crypto_sync_tone_duration():
    tone = crypto_sync_tone(SR, preset="ky57")
    assert abs(tone.size / SR - 0.30) < 0.01
    assert np.max(np.abs(tone)) <= 1.0


@pytest.mark.parametrize("preset", ["ky57", "single", "sweep"])
def test_crypto_sync_tone_presets(preset):
    tone = crypto_sync_tone(SR, preset=preset)
    assert tone.size > 0 and np.isfinite(tone).all()


def test_soft_clip_bounds_and_linearity():
    from pivot.dsp.filters import soft_clip

    # Test extreme values are soft-clipped within [-1, 1] bounds
    x = np.array([-10.0, -2.0, -1.0, 0.0, 1.0, 2.0, 10.0], dtype=np.float32)
    y = soft_clip(x)

    assert y.dtype == np.float32
    assert np.all(y >= -1.0)
    assert np.all(y <= 1.0)
    assert np.isclose(y[0], -1.0, atol=1e-4)
    assert np.isclose(y[-1], 1.0, atol=1e-4)

    # Test small signals remain largely unmodified (linear region)
    x_small = np.array([-0.1, 0.0, 0.1], dtype=np.float32)
    y_small = soft_clip(x_small)
    assert np.allclose(x_small, y_small, atol=0.01)

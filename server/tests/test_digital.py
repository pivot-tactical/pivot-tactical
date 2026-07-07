"""MELP-flavoured digital voice for Cypher mode (AN/PRC-160 baseline, §3.4.1).

Asserts the three defining behaviours: decoded digital voice is *cleaner* than
analog (no channel-noise bed), it keeps decoding a few dB below where analog
copy has gone rough ("cuts through"), and past the digital cliff it fails
digitally — dropouts and pitched-up garble over silence, near-total loss under
jamming — instead of sinking into noise.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from pivot.core.bands import BandProfile
from pivot.core.crypto import RadioMode, Reception, single_reception
from pivot.dsp.digital import DigitalVoice
from pivot.dsp.engine import DspEngine, render_reception
from pivot.dsp.filters import lowpass, rms

SR = 16_000
FRAME = 320  # 20 ms


def _cadenced_voice(seconds: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """A formant-ish voice with syllable cadence; returns (voice, pause_mask)."""
    t = np.arange(int(seconds * SR)) / SR
    cadence = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, 1) ** 0.6
    v = (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 600 * t)
         + 0.3 * np.sin(2 * np.pi * 1500 * t)) * cadence
    v = (v / np.max(np.abs(v))).astype(np.float32)
    return v, cadence < 0.01


def _conditions(mhz: float = 145.5, **scenario):
    profile = BandProfile()
    if scenario:
        profile.set_net_scenario(mhz * 1e6, **scenario)
    return profile.conditions_at(mhz * 1e6)


def _env_corr(x: np.ndarray, ref: np.ndarray) -> float:
    """Envelope correlation — phase-insensitive intelligibility proxy."""
    n = min(x.size, ref.size)
    a = lowpass(np.abs(x[:n].astype(np.float64)).astype(np.float32), 40.0, SR)
    b = lowpass(np.abs(ref[:n].astype(np.float64)).astype(np.float32), 40.0, SR)
    a, b = a - a.mean(), b - b.mean()
    d = np.sqrt(np.sum(a * a) * np.sum(b * b))
    return abs(float(np.sum(a * b) / d)) if d > 1e-9 else 0.0


def _quiet_frame_fraction(x: np.ndarray, thresh: float = 0.01) -> float:
    energies = [rms(x[i * FRAME:(i + 1) * FRAME]) for i in range(x.size // FRAME)]
    return float(np.mean(np.array(energies) < thresh))


# --- reception matrix ------------------------------------------------------- #


def test_cypher_to_cypher_is_digital_reception():
    assert single_reception(RadioMode.CYPHER, RadioMode.CYPHER) is Reception.DIGITAL
    assert single_reception(RadioMode.PLAIN, RadioMode.CYPHER) is Reception.CLEAR
    assert single_reception(RadioMode.CYPHER, RadioMode.PLAIN) is Reception.HASH


# --- basic render contract --------------------------------------------------- #


def test_digital_render_length_bounds_and_determinism():
    voice, _ = _cadenced_voice(1.0)
    cond = _conditions()
    a = render_reception(Reception.DIGITAL, voice, cond, SR, rng=np.random.default_rng(3))
    b = render_reception(Reception.DIGITAL, voice, cond, SR, rng=np.random.default_rng(3))
    assert a.shape == voice.shape
    assert np.isfinite(a).all()
    assert float(np.max(np.abs(a))) <= 1.0 + 1e-5
    assert np.array_equal(a, b)


def test_digital_voice_is_narrowband():
    """MELP is a narrowband coder: content above the passband doesn't survive."""
    t = np.arange(SR) / SR
    tone_5k = (0.5 * np.sin(2 * np.pi * 5000 * t)).astype(np.float32)
    out = render_reception(Reception.DIGITAL, tone_5k, _conditions(), SR,
                           rng=np.random.default_rng(2))
    assert rms(out) < 0.1 * rms(tone_5k)


# --- cleaner + cuts through --------------------------------------------------- #


def test_digital_is_cleaner_than_analog_between_words():
    """The defining 'cleaner' property: no channel noise rides with the voice,
    so the floor in speech pauses is far below the analog render's hiss."""
    voice, pause = _cadenced_voice()
    cond = replace(_conditions(), snr_db=8.0)  # audibly noisy analog channel
    dig = render_reception(Reception.DIGITAL, voice, cond, SR, rng=np.random.default_rng(1))
    clr = render_reception(Reception.CLEAR, voice, cond, SR, rng=np.random.default_rng(1))
    assert rms(dig[pause]) < 0.5 * rms(clr[pause])


def test_digital_holds_clean_copy_on_a_noisy_channel():
    """'Cuts through': at an SNR where analog copy is rough, digital voice still
    decodes essentially clean (envelope tracks the speech like a clean channel)."""
    voice, _ = _cadenced_voice()
    noisy = replace(_conditions(), snr_db=8.0)
    dig = render_reception(Reception.DIGITAL, voice, noisy, SR, rng=np.random.default_rng(1))
    assert _env_corr(dig, voice) > 0.95


# --- the digital cliff --------------------------------------------------------- #


def test_digital_falls_off_a_cliff_not_a_slope():
    """A few dB spans the difference between clean copy and unusable garble."""
    voice, _ = _cadenced_voice()
    base = _conditions()
    good = render_reception(Reception.DIGITAL, voice, replace(base, snr_db=8.0), SR,
                            rng=np.random.default_rng(1))
    below = render_reception(Reception.DIGITAL, voice, replace(base, snr_db=-4.0), SR,
                             rng=np.random.default_rng(1))
    assert _env_corr(good, voice) > 0.95
    assert _env_corr(below, voice) < 0.6
    # Below the cliff the audio clips out: many frames are hard-muted.
    assert _quiet_frame_fraction(below) > 0.5
    assert _quiet_frame_fraction(good) < _quiet_frame_fraction(below)


def test_digital_failure_includes_garble_not_noise():
    """Past the cliff the decoder squawks and mutes; it does not hiss. The
    residue is *bursty* — mostly silence broken by squawks — unlike analog's
    continuous noise bed at the same SNR, which fills every frame."""
    voice, _ = _cadenced_voice()
    cond = replace(_conditions(), snr_db=-4.0)
    below = render_reception(Reception.DIGITAL, voice, cond, SR,
                             rng=np.random.default_rng(5))
    analog = render_reception(Reception.CLEAR, voice, cond, SR,
                              rng=np.random.default_rng(5))
    # Some audio still comes through (garble bursts / squawks)…
    assert rms(below) > 0.005
    # …but most of the time is hard silence, where analog is wall-to-wall noise.
    assert _quiet_frame_fraction(below) > 0.5
    assert _quiet_frame_fraction(analog) < 0.05


def test_jamming_defeats_digital_voice_too():
    """Under jamming essentially nothing decodes: near-silence with at most the
    odd squawk — cypher does not defeat the jammer."""
    voice, _ = _cadenced_voice()
    jammed = render_reception(Reception.DIGITAL, voice, _conditions(jammed=True), SR,
                              rng=np.random.default_rng(1))
    good = render_reception(Reception.DIGITAL, voice, _conditions(), SR,
                            rng=np.random.default_rng(1))
    assert _quiet_frame_fraction(jammed) > 0.7
    assert rms(jammed) < 0.5 * rms(good)


# --- engine/mixer/AAR integration ---------------------------------------------- #


def test_engine_keeps_per_net_digital_state():
    eng = DspEngine(sample_rate=SR)
    cond = _conditions(14.25)
    d1 = eng._digital_voice(cond, None)
    d2 = eng._digital_voice(cond, None)
    assert d1 is d2                      # same net -> continuous decoder state
    assert eng._digital_voice(_conditions(145.5), None) is not d1
    assert isinstance(eng._digital_voice(cond, np.random.default_rng(1)), DigitalVoice)


def test_mixer_renders_digital_reception():
    from pivot.audio.mixer import render_net_frame

    frame = (0.3 * np.sin(2 * np.pi * 440 * np.arange(FRAME) / SR)).astype(np.float32)
    rendered = render_net_frame(
        {"tx": frame}, _conditions(), {Reception.DIGITAL, Reception.HASH},
        DspEngine(SR), rng=np.random.default_rng(0),
    )
    assert set(rendered) == {Reception.DIGITAL, Reception.HASH}
    assert rendered[Reception.DIGITAL].shape == frame.shape


def test_aar_playback_reception_for_cypher_views():
    from pivot.audio.render import AarCryptoView, reception_for_playback

    assert reception_for_playback(RadioMode.CYPHER, AarCryptoView.CYPHER) is Reception.DIGITAL
    assert reception_for_playback(RadioMode.CYPHER, AarCryptoView.PLAIN) is Reception.HASH
    assert reception_for_playback(RadioMode.PLAIN, AarCryptoView.PLAIN) is Reception.CLEAR
    assert reception_for_playback(RadioMode.PLAIN, AarCryptoView.CYPHER) is Reception.CLEAR


def test_streaming_frames_match_engine_contract():
    """The live path renders 20 ms blocks through one per-net decoder; output
    stays finite, bounded and voice-levelled across many consecutive frames."""
    eng = DspEngine(sample_rate=SR)
    voice, _ = _cadenced_voice(2.0)
    cond = _conditions()
    outs = [eng.render_digital(voice[i:i + FRAME], cond)
            for i in range(0, FRAME * 80, FRAME)]
    stream = np.concatenate(outs)
    assert stream.size == FRAME * 80
    assert np.isfinite(stream).all()
    assert float(np.max(np.abs(stream))) <= 1.0 + 1e-5
    assert rms(stream) > 0.02  # voice actually comes through

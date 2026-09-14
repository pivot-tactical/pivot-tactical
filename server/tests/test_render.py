import io
import json
import numpy as np
import pytest
import soundfile as sf
from unittest.mock import MagicMock

from pivot.audio.render import (
    AarCryptoView,
    PlaybackMode,
    _conditions_for,
    _read,
    reception_for_playback,
    render_event,
    render_event_wav_bytes,
)
from pivot.core.bands import BandProfile
from pivot.core.crypto import RadioMode, Reception
from pivot.db.models import EventRow


def test_reception_for_playback():
    assert reception_for_playback(RadioMode.PLAIN, AarCryptoView.PLAIN) == Reception.CLEAR
    assert reception_for_playback(RadioMode.PLAIN, AarCryptoView.CYPHER) == Reception.CLEAR
    assert reception_for_playback(RadioMode.CYPHER, AarCryptoView.PLAIN) == Reception.HASH
    assert reception_for_playback(RadioMode.CYPHER, AarCryptoView.CYPHER) == Reception.DIGITAL


def test_conditions_for_valid_json():
    event = MagicMock(spec=EventRow)
    event.frequency = "14.250 MHz"
    full_dict = BandProfile().conditions_at(14_250_000).to_dict()
    event.dsp_profile_json = json.dumps(full_dict)
    cond = _conditions_for(event)
    assert cond.freq_hz == 14_250_000.0


def test_conditions_for_invalid_json_fallback():
    event = MagicMock(spec=EventRow)
    event.dsp_profile_json = "invalid json{"
    event.frequency = "14.250 MHz"
    cond = _conditions_for(event)
    assert cond is not None
    assert cond.freq_hz == 14_250_000.0


def test_conditions_for_empty_json_fallback():
    event = MagicMock(spec=EventRow)
    event.dsp_profile_json = "{}"
    event.frequency = "14.250 MHz"
    cond = _conditions_for(event)
    assert cond is not None
    assert cond.freq_hz == 14_250_000.0


def test_read_mono_and_stereo(tmp_path):
    sr = 16000
    # Mono audio file
    mono_data = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)
    mono_path = tmp_path / "mono.wav"
    sf.write(mono_path, mono_data, sr)

    read_mono, read_sr = _read(mono_path)
    assert read_sr == sr
    np.testing.assert_allclose(read_mono, mono_data, atol=1e-4)

    # Stereo audio file (2 channels)
    stereo_data = np.array([[0.2, 0.4], [-0.2, -0.4]], dtype=np.float32)
    stereo_path = tmp_path / "stereo.wav"
    sf.write(stereo_path, stereo_data, sr)

    read_stereo, read_sr = _read(stereo_path)
    assert read_sr == sr
    expected_stereo = np.array([0.3, -0.3], dtype=np.float32)
    np.testing.assert_allclose(read_stereo, expected_stereo, atol=1e-4)


def test_render_event_clean_and_dirty(tmp_path):
    sr = 16000
    tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(1600) / sr)).astype(np.float32)
    audio_path = tmp_path / "event.wav"
    sf.write(audio_path, tone, sr)

    event = MagicMock(spec=EventRow)
    event.audio_path = "event.wav"
    event.dsp_profile_json = json.dumps(BandProfile().conditions_at(14_250_000).to_dict())
    event.tx_mode = RadioMode.PLAIN
    event.frequency = "14.250 MHz"

    # Clean mode returns clean audio unchanged
    clean, clean_sr = render_event(event, tmp_path, mode=PlaybackMode.CLEAN)
    assert clean_sr == sr
    np.testing.assert_allclose(clean, tone, atol=1e-4)

    # Dirty mode applies DSP engine rendering
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    dirty1, dirty1_sr = render_event(
        event, tmp_path, mode=PlaybackMode.DIRTY, view=AarCryptoView.PLAIN, rng=rng1
    )
    dirty2, dirty2_sr = render_event(
        event, tmp_path, mode=PlaybackMode.DIRTY, view=AarCryptoView.PLAIN, rng=rng2
    )
    assert dirty1_sr == sr
    assert dirty1.size > 0
    np.testing.assert_allclose(dirty1, dirty2)


def test_render_event_tx_mode_string(tmp_path):
    sr = 16000
    tone = np.zeros(320, dtype=np.float32)
    audio_path = tmp_path / "string_mode.wav"
    sf.write(audio_path, tone, sr)

    event = MagicMock(spec=EventRow)
    event.audio_path = "string_mode.wav"
    event.dsp_profile_json = json.dumps({})
    event.tx_mode = "Cypher"  # string value rather than RadioMode enum
    event.frequency = "14.250 MHz"

    rendered, _ = render_event(
        event, tmp_path, mode=PlaybackMode.DIRTY, view=AarCryptoView.PLAIN
    )
    assert rendered.size > 0


def test_render_event_wav_bytes(tmp_path):
    sr = 16000
    tone = np.array([2.0, -2.0, 0.5, -0.5], dtype=np.float32)  # includes values outside [-1, 1]
    audio_path = tmp_path / "bytes_event.wav"
    sf.write(audio_path, tone, sr)

    event = MagicMock(spec=EventRow)
    event.audio_path = "bytes_event.wav"
    event.dsp_profile_json = "{}"
    event.tx_mode = RadioMode.PLAIN
    event.frequency = "14.250 MHz"

    wav_bytes = render_event_wav_bytes(
        event, tmp_path, mode=PlaybackMode.CLEAN
    )
    assert isinstance(wav_bytes, bytes)
    assert wav_bytes.startswith(b"RIFF")

    # Read back the WAV bytes and verify clipping to [-1, 1]
    buf = io.BytesIO(wav_bytes)
    data, read_sr = sf.read(buf, dtype="float32")
    assert read_sr == sr
    assert np.all(data >= -1.0)
    assert np.all(data <= 1.0)

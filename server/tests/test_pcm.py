import numpy as np

from pivot.audio.pcm import float32_to_pcm16, pcm16_to_float32


def test_pcm16_to_float32_empty():
    """Empty input returns an empty float32 array."""
    res = pcm16_to_float32(b"")
    assert res.size == 0
    assert res.dtype == np.float32


def test_pcm16_to_float32_values():
    """Valid PCM bytes are correctly converted to float32."""
    # 0, 32767 (max positive), -32768 (min negative)
    # Using little-endian format '<i2'
    data = np.array([0, 32767, -32768], dtype="<i2").tobytes()
    res = pcm16_to_float32(data)

    assert res.dtype == np.float32
    assert len(res) == 3
    assert res[0] == 0.0
    assert np.isclose(res[1], 32767.0 / 32768.0)
    assert np.isclose(res[2], -1.0)


def test_pcm16_to_float32_odd_length():
    """Odd-length input trims the last byte instead of failing."""
    data = np.array([0, 32767], dtype="<i2").tobytes()
    odd_data = data + b"\x00"

    res = pcm16_to_float32(odd_data)

    assert res.dtype == np.float32
    assert len(res) == 2
    assert res[0] == 0.0
    assert np.isclose(res[1], 32767.0 / 32768.0)


def test_float32_to_pcm16_empty():
    """Empty float32 array returns empty bytes."""
    res = float32_to_pcm16(np.array([], dtype=np.float32))
    assert res == b""


def test_float32_to_pcm16_values():
    """Float32 values are correctly converted to little-endian 16-bit PCM bytes."""
    samples = np.array([0.0, 1.0, -1.0], dtype=np.float32)
    res = float32_to_pcm16(samples)

    # 0 -> 0, 1.0 -> 32767, -1.0 -> -32767
    expected = np.array([0, 32767, -32767], dtype="<i2").tobytes()
    assert res == expected


def test_float32_to_pcm16_clipping():
    """Out-of-bounds float values are clipped to [-1.0, 1.0]."""
    samples = np.array([-2.0, 2.0, 1.5, -1.5], dtype=np.float32)
    res = float32_to_pcm16(samples)

    # Clip to [-1.0, 1.0], which maps to [-32767, 32767]
    expected = np.array([-32767, 32767, 32767, -32767], dtype="<i2").tobytes()
    assert res == expected

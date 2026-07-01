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


def test_float32_to_pcm16_mid_range():
    """Mid-range float32 values map correctly to PCM16."""
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    res_bytes = float32_to_pcm16(samples)
    res_array = np.frombuffer(res_bytes, dtype="<i2")

    expected = np.array([0, int(0.5 * 32767), int(-0.5 * 32767), 32767, -32767], dtype="<i2")
    np.testing.assert_array_equal(res_array, expected)


def test_float32_to_pcm16_clipping():
    """Out-of-bounds float values are clipped to [-1.0, 1.0]."""
    samples = np.array([-2.0, 2.0, 1.5, -1.5], dtype=np.float32)
    res = float32_to_pcm16(samples)

    # Clip to [-1.0, 1.0], which maps to [-32767, 32767]
    expected = np.array([-32767, 32767, 32767, -32767], dtype="<i2").tobytes()
    assert res == expected


def test_round_trip():
    """Round-trip PCM→float32→PCM stays within 1 LSB of the original."""
    original_pcm = b"\x00\x00\xff\x7f\x00\x80\x12\x34\xab\xcd"

    floats = pcm16_to_float32(original_pcm)
    round_trip_pcm = float32_to_pcm16(floats)

    orig_vals = np.frombuffer(original_pcm, dtype="<i2")
    rt_vals = np.frombuffer(round_trip_pcm, dtype="<i2")

    assert len(orig_vals) == len(rt_vals)
    diff = np.abs(orig_vals.astype(np.int32) - rt_vals.astype(np.int32))
    assert np.max(diff) <= 1


def test_pcm16_to_float32_known_bytes():
    """Verify decoding with an explicitly defined byte sequence."""
    # b"\x00\x00" -> 0
    # b"\xff\x7f" -> 32767
    # b"\x00\x80" -> -32768
    data = b"\x00\x00\xff\x7f\x00\x80"
    res = pcm16_to_float32(data)
    assert res.dtype == np.float32
    assert len(res) == 3
    assert res[0] == 0.0
    assert np.isclose(res[1], 32767.0 / 32768.0)
    assert np.isclose(res[2], -1.0)

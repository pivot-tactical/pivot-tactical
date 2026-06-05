"""PCM conversion between the wire format and float32 (spec §3.5.1, §6.3).

Audio is carried over the WebSocket as little-endian 16-bit signed PCM at the
recording sample rate (16 kHz mono). These helpers convert to/from the float32
``[-1, 1]`` arrays the DSP engine and recording layer use.
"""

from __future__ import annotations

import numpy as np


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Decode little-endian 16-bit PCM bytes into float32 in [-1, 1]."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    # Trim a stray odd byte rather than raising on a partial frame.
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Encode float32 samples into little-endian 16-bit PCM bytes."""
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()

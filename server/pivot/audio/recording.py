"""Per-station recording tap (spec §3.5.1).

Recording is per transmitting station and independent of what is heard on the
air: every keying captures that station's own clean source audio regardless of
collisions, wrong frequency, or crypto state. One discrete file per PTT event,
captured PRE-DSP as 16-bit mono WAV at 16 kHz, at
``/recordings/{session_id}/{event_id}.wav`` (§3.5.1).

The WAV is the single source of audio truth; AAR Dirty playback re-renders it
through the stored DSP profile rather than storing a second file (§3.6.3, §4.5).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from pivot.config import RECORDING_SAMPLE_RATE


def event_audio_path(recordings_dir: Path, session_id: str, event_id: str) -> Path:
    return recordings_dir / session_id / f"{event_id}.wav"


def relative_audio_path(session_id: str, event_id: str) -> str:
    """The relative path stored in the event row (spec §3.5.3)."""
    return f"{session_id}/{event_id}.wav"


def write_recording(
    path: Path,
    audio: np.ndarray,
    sample_rate: int = RECORDING_SAMPLE_RATE,
) -> Path:
    """Write a clean mono buffer to a 16-bit PCM WAV (§3.5.1)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mono = np.asarray(audio, dtype=np.float32).reshape(-1)
    mono = np.clip(mono, -1.0, 1.0)
    sf.write(str(path), mono, sample_rate, subtype="PCM_16")
    return path


def read_recording(path: Path) -> tuple[np.ndarray, int]:
    """Load a recording as float32 mono plus its sample rate."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), int(sr)


def duration_ms(audio: np.ndarray, sample_rate: int = RECORDING_SAMPLE_RATE) -> int:
    return int(round(1000 * len(audio) / sample_rate))

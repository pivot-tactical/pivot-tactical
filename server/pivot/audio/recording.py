"""Per-station recording tap (spec §3.5.1).

Recording is per transmitting station and independent of what is heard on the
air: every keying captures that station's own clean source audio regardless of
collisions, wrong frequency, or crypto state. One discrete file per PTT event,
captured PRE-DSP as 16-bit mono WAV at 16 kHz.

Files are named for humans, not machines: recordings land in a per-session
folder named ``{session-name}_{date-time-group}`` and each WAV is named
``{date-time-group}_{trainee}_{shortid}.wav`` so an instructor can find the
right clip in a file browser without the application. The date-time group is
the UTC instant in readable ``YYYY-MM-DD_HH-MM-SSZ`` form (sorts
chronologically); the short id is the first 8 characters of the event UUID,
kept only to guarantee uniqueness when two stations key within the same second.

    e.g. ``night-nav-ex_2026-07-11_14-00-00Z/2026-07-11_14-30-22Z_j-smith_a1b2c3d4.wav``

The path actually used to read a recording is always the one stored on the
event row (``audio_path``) — it is never reconstructed from ids — so older
recordings written under a previous naming scheme keep resolving unchanged.

The WAV is the single source of audio truth; AAR Dirty playback re-renders it
through the stored DSP profile rather than storing a second file (§3.6.3, §4.5).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import soundfile as sf

from pivot.config import RECORDING_SAMPLE_RATE
from pivot.core.timebase import parse_iso_utc

_SESSION_SLUG_MAX = 40
_TRAINEE_SLUG_MAX = 40


def _slugify(text: str | None, *, max_len: int, fallback: str) -> str:
    """Filesystem- and human-friendly slug: lowercase alphanumerics joined by
    single hyphens. Anything else (spaces, punctuation, path separators, dots)
    collapses to a hyphen, which also makes path traversal impossible."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or fallback


def _dtg(iso_timestamp: str) -> str:
    """Human-readable, chronologically sortable UTC date-time group for a
    filename: ``YYYY-MM-DD_HH-MM-SSZ``. Hyphens (not colons) keep it valid on
    Windows filesystems, and the fixed-width fields still sort lexically."""
    try:
        return parse_iso_utc(iso_timestamp).strftime("%Y-%m-%d_%H-%M-%SZ")
    except (ValueError, TypeError):
        return "0000-00-00_00-00-00Z"


def session_dir_name(session_name: str | None, session_started_at: str) -> str:
    """The per-session folder name: ``{session-name}_{date-time-group}``."""
    return f"{_slugify(session_name, max_len=_SESSION_SLUG_MAX, fallback='session')}_{_dtg(session_started_at)}"


def relative_audio_path(
    session_name: str | None,
    session_started_at: str,
    event_started_at: str,
    trainee_name: str | None,
    event_id: str,
) -> str:
    """The relative path stored in the event row (spec §3.5.3)."""
    trainee = _slugify(trainee_name, max_len=_TRAINEE_SLUG_MAX, fallback="unknown")
    filename = f"{_dtg(event_started_at)}_{trainee}_{event_id[:8]}.wav"
    return f"{session_dir_name(session_name, session_started_at)}/{filename}"


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

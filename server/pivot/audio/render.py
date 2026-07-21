"""AAR playback re-render (spec §3.6.3, §4.5).

A single source of audio truth: the stored clean WAV. The AAR offers two
independent, non-destructive playback toggles:

* **Clean / Dirty** — Clean plays the raw pre-DSP recording; Dirty streams it
  back through the *original* DSP profile in real time (§3.6.3).
* **Plain / Cypher (AAR listener)** — in Plain view a Cypher event renders as the
  encrypted hash; in Cypher view all events render as clear voice for review and
  grading (§3.6.3).

The crypto sync tone is never re-rendered (it was never broadcast, §4.5).
"""

from __future__ import annotations

import io
import json
from enum import StrEnum
from pathlib import Path

import numpy as np
import soundfile as sf

from pivot.core.bands import BandConditions
from pivot.core.crypto import RadioMode, Reception
from pivot.db.models import EventRow
from pivot.dsp.engine import DspEngine


class PlaybackMode(StrEnum):
    CLEAN = "clean"
    DIRTY = "dirty"


class AarCryptoView(StrEnum):
    PLAIN = "plain"   # cypher events render as hash (default, §3.6.3)
    CYPHER = "cypher"  # everything renders as clear voice for grading


def reception_for_playback(tx_mode: RadioMode, view: AarCryptoView) -> Reception:
    """Decide the render type for Dirty playback (spec §4.5 rules).

    A cypher transmission heard in Cypher view replays as the *digital* decode
    (MELP vocoder reconstruction, §3.4.1) — the same thing a cypher-capable set
    on the net heard live — not as analog voice through the band chain.
    """
    if tx_mode is RadioMode.CYPHER and view is AarCryptoView.PLAIN:
        return Reception.HASH
    if tx_mode is RadioMode.CYPHER:
        return Reception.DIGITAL
    return Reception.CLEAR


def render_event(
    event: EventRow,
    recordings_dir: Path,
    mode: PlaybackMode = PlaybackMode.CLEAN,
    view: AarCryptoView = AarCryptoView.PLAIN,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, int]:
    """Return ``(audio, sample_rate)`` for an event under the given AAR toggles."""
    path = recordings_dir / event.audio_path
    clean, sr = _read(path)

    if mode is PlaybackMode.CLEAN:
        return clean, sr

    # Dirty: re-render through the stored DSP profile.
    conditions = _conditions_for(event)
    tx_mode = event.tx_mode if isinstance(event.tx_mode, RadioMode) else RadioMode(event.tx_mode)
    reception = reception_for_playback(tx_mode, view)
    engine = DspEngine(sample_rate=sr)
    rendered = engine.render(
        reception, clean, conditions=conditions, rng=rng, with_transients=True
    )
    return rendered, sr


def render_event_wav_bytes(
    event: EventRow,
    recordings_dir: Path,
    mode: PlaybackMode = PlaybackMode.CLEAN,
    view: AarCryptoView = AarCryptoView.PLAIN,
    rng: np.random.Generator | None = None,
) -> bytes:
    """Render and encode to in-memory WAV bytes for HTTP streaming (§6.1)."""
    audio, sr = render_event(event, recordings_dir, mode, view, rng)
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), sr, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def _conditions_for(event: EventRow) -> BandConditions:
    """Reconstruct conditions from the stored profile, tolerating older rows."""
    try:
        data = json.loads(event.dsp_profile_json)
        if data:
            return BandConditions.from_dict(data)
    except (ValueError, KeyError, TypeError):
        pass
    # Fallback: derive from frequency if the stored profile is missing/partial.
    from pivot.core.bands import BandProfile, parse_frequency

    return BandProfile().conditions_at(parse_frequency(event.frequency))


def _read(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32), int(sr)


__all__ = [
    "PlaybackMode",
    "AarCryptoView",
    "reception_for_playback",
    "render_event",
    "render_event_wav_bytes",
]

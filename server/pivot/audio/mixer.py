"""Per-listener render grouping and frame mixing (spec §6.3, Appendix A).

The defining constraint (Appendix A.1): a single transmission must be heard
differently by different receivers on the same frequency. The server is the one
audio endpoint and renders exactly what each listener should hear.

This module is the *pure* core of that render loop — no aiortc, no I/O — so it is
unit-testable:

* :func:`group_renders` implements the **fan-out optimisation** (A.4): listeners
  whose :class:`~pivot.core.crypto.Reception` is identical share one encoded
  stream, so a frequency has only a few distinct renders at any instant.
* :func:`render_net_frame` produces one audio frame per distinct render from the
  active transmitters' clean frames, using the DSP engine.

:mod:`pivot.audio.router` wraps this with WebRTC peer connections and Opus
encode.
"""

from __future__ import annotations

import numpy as np

from pivot.core.bands import BandConditions
from pivot.core.crypto import Reception
from pivot.dsp.engine import DspEngine


def group_renders(render_map: dict[str, Reception]) -> dict[Reception, list[str]]:
    """Group listener radio-ids by the render they need (fan-out, A.4).

    ``SILENCE`` listeners are returned too (so callers can stop their streams),
    but they require no render work.
    """
    groups: dict[Reception, list[str]] = {}
    for radio_id, reception in render_map.items():
        groups.setdefault(reception, []).append(radio_id)
    return groups


def distinct_renders(render_map: dict[str, Reception]) -> set[Reception]:
    """The set of renders that must actually be computed for a net."""
    return {r for r in render_map.values() if r is not Reception.SILENCE}


def render_net_frame(
    active_tx_frames: dict[str, np.ndarray],
    conditions: BandConditions,
    receptions_needed: set[Reception],
    engine: DspEngine,
    rng: np.random.Generator | None = None,
) -> dict[Reception, np.ndarray]:
    """Render one output frame per needed reception for a frequency.

    ``active_tx_frames`` maps each *on-air* station's radio-id to its clean
    (pre-DSP) frame for this block. ``CLEAR``/``HASH`` derive from the single
    decodable transmitter; collisions combine all of them.
    """
    out: dict[Reception, np.ndarray] = {}
    frames = list(active_tx_frames.values())
    if not frames:
        return out

    n = max(len(f) for f in frames)
    single = frames[0]

    for reception in receptions_needed:
        match reception:
            case Reception.CLEAR:
                out[reception] = engine.render_clear(single, conditions, rng)
            case Reception.HASH:
                out[reception] = engine.render_hash(single, conditions, rng)
            case Reception.PLAIN_COLLISION:
                out[reception] = engine.render_plain_collision(frames, conditions, rng)
            case Reception.CRYPTO_JAM:
                out[reception] = engine.render_crypto_jam(n, conditions, rng)
            case _:
                pass  # SILENCE needs no render.
    return out

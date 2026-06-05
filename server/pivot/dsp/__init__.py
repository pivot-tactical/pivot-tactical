"""DSP & audio processing engine (spec §4).

A single DSP chain whose parameters vary continuously with the tuned frequency
(via :class:`pivot.core.bands.BandConditions`). The engine renders a clean,
pre-DSP voice buffer into what a particular listener hears:

* clear voice through the frequency-dependent chain (noise, fading, squelch), or
* the encrypted hash (a Plain receiver hearing a Cypher transmission), or
* the collision renders (plain doubling, cypher jam).

Everything operates on ``float32`` numpy arrays in ``[-1, 1]`` and is seedable
(pass an ``rng``) so renders are deterministic under test. The same engine powers
both the live per-listener router (§6.3) and the AAR Dirty-playback re-render
(§4.5) — a single source of audio truth.

Implemented entirely on numpy + scipy (BSD), keeping the audio path fully
permissive (spec §13.5).
"""

from pivot.dsp.engine import DspEngine, render_reception
from pivot.dsp.hash_gen import encrypted_hash, envelope_follower
from pivot.dsp.tone import crypto_sync_tone

__all__ = [
    "DspEngine",
    "render_reception",
    "encrypted_hash",
    "envelope_follower",
    "crypto_sync_tone",
]

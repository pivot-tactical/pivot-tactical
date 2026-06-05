"""Plain/Cypher reception matrix and simplex collision resolution (spec §3.4).

Two independent concepts live here:

1. **What a listener hears right now** — :func:`resolve_listener`, built on the
   2x2 reception matrix (§3.4.1) and the simplex collision rules (§3.4.6). The
   audio router calls this once per (frequency, listener-render-class) to decide
   which render to send.

2. **How a finished transmission is logged** — :func:`classify_audibility`,
   which produces the stored ``audibility`` event field (§3.5.3) describing the
   on-air outcome of that station's keying.

The model is *permissive cypher receive*: a Cypher-mode receiver decodes both
Cypher and Plain transmissions (§3.4.1). The only combination that does **not**
yield clear voice is Cypher transmitted into a Plain receiver, which renders as
the encrypted hash (§3.4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RadioMode(str, Enum):
    """Per-radio crypto mode (spec §3.4). Persists across retuning, never
    auto-resets (§3.4.4)."""

    PLAIN = "Plain"
    CYPHER = "Cypher"

    def toggled(self) -> "RadioMode":
        return RadioMode.CYPHER if self is RadioMode.PLAIN else RadioMode.PLAIN


class Reception(str, Enum):
    """What a listener hears for a given moment of audio."""

    SILENCE = "silence"                 # nothing on frequency, or listener is keyed
    CLEAR = "clear"                     # clean voice through the band DSP chain
    HASH = "hash"                       # encrypted garble (Cypher TX → Plain RX)
    PLAIN_COLLISION = "plain_collision" # two+ plain voices overlapping/chaotic
    CRYPTO_JAM = "crypto_jam"           # cypher collision jam during overlap


class Audibility(str, Enum):
    """Stored per-transmission on-air outcome (spec §3.5.3)."""

    HEARD = "Heard"
    UNHEARD_NO_LISTENERS = "Unheard-no-listeners"
    PLAIN_COLLISION = "Plain-collision"
    CYPHER_SUPPRESSED = "Cypher-suppressed"


class SyncStatus(str, Enum):
    """Crypto-sync outcome for a keying (spec §3.5.3)."""

    COMPLETED = "Completed"
    ABORTED = "Aborted"  # PTT released during the crypto sync delay (§3.2.3)


# --------------------------------------------------------------------------- #
# 3.4.1 Reception matrix (single transmitter)
# --------------------------------------------------------------------------- #


def single_reception(tx_mode: RadioMode, rx_mode: RadioMode) -> Reception:
    """The 2x2 reception matrix (spec §3.4.1) for one TX heard by one RX.

    +-----------+-----------+----------------------------------------+
    | TX mode   | RX mode   | Result                                 |
    +===========+===========+========================================+
    | Plain     | Plain     | clear voice                            |
    | Plain     | Cypher    | clear voice (cypher set decodes plain) |
    | Cypher    | Cypher    | clear voice (decoded)                  |
    | Cypher    | Plain     | encrypted hash — undecodable           |
    +-----------+-----------+----------------------------------------+
    """
    if tx_mode is RadioMode.CYPHER and rx_mode is RadioMode.PLAIN:
        return Reception.HASH
    return Reception.CLEAR


# --------------------------------------------------------------------------- #
# 3.4.6 Simplex operation & collisions (multiple transmitters)
# --------------------------------------------------------------------------- #


def collision_reception(tx_modes: list[RadioMode]) -> Reception:
    """Resolve what receivers hear when 2+ stations key the same frequency.

    Per §3.4.6: a *plain* collision (all transmitters in Plain) is heard as both
    voices overlapping and chaotic; any *cypher* involvement turns the overlap
    into a crypto-jam heard by **all** receiving stations for the overlap.
    """
    if any(m is RadioMode.CYPHER for m in tx_modes):
        return Reception.CRYPTO_JAM
    return Reception.PLAIN_COLLISION


def resolve_listener(
    rx_mode: RadioMode,
    active_tx_modes: list[RadioMode],
    listener_is_transmitting: bool = False,
) -> Reception:
    """Decide what a single listener hears given the stations keyed on its freq.

    ``active_tx_modes`` are the modes of every *other* station currently keyed on
    the listener's frequency (exclude the listener itself). A keyed listener is
    half-duplex and hears nothing (§3.4.6).
    """
    if listener_is_transmitting:
        return Reception.SILENCE
    if not active_tx_modes:
        return Reception.SILENCE
    if len(active_tx_modes) == 1:
        return single_reception(active_tx_modes[0], rx_mode)
    return collision_reception(active_tx_modes)


# --------------------------------------------------------------------------- #
# 3.5.3 Audibility classification (per completed transmission)
# --------------------------------------------------------------------------- #


@dataclass
class TxOutcome:
    """Everything needed to classify one finished transmission's audibility.

    Gathered by the session/audio router over the lifetime of the keying.

    Policy notes (matching spec §3.5.1, §3.6.2 "who was actually copied"):

    * ``had_listener`` is True if at least one *other* radio was tuned to the
      same frequency and in receive (not keyed) for any part of this TX. Whether
      those listeners decoded clear voice or heard the encrypted hash is a
      crypto-matrix/playback concern, surfaced separately via the TX-mode icon —
      it does not change audibility.
    * In a cypher collision the first keyer is "Heard" (copied before the
      overlap) and every later keyer is "Cypher-suppressed" (its content never
      reached a receiver intelligibly) — spec §3.4.6, §3.5.1.
    """

    self_mode: RadioMode
    had_listener: bool
    overlapped_modes: list[RadioMode] = field(default_factory=list)
    keyed_first: bool = True


def classify_audibility(outcome: TxOutcome) -> Audibility:
    """Compute the stored ``audibility`` for a finished transmission (§3.5.3)."""
    if not outcome.had_listener:
        return Audibility.UNHEARD_NO_LISTENERS
    if not outcome.overlapped_modes:
        return Audibility.HEARD

    cypher_involved = outcome.self_mode is RadioMode.CYPHER or any(
        m is RadioMode.CYPHER for m in outcome.overlapped_modes
    )
    if cypher_involved:
        return Audibility.HEARD if outcome.keyed_first else Audibility.CYPHER_SUPPRESSED
    return Audibility.PLAIN_COLLISION

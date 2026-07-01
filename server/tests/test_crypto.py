"""Tests for the reception matrix and collision rules (spec §3.4)."""

from pivot.core.crypto import (
    Audibility,
    RadioMode,
    Reception,
    SyncStatus,
    TxOutcome,
    classify_audibility,
    collision_reception,
    resolve_listener,
    single_reception,
)

P = RadioMode.PLAIN
C = RadioMode.CYPHER


# --- 3.4.1 reception matrix (acceptance criteria #7-#10) ------------------- #


def test_plain_to_plain_is_clear():
    assert single_reception(P, P) is Reception.CLEAR


def test_plain_to_cypher_is_clear_permissive():
    # Cypher set decodes plain (permissive receive).
    assert single_reception(P, C) is Reception.CLEAR


def test_cypher_to_cypher_is_clear():
    assert single_reception(C, C) is Reception.CLEAR


def test_cypher_to_plain_is_hash():
    # The only non-clear single-TX combination.
    assert single_reception(C, P) is Reception.HASH


# --- 3.4.6 collisions ------------------------------------------------------ #


def test_plain_collision_is_overlap():
    assert collision_reception([P, P]) is Reception.PLAIN_COLLISION


def test_any_cypher_collision_is_jam():
    assert collision_reception([C, C]) is Reception.CRYPTO_JAM
    assert collision_reception([P, C]) is Reception.CRYPTO_JAM
    assert collision_reception([C, P, P]) is Reception.CRYPTO_JAM


def test_keyed_listener_hears_nothing():
    # Half-duplex simplex: a transmitting station is deaf (§3.4.6).
    assert resolve_listener(P, [P], listener_is_transmitting=True) is Reception.SILENCE


def test_listener_with_no_transmitters_hears_silence():
    assert resolve_listener(P, []) is Reception.SILENCE


def test_resolve_single_transmitter_uses_matrix():
    assert resolve_listener(P, [C]) is Reception.HASH
    assert resolve_listener(C, [C]) is Reception.CLEAR
    assert resolve_listener(C, [P]) is Reception.CLEAR


def test_resolve_collision():
    assert resolve_listener(P, [P, P]) is Reception.PLAIN_COLLISION
    assert resolve_listener(C, [C, P]) is Reception.CRYPTO_JAM


# --- 3.5.3 audibility classification --------------------------------------- #


def test_no_listener_is_unheard():
    out = TxOutcome(self_mode=P, had_listener=False)
    assert classify_audibility(out) is Audibility.UNHEARD_NO_LISTENERS


def test_clean_transmission_is_heard():
    out = TxOutcome(self_mode=P, had_listener=True)
    assert classify_audibility(out) is Audibility.HEARD


def test_plain_collision_audibility():
    out = TxOutcome(self_mode=P, had_listener=True, overlapped_modes=[P])
    assert classify_audibility(out) is Audibility.PLAIN_COLLISION


def test_cypher_collision_first_keyer_heard():
    out = TxOutcome(self_mode=C, had_listener=True, overlapped_modes=[C], keyed_first=True)
    assert classify_audibility(out) is Audibility.HEARD


def test_cypher_collision_later_keyer_suppressed():
    # The "suppressed second station in a cypher collision" (§3.5.1).
    out = TxOutcome(self_mode=C, had_listener=True, overlapped_modes=[C], keyed_first=False)
    assert classify_audibility(out) is Audibility.CYPHER_SUPPRESSED


def test_mixed_collision_with_cypher_suppresses_later():
    out = TxOutcome(self_mode=P, had_listener=True, overlapped_modes=[C], keyed_first=False)
    assert classify_audibility(out) is Audibility.CYPHER_SUPPRESSED


def test_mode_toggle():
    assert P.toggled() is C
    assert C.toggled() is P


def test_sync_status_values():
    assert SyncStatus.COMPLETED.value == "Completed"
    assert SyncStatus.ABORTED.value == "Aborted"

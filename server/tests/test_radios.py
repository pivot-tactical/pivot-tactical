"""Tests for the radio registry / emergent-net frequency map (spec §3.1.2, §6.3)."""

import pytest

from pivot.core.crypto import RadioMode, Reception
from pivot.core.radios import (
    INSTRUCTOR_OWNER,
    Radio,
    RadioBusyError,
    RadioRegistry,
)


def make_radio(rid, freq_hz, mode=RadioMode.PLAIN, owner=None, instructor=False):
    return Radio(
        radio_id=rid,
        owner=owner or rid,
        label=rid,
        frequency_hz=freq_hz,
        mode=mode,
        is_instructor=instructor,
    )


def test_same_frequency_is_same_net():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.add(make_radio("b", 14_250_000))
    reg.add(make_radio("c", 7_100_000))
    on_net = {r.radio_id for r in reg.radios_on_net(14_250_000)}
    assert on_net == {"a", "b"}


def test_tuning_grid_merges_near_frequencies():
    reg = RadioRegistry(tuning_step_hz=100.0)
    # 14_250_010 quantises to the same channel as 14_250_000.
    assert reg.same_net(14_250_000, 14_250_010)
    assert not reg.same_net(14_250_000, 14_260_000)


def test_emergent_net_no_preset_channels():
    """Radios on different frequencies do not hear each other (acceptance #3)."""
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.add(make_radio("b", 7_100_000))
    assert reg.render_for("a") is Reception.SILENCE
    reg.begin_key("b", "evt-b", crypto_enabled=False)
    # b is on a different net; a still hears nothing.
    assert reg.render_for("a") is Reception.SILENCE


def test_plain_tx_heard_clear_on_net():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000))
    reg.add(make_radio("rx", 14_250_000))
    reg.begin_key("tx", "evt", crypto_enabled=False)
    assert reg.render_for("rx") is Reception.CLEAR
    assert reg.render_for("tx") is Reception.SILENCE  # keyed -> deaf


def test_cypher_tx_plain_rx_hears_hash():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000, mode=RadioMode.CYPHER))
    reg.add(make_radio("rx", 14_250_000, mode=RadioMode.PLAIN))
    reg.begin_key("tx", "evt", crypto_enabled=True)
    reg.sync_complete("tx")  # on air after sync
    assert reg.render_for("rx") is Reception.HASH


def test_cypher_sync_not_on_air_until_complete():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000, mode=RadioMode.CYPHER))
    reg.add(make_radio("rx", 14_250_000))
    applies = reg.begin_key("tx", "evt", crypto_enabled=True)
    assert applies is True
    # During sync the station is not yet on air, so rx hears nothing.
    assert reg.render_for("rx") is Reception.SILENCE
    reg.sync_complete("tx")
    assert reg.render_for("rx") is Reception.HASH


def test_plain_keying_skips_sync():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000, mode=RadioMode.PLAIN))
    applies = reg.begin_key("tx", "evt", crypto_enabled=True)
    assert applies is False
    assert reg.get("tx").on_air is True


def test_crypto_globally_disabled_skips_sync():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000, mode=RadioMode.CYPHER))
    applies = reg.begin_key("tx", "evt", crypto_enabled=False)
    assert applies is False
    assert reg.get("tx").on_air is True


def test_plain_collision_render():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.add(make_radio("b", 14_250_000))
    reg.add(make_radio("rx", 14_250_000))
    reg.begin_key("a", "ea", crypto_enabled=False)
    reg.begin_key("b", "eb", crypto_enabled=False)
    assert reg.render_for("rx") is Reception.PLAIN_COLLISION


def test_cypher_collision_render():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000, mode=RadioMode.CYPHER))
    reg.add(make_radio("b", 14_250_000, mode=RadioMode.CYPHER))
    reg.add(make_radio("rx", 14_250_000, mode=RadioMode.CYPHER))
    reg.begin_key("a", "ea")
    reg.sync_complete("a")
    reg.begin_key("b", "eb")
    reg.sync_complete("b")
    assert reg.render_for("rx") is Reception.CRYPTO_JAM


def test_cannot_retune_while_on_air():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.begin_key("a", "e", crypto_enabled=False)
    with pytest.raises(RadioBusyError):
        reg.tune("a", 7_100_000)


def test_can_retune_when_idle():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.tune("a", 7_100_000)
    assert reg.get("a").frequency_hz == 7_100_000


def test_cannot_change_mode_while_transmitting():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000, mode=RadioMode.CYPHER))
    reg.begin_key("a", "e")  # enters sync (transmitting)
    with pytest.raises(RadioBusyError):
        reg.set_mode("a", RadioMode.PLAIN)


def test_mode_persists_across_retune():
    """Mode never changes on retune (§3.4.4, acceptance #6)."""
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000, mode=RadioMode.CYPHER))
    reg.tune("a", 7_100_000)
    assert reg.get("a").mode is RadioMode.CYPHER


def test_end_key_returns_event_id():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000))
    reg.begin_key("a", "evt-123", crypto_enabled=False)
    assert reg.end_key("a") == "evt-123"
    assert reg.get("a").transmitting is False


def test_has_listener():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000))
    assert reg.has_listener(14_250_000, exclude="tx") is False
    reg.add(make_radio("rx", 14_250_000))
    assert reg.has_listener(14_250_000, exclude="tx") is True


def test_render_map_groups_listeners():
    reg = RadioRegistry()
    reg.add(make_radio("tx", 14_250_000, mode=RadioMode.CYPHER))
    reg.add(make_radio("p1", 14_250_000, mode=RadioMode.PLAIN))
    reg.add(make_radio("p2", 14_250_000, mode=RadioMode.PLAIN))
    reg.add(make_radio("c1", 14_250_000, mode=RadioMode.CYPHER))
    reg.begin_key("tx", "e")
    reg.sync_complete("tx")
    rmap = reg.render_map_for_net(14_250_000)
    assert rmap["p1"] is Reception.HASH
    assert rmap["p2"] is Reception.HASH
    assert rmap["c1"] is Reception.CLEAR
    assert rmap["tx"] is Reception.SILENCE  # keyed


def test_instructor_radio_owner():
    reg = RadioRegistry()
    reg.add(make_radio("ir1", 14_250_000, owner=INSTRUCTOR_OWNER, instructor=True))
    r = reg.get("ir1")
    assert r.owner == INSTRUCTOR_OWNER
    assert r.is_instructor is True


def test_radio_status_strings():
    reg = RadioRegistry()
    reg.add(make_radio("a", 14_250_000, mode=RadioMode.CYPHER))
    assert reg.get("a").status == "idle"
    reg.begin_key("a", "e")
    assert reg.get("a").status == "crypto-sync"
    reg.sync_complete("a")
    assert reg.get("a").status == "transmitting"

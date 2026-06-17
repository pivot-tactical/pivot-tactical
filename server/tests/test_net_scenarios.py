"""Per-net scenario overrides (spec §3.1.5).

The instructor controls interference/jamming per channel from their radio
panels. These cover the band-profile resolution (only the targeted net is
affected), the manager's persistence + broadcast, the REST surface, and the
schema migration that adds the column to a v1 database.
"""

from __future__ import annotations

import pytest

from pivot.core.bands import BandProfile, NetScenario, net_key_for
from pivot.db import repository as repo
from pivot.runtime.manager import SessionManager

NET_A = 14_250_000.0
NET_B = 14_262_500.0  # the adjacent 12.5 kHz channel


# --- band profile resolution ------------------------------------------------ #


def test_interference_degrades_only_its_net():
    profile = BandProfile()
    clean_a = profile.conditions_at(NET_A)
    clean_b = profile.conditions_at(NET_B)
    profile.set_net_scenario(NET_A, interference=0.6)
    hit = profile.conditions_at(NET_A)
    neighbour = profile.conditions_at(NET_B)
    assert hit.interference == pytest.approx(0.6)
    assert hit.snr_db < clean_a.snr_db
    assert hit.fading_depth_db > clean_a.fading_depth_db
    assert neighbour.interference == 0.0
    assert neighbour.snr_db == pytest.approx(clean_b.snr_db)


def test_interference_scales_with_level():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.25)
    mild = profile.conditions_at(NET_A).snr_db
    profile.set_net_scenario(NET_A, interference=1.0)
    severe = profile.conditions_at(NET_A).snr_db
    assert severe < mild


def test_negative_interference_cleans_the_channel():
    """A negative offset lifts the net above its natural baseline — the
    instructor's on-the-fly cleanup for a channel that is too noisy."""
    profile = BandProfile()
    low_hf = 2_000_000.0
    clean_base = profile.conditions_at(low_hf)
    profile.set_net_scenario(low_hf, interference=-1.0)
    lifted = profile.conditions_at(low_hf)
    assert lifted.snr_db > clean_base.snr_db
    assert lifted.fading_depth_db < clean_base.fading_depth_db
    # The neighbouring channel keeps its natural (noisy) baseline.
    neighbour = profile.conditions_at(low_hf + 12_500.0)
    assert neighbour.snr_db == pytest.approx(BandProfile().conditions_at(low_hf + 12_500.0).snr_db)


def test_cleanup_is_a_real_override_not_a_default():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=-0.5)
    s = profile.net_scenario_at(NET_A)
    assert s is not None and not s.is_default
    # And it round-trips through persistence like any other override.
    restored = BandProfile.net_scenarios_from_json(profile.net_scenarios_to_json())
    assert restored[net_key_for(NET_A)].interference == pytest.approx(-0.5)


def test_net_jam_flags_only_its_net():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, jammed=True)
    assert profile.conditions_at(NET_A).jammed is True
    assert profile.conditions_at(NET_A).snr_db <= -6.0
    assert profile.conditions_at(NET_B).jammed is False


def test_default_override_is_dropped():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.5, jammed=True)
    assert profile.net_scenario_at(NET_A) is not None
    profile.set_net_scenario(NET_A, interference=0.0, jammed=False)
    assert profile.net_scenario_at(NET_A) is None
    assert profile.net_scenarios == {}


def test_partial_update_keeps_other_fields():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.7)
    profile.set_net_scenario(NET_A, jammed=True)
    s = profile.net_scenario_at(NET_A)
    assert s is not None
    assert s.interference == pytest.approx(0.7)
    assert s.jammed is True


def test_net_scenario_snaps_to_channel():
    # An off-grid frequency lands on the same channel as its snapped form.
    profile = BandProfile()
    profile.set_net_scenario(NET_A + 4_000.0, jammed=True)
    assert profile.conditions_at(NET_A).jammed is True


def test_net_scenarios_json_roundtrip():
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.4)
    profile.set_net_scenario(NET_B, jammed=True)
    data = profile.net_scenarios_to_json()
    restored = BandProfile.net_scenarios_from_json(data)
    assert restored.keys() == profile.net_scenarios.keys()
    a = restored[net_key_for(NET_A)]
    assert a.interference == pytest.approx(0.4) and a.jammed is False
    assert restored[net_key_for(NET_B)].jammed is True


def test_interference_recorded_in_dsp_profile():
    # The event's dsp_profile_json round-trips the per-net fields for AAR.
    from pivot.core.bands import BandConditions

    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.5)
    cond = profile.conditions_at(NET_A)
    restored = BandConditions.from_dict(cond.to_dict())
    assert restored.interference == pytest.approx(0.5)
    # Old rows without the field still load (back-compat).
    legacy = dict(cond.to_dict())
    legacy.pop("interference")
    assert BandConditions.from_dict(legacy).interference == 0.0


# --- manager: persistence + broadcast --------------------------------------- #


def test_manager_set_net_scenario_persists_and_broadcasts(database, settings):
    manager = SessionManager(database, settings)
    received: list[dict] = []
    manager._fanout = lambda msg: received.append(msg)  # capture broadcasts

    result = manager.set_net_scenario("14.250 MHz", interference=0.8, jammed=True)
    assert result["interference"] == pytest.approx(0.8)
    assert result["jammed"] is True

    updates = [m for m in received if m["type"] == "band_profile_update"]
    assert updates and updates[-1]["payload"]["net_scenarios"][0]["jammed"] is True

    # Survives a restart: a fresh manager reloads the override from the DB.
    reloaded = SessionManager(database, settings)
    assert reloaded.band_profile.conditions_at(NET_A).jammed is True


def test_manager_update_curve_preserves_net_scenarios(database, settings):
    manager = SessionManager(database, settings)
    manager.set_net_scenario(NET_A, interference=0.5)
    manager.update_curve(manager.band_profile.curve_to_json())
    assert manager.band_profile.conditions_at(NET_A).interference == pytest.approx(0.5)


# --- DB round trip + migration ---------------------------------------------- #


def test_band_profile_row_roundtrips_net_scenarios(database):
    profile = BandProfile()
    profile.set_net_scenario(NET_A, interference=0.3, jammed=True)
    with database.session() as s:
        repo.save_band_profile(s, profile)
    with database.session() as s:
        loaded = repo.load_band_profile(s)
    scenario = loaded.net_scenario_at(NET_A)
    assert scenario is not None
    assert scenario.interference == pytest.approx(0.3)
    assert scenario.jammed is True


def test_migration_adds_net_scenarios_column(tmp_path):
    """A v1 database (no net_scenarios_json) is migrated forward on init."""
    import sqlite3

    from sqlalchemy import text

    from pivot.db.database import Database

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE config (key VARCHAR(64) PRIMARY KEY, value TEXT);
        INSERT INTO config VALUES ('schema_version', '1');
        CREATE TABLE band_profile (
            id INTEGER PRIMARY KEY,
            curve_json TEXT,
            atmospheric_multiplier FLOAT,
            crypto_delay_ms INTEGER,
            crypto_enabled INTEGER
        );
        INSERT INTO band_profile VALUES (1, '[]', 1.0, 1500, 1);
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    db.initialise()
    with db.session() as s:
        row = s.execute(text("SELECT net_scenarios_json FROM band_profile WHERE id = 1")).fetchone()
    assert row[0] == "[]"


# --- NetScenario model -------------------------------------------------------#


def test_net_scenario_clamps_and_snaps():
    s = NetScenario(freq_hz=NET_A + 4_000.0, interference=1.7)
    assert s.freq_hz == NET_A
    assert s.interference == 1.0
    assert not s.is_default
    assert NetScenario(freq_hz=NET_A, interference=-2.0).interference == -1.0

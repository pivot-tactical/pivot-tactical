"""Tests for the frequency model (spec §3.1.2, §4.1)."""

import math

import pytest

from pivot.core.bands import (
    DEFAULT_ANCHORS,
    MAX_FREQ_HZ,
    MIN_FREQ_HZ,
    BandProfile,
    BandRegion,
    JammingSpan,
    clamp_frequency,
    format_frequency,
    parse_frequency,
    region_for,
)


@pytest.mark.parametrize(
    "freq_hz,expected",
    [
        (1_600_000, BandRegion.HF),       # 1.6–3 MHz is technically MF, reported as HF
        (7_100_000, BandRegion.HF),
        (9_999_999, BandRegion.HF),
        (10_000_000, BandRegion.HF),
        (14_250_000, BandRegion.HF),
        (29_999_999, BandRegion.HF),
        (30_000_000, BandRegion.HF),      # ITU: 30 MHz upper edge belongs to HF
        (30_000_001, BandRegion.VHF),
        (145_500_000, BandRegion.VHF),
        (299_999_999, BandRegion.VHF),
        (300_000_000, BandRegion.VHF),    # ITU: 300 MHz upper edge belongs to VHF
        (300_000_001, BandRegion.UHF),
        (2_400_000_000, BandRegion.UHF),
    ],
)
def test_region_boundaries(freq_hz, expected):
    assert region_for(freq_hz) is expected


@pytest.mark.parametrize(
    "text,expected_hz",
    [
        ("14.250 MHz", 14_250_000),
        ("145500 kHz", 145_500_000),
        ("243 MHz", 243_000_000),
        ("7.1", 7_100_000),       # bare -> MHz
        ("2.4 GHz", 2_400_000_000),
        ("1600 kHz", 1_600_000),
        (14.25, 14_250_000),       # bare float -> MHz
        (145_500_000, 145_500_000),  # large number -> already Hz
    ],
)
def test_parse_frequency(text, expected_hz):
    assert parse_frequency(text) == pytest.approx(expected_hz)


def test_parse_frequency_roundtrip():
    f = parse_frequency("14.250 MHz")
    assert format_frequency(f) == "14.250 MHz"


def test_parse_frequency_rejects_garbage():
    with pytest.raises(ValueError):
        parse_frequency("not a frequency")


def test_clamp_frequency():
    assert clamp_frequency(100) == MIN_FREQ_HZ
    assert clamp_frequency(9e9) == MAX_FREQ_HZ
    assert clamp_frequency(14e6) == 14e6


def test_snr_increases_with_frequency():
    """The defining behaviour: tuning upward cleans up (§4.1, acceptance #4)."""
    profile = BandProfile()
    samples = [1.6e6, 7e6, 14e6, 50e6, 145e6, 440e6, 2.4e9]
    snrs = [profile.conditions_at(f).snr_db for f in samples]
    # Strictly non-decreasing, and meaningfully better at the top.
    assert all(b >= a for a, b in zip(snrs, snrs[1:]))
    assert snrs[-1] - snrs[0] > 15.0


def test_fading_decreases_with_frequency():
    profile = BandProfile()
    low = profile.conditions_at(2e6).fading_depth_db
    high = profile.conditions_at(2.4e9).fading_depth_db
    assert low > high
    assert high < 1.0  # near-clean at UHF


def test_interpolation_is_between_anchors():
    profile = BandProfile()
    # 15 MHz sits between the 10 MHz and 20 MHz anchors.
    c = profile.conditions_at(15e6)
    a10 = next(a for a in DEFAULT_ANCHORS if a.freq_hz == 10e6)
    a20 = next(a for a in DEFAULT_ANCHORS if a.freq_hz == 20e6)
    assert a10.snr_db < c.snr_db < a20.snr_db


def test_hf_flags_engaged_only_in_hf():
    profile = BandProfile()
    assert profile.conditions_at(5e6).selective_fading is True
    assert profile.conditions_at(5e6).qrm is True
    assert profile.conditions_at(145e6).selective_fading is False
    assert profile.conditions_at(145e6).qrm is False


def test_atmospheric_multiplier_worsens_conditions():
    base = BandProfile().conditions_at(14e6)
    worse = BandProfile(atmospheric_multiplier=2.0).conditions_at(14e6)
    assert worse.snr_db < base.snr_db
    assert worse.fading_depth_db > base.fading_depth_db


def test_jamming_overrides_baseline():
    profile = BandProfile(jamming=[JammingSpan(14_200_000, 14_300_000)])
    jammed = profile.conditions_at(14_250_000)
    clean = profile.conditions_at(14_000_000)
    assert jammed.jammed is True
    assert clean.jammed is False
    assert jammed.snr_db < clean.snr_db


def test_pink_weight_strongest_at_low_hf():
    profile = BandProfile()
    assert profile.conditions_at(1.6e6).pink_weight > 0.9
    assert profile.conditions_at(2.4e9).pink_weight < 0.1


def test_curve_json_roundtrip():
    profile = BandProfile(atmospheric_multiplier=1.3)
    data = profile.curve_to_json()
    restored = BandProfile.from_curve_json(data, atmospheric_multiplier=1.3)
    for f in (2e6, 14e6, 145e6, 440e6):
        assert restored.conditions_at(f).snr_db == pytest.approx(
            profile.conditions_at(f).snr_db
        )


def test_squelch_tail_longer_on_hf():
    profile = BandProfile()
    assert profile.conditions_at(2e6).squelch_tail_ms > profile.conditions_at(440e6).squelch_tail_ms


def test_bandpass_narrows_at_low_hf():
    profile = BandProfile()
    low = profile.conditions_at(2e6)
    vhf = profile.conditions_at(145e6)
    assert low.bandpass_low_hz > vhf.bandpass_low_hz
    assert low.bandpass_high_hz < vhf.bandpass_high_hz


def test_snap_frequency_to_25khz_raster():
    from pivot.core.bands import CHANNEL_STEP_HZ, snap_frequency

    assert CHANNEL_STEP_HZ == 25_000
    assert snap_frequency(145_500_000) == 145_500_000  # already on grid
    assert snap_frequency(145_513_000) == 145_525_000  # snaps up to nearest
    assert snap_frequency(145_511_000) == 145_500_000  # snaps down to nearest
    # Any input lands on a valid channel within half a step, and clamps to range.
    for f in (7_106_000, 30_010_000, 243_333_000):
        snapped = snap_frequency(f)
        assert snapped % 25_000 == 0
        assert abs(snapped - f) <= 12_500
    assert snap_frequency(100) == MIN_FREQ_HZ

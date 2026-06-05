"""Tests for semantic-version ordering (§3.7.3) and time handling (§3.8)."""

from datetime import datetime, timezone

from pivot.core.timebase import (
    format_clock,
    parse_iso_utc,
    resolve_timezone,
    to_iso_utc,
)
from pivot.version import SemVer


def test_semver_ordering():
    assert SemVer.parse("1.0.0") < SemVer.parse("1.0.1")
    assert SemVer.parse("1.0.0") < SemVer.parse("1.1.0")
    assert SemVer.parse("1.9.0") < SemVer.parse("2.0.0")
    assert SemVer.parse("v1.2.3") == SemVer.parse("1.2.3")


def test_prerelease_sorts_below_release():
    # A pre-release precedes its release (SemVer §11, used by update ordering).
    assert SemVer.parse("1.0.0-rc.1") < SemVer.parse("1.0.0")
    assert SemVer.parse("1.0.0-alpha") < SemVer.parse("1.0.0-beta")
    assert SemVer.parse("1.0.0-alpha.1") < SemVer.parse("1.0.0-alpha.2")


def test_build_metadata_ignored_for_precedence():
    assert not (SemVer.parse("1.0.0+abc") < SemVer.parse("1.0.0+def"))
    assert not (SemVer.parse("1.0.0+def") < SemVer.parse("1.0.0+abc"))


def test_try_parse_returns_none_on_garbage():
    assert SemVer.try_parse("not-a-version") is None
    assert SemVer.try_parse("1.2.3") is not None


def test_is_prerelease_flag():
    assert SemVer.parse("1.0.0-rc.1").is_prerelease
    assert not SemVer.parse("1.0.0").is_prerelease


def test_utc_roundtrip():
    dt = datetime(2026, 6, 5, 12, 30, 15, tzinfo=timezone.utc)
    assert parse_iso_utc(to_iso_utc(dt)) == dt


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 6, 5, 12, 0, 0)
    iso = to_iso_utc(naive)
    assert iso.endswith("+00:00")


def test_clock_formats_in_zone():
    dt = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert format_clock(dt, "UTC") == "12:00:00"
    # New York is UTC-4 in June (DST).
    assert format_clock(dt, "America/New_York") == "08:00:00"


def test_unknown_timezone_falls_back_to_utc():
    tz = resolve_timezone("Not/AZone")
    assert tz.key == "UTC"

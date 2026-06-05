"""Time handling: UTC storage, configurable display timezone (spec §3.8).

All timestamps are stored internally in UTC. A single display timezone is
configurable in server settings (default UTC) and applied consistently across
the instructor station, every trainee terminal and the AAR. Changing the zone
updates presentation only — stored UTC values never change.

The live seven-segment clock on every terminal (§7.3) is driven by the client,
but the server is the source of truth for the configured zone and the canonical
"now", broadcast via the ``timezone_update`` WebSocket message (§6.2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    """Timezone-aware current time in UTC (the canonical clock)."""
    return datetime.now(timezone.utc)


def to_iso_utc(dt: datetime) -> str:
    """Serialise an instant as an ISO-8601 UTC string for storage (§3.5.3)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso_utc(text: str) -> datetime:
    """Parse a stored ISO-8601 timestamp back into an aware UTC datetime."""
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_timezone(name: str) -> ZoneInfo:
    """Resolve a configured IANA timezone name, falling back to UTC.

    The GUI offers a picker; an unknown/garbled value must never crash a
    terminal's clock, so we degrade gracefully to UTC.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def format_clock(dt: datetime, tz_name: str = "UTC") -> str:
    """Render ``HH:MM:SS`` in the configured zone for the seven-segment clock."""
    tz = resolve_timezone(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%H:%M:%S")


def display_in_zone(dt: datetime, tz_name: str = "UTC") -> datetime:
    """Convert a stored UTC instant into the configured display zone."""
    tz = resolve_timezone(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)

"""Pydantic request/response models for the REST + WS API (spec §6)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pivot.core.crypto import RadioMode


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    trainee_id: str | None = None  # browser-generated UUID; created if absent


class LoginResponse(BaseModel):
    trainee_id: str
    radio_id: str
    frequency: str
    frequency_hz: float
    mode: str


class TuneRequest(BaseModel):
    radio_id: str
    frequency: str  # human frequency, e.g. "14.250 MHz" or "145.5"


class ModeRequest(BaseModel):
    radio_id: str
    mode: RadioMode


class RadioResponse(BaseModel):
    radio_id: str
    name: str
    is_instructor: bool
    frequency: str
    frequency_hz: float
    band_region: str
    mode: str
    status: str


class SessionResponse(BaseModel):
    id: str
    name: str
    started_at: str
    ended_at: str | None = None
    event_count: int | None = None


class StartSessionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ScenarioRequest(BaseModel):
    """Instructor scenario controls (§3.1.5). All fields optional; set what you
    want to change in one call."""

    atmospheric_multiplier: float | None = None
    crypto_enabled: bool | None = None
    jamming_on: list[list[float]] | None = None   # [[low_hz, high_hz], ...]
    noise_burst: list[float] | None = None         # [low_hz, high_hz]
    curve: list[dict] | None = None                # noise-vs-frequency anchors
    display_timezone: str | None = None
    kick_trainee_id: str | None = None

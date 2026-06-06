"""Pydantic request/response models for the REST + WS API (spec §6)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pivot.core.crypto import RadioMode


class LoginRequest(BaseModel):
    # Trainees send a callsign; instructors send role="instructor" + password.
    name: str | None = Field(default=None, max_length=32)
    role: str = "trainee"  # "trainee" | "instructor"
    password: str | None = None
    trainee_id: str | None = None  # browser-generated UUID; created if absent


class LoginResponse(BaseModel):
    role: str
    token: str | None = None          # instructor bearer token
    must_change_password: bool = False  # true while the default password is in use
    # Trainee radio fields (absent for instructors):
    trainee_id: str | None = None
    radio_id: str | None = None
    frequency: str | None = None
    frequency_hz: float | None = None
    mode: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=4, max_length=128)


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


class ApplyUpdateRequest(BaseModel):
    """Instructor-initiated update apply (§3.7.5)."""

    tag: str
    asset_url: str
    sha256_url: str = ""
    asset_name: str


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

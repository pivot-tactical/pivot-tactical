"""SQLAlchemy ORM models mirroring the spec §5.1 schema.

Enum columns are stored as their human string value (``native_enum=False`` so
SQLite gets a portable VARCHAR + CHECK). ``RadioMode``, ``Audibility`` and
``SyncStatus`` are the same enums used by the domain logic
(:mod:`pivot.core.crypto`); ``TranscriptionStatus`` is defined here as it is a
purely persisted concept.
"""

from __future__ import annotations

import enum
import json

from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from pivot.core.crypto import Audibility, RadioMode, SyncStatus


class Base(DeclarativeBase):
    pass


class TranscriptionStatus(enum.StrEnum):
    """faster-whisper job state for an event (spec §3.5.3)."""

    PENDING = "Pending"
    DONE = "Done"
    FAILED = "Failed"
    SKIPPED = "Skipped"


def _str_enum(py_enum):
    """A portable VARCHAR-backed column type storing the enum's *value*."""
    return SAEnum(
        py_enum,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        length=32,
        validate_strings=True,
    )


# --------------------------------------------------------------------------- #
# config (key/value, JSON-encoded values) — spec §5.1
# --------------------------------------------------------------------------- #


class ConfigRow(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded


# --------------------------------------------------------------------------- #
# band_profile (single active row) — spec §5.1
# --------------------------------------------------------------------------- #


class BandProfileRow(Base):
    __tablename__ = "band_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    curve_json: Mapped[str] = mapped_column(Text)  # anchor points -> noise/fading
    crypto_delay_ms: Mapped[int] = mapped_column(Integer, default=1500)
    crypto_enabled: Mapped[int] = mapped_column(Integer, default=1)  # 0/1 override
    # Per-net instructor overrides (interference/jam per channel, §3.1.5), so a
    # jammed net survives a server restart like the rest of the scenario.
    net_scenarios_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")


# --------------------------------------------------------------------------- #
# instructor_radios — spec §5.1, §3.1.2a
# --------------------------------------------------------------------------- #


class InstructorRadioRow(Base):
    __tablename__ = "instructor_radios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64))         # "Radio 1", ...
    frequency: Mapped[str] = mapped_column(String(32))     # current tuned frequency
    mode: Mapped[RadioMode] = mapped_column(_str_enum(RadioMode), default=RadioMode.PLAIN)


# --------------------------------------------------------------------------- #
# trainees — spec §5.1
# --------------------------------------------------------------------------- #


class TraineeRow(Base):
    __tablename__ = "trainees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # browser UUID
    name: Mapped[str] = mapped_column(String(64))
    first_seen: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[str] = mapped_column(String(32))


# --------------------------------------------------------------------------- #
# radio_state (per terminal; persistent crypto mode) — spec §5.1, §3.4.4
# --------------------------------------------------------------------------- #


class RadioStateRow(Base):
    __tablename__ = "radio_state"
    __table_args__ = (UniqueConstraint("session_id", "trainee_id", name="uq_radio_state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    trainee_id: Mapped[str] = mapped_column(String(36), ForeignKey("trainees.id"))
    frequency: Mapped[str] = mapped_column(String(32))
    mode: Mapped[RadioMode] = mapped_column(_str_enum(RadioMode), default=RadioMode.PLAIN)
    updated_at: Mapped[str] = mapped_column(String(32))


# --------------------------------------------------------------------------- #
# sessions — spec §5.1
# --------------------------------------------------------------------------- #


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    name: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[str] = mapped_column(String(32))
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    events: Mapped[list[EventRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- #
# events — full schema per spec §3.5.3
# --------------------------------------------------------------------------- #


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)

    trainee_name: Mapped[str] = mapped_column(String(96))  # or "INSTRUCTOR (Radio N)"
    frequency: Mapped[str] = mapped_column(String(32))      # frequency transmitted on
    band_region: Mapped[str] = mapped_column(String(16))    # HF / VHF / UHF (ITU)
    tx_mode: Mapped[RadioMode] = mapped_column(_str_enum(RadioMode))
    audibility: Mapped[Audibility] = mapped_column(_str_enum(Audibility))
    sync_status: Mapped[SyncStatus] = mapped_column(_str_enum(SyncStatus), default=SyncStatus.COMPLETED)

    timestamp_start: Mapped[str] = mapped_column(String(32), index=True)  # ISO 8601 UTC
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    audio_path: Mapped[str] = mapped_column(String(255))     # relative WAV path
    dsp_profile_json: Mapped[str] = mapped_column(Text)      # full DSP settings for re-render

    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcription_status: Mapped[TranscriptionStatus] = mapped_column(
        _str_enum(TranscriptionStatus), default=TranscriptionStatus.PENDING
    )
    # Manual instructor edits (§3.5.3): the machine transcription is preserved in
    # ``transcription_original`` the first time an instructor overrides the text,
    # so the console can diff the two and highlight what was changed by hand.
    # ``transcription_edited`` (0/1) flags a row the instructor has corrected.
    transcription_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_edited: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    session: Mapped[SessionRow] = relationship(back_populates="events")

    def to_dict(self) -> dict:
        """Flat dict for the AAR API and CSV export (§3.6.4)."""
        # Surface the captured channel state so the log can show *what conditions
        # this recording will re-render under* — e.g. whether jamming was on at
        # the instant it was transmitted (Dirty playback replays exactly this).
        try:
            profile = json.loads(self.dsp_profile_json) if self.dsp_profile_json else {}
        except (ValueError, TypeError):
            profile = {}
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "trainee_name": self.trainee_name,
            "frequency": self.frequency,
            "band_region": self.band_region,
            "jammed": bool(profile.get("jammed", False)),
            "snr_db": profile.get("snr_db"),
            "tx_mode": self.tx_mode.value if isinstance(self.tx_mode, RadioMode) else self.tx_mode,
            "audibility": self.audibility.value
            if isinstance(self.audibility, Audibility)
            else self.audibility,
            "sync_status": self.sync_status.value
            if isinstance(self.sync_status, SyncStatus)
            else self.sync_status,
            "timestamp_start": self.timestamp_start,
            "duration_ms": self.duration_ms,
            "audio_path": self.audio_path,
            "transcription": self.transcription,
            "transcription_confidence": self.transcription_confidence,
            "transcription_status": self.transcription_status.value
            if isinstance(self.transcription_status, TranscriptionStatus)
            else self.transcription_status,
            "transcription_original": self.transcription_original,
            "transcription_edited": bool(self.transcription_edited),
        }

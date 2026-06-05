"""Repository helpers over the ORM models.

Keeps SQLAlchemy query details out of the API/session layers. All timestamps are
written as ISO-8601 UTC strings (spec §3.8).
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from pivot.core.bands import BandProfile
from pivot.core.crypto import Audibility, RadioMode, SyncStatus
from pivot.core.timebase import to_iso_utc, utc_now
from pivot.db.models import (
    BandProfileRow,
    EventRow,
    InstructorRadioRow,
    SessionRow,
    TraineeRow,
    TranscriptionStatus,
)


def new_uuid() -> str:
    return str(uuid.uuid4())


# --- band profile ---------------------------------------------------------- #


def load_band_profile(session: Session) -> BandProfile:
    """Load the single active band profile, falling back to defaults."""
    row = session.get(BandProfileRow, 1)
    if row is None:
        return BandProfile()
    return BandProfile.from_curve_json(
        json.loads(row.curve_json),
        atmospheric_multiplier=row.atmospheric_multiplier,
        crypto_delay_ms=row.crypto_delay_ms,
        crypto_enabled=bool(row.crypto_enabled),
    )


def save_band_profile(session: Session, profile: BandProfile) -> None:
    row = session.get(BandProfileRow, 1)
    payload = dict(
        curve_json=json.dumps(profile.curve_to_json()),
        atmospheric_multiplier=profile.atmospheric_multiplier,
        crypto_delay_ms=profile.crypto_delay_ms,
        crypto_enabled=1 if profile.crypto_enabled else 0,
    )
    if row is None:
        session.add(BandProfileRow(id=1, **payload))
    else:
        for k, v in payload.items():
            setattr(row, k, v)


# --- trainees -------------------------------------------------------------- #


def upsert_trainee(session: Session, trainee_id: str, name: str) -> TraineeRow:
    now = to_iso_utc(utc_now())
    row = session.get(TraineeRow, trainee_id)
    if row is None:
        row = TraineeRow(id=trainee_id, name=name, first_seen=now, last_seen=now)
        session.add(row)
    else:
        row.name = name
        row.last_seen = now
    return row


# --- sessions -------------------------------------------------------------- #


def start_session(session: Session, name: str) -> SessionRow:
    row = SessionRow(id=new_uuid(), name=name, started_at=to_iso_utc(utc_now()), ended_at=None)
    session.add(row)
    return row


def end_session(session: Session, session_id: str) -> SessionRow | None:
    row = session.get(SessionRow, session_id)
    if row is not None and row.ended_at is None:
        row.ended_at = to_iso_utc(utc_now())
    return row


def list_sessions(session: Session) -> list[SessionRow]:
    return list(session.scalars(select(SessionRow).order_by(SessionRow.started_at.desc())))


def delete_session(session: Session, session_id: str) -> bool:
    row = session.get(SessionRow, session_id)
    if row is None:
        return False
    session.delete(row)
    return True


# --- events ---------------------------------------------------------------- #


def create_event(
    session: Session,
    *,
    session_id: str,
    trainee_name: str,
    frequency: str,
    band_region: str,
    tx_mode: RadioMode,
    audibility: Audibility,
    sync_status: SyncStatus,
    timestamp_start: str,
    duration_ms: int,
    audio_path: str,
    dsp_profile: dict,
    event_id: str | None = None,
) -> EventRow:
    """Insert a recorded transmission event (spec §3.5.3).

    Transcription fields start ``Pending``; the async worker fills them in
    (§3.5.2) without blocking live audio.
    """
    row = EventRow(
        event_id=event_id or new_uuid(),
        session_id=session_id,
        trainee_name=trainee_name,
        frequency=frequency,
        band_region=band_region,
        tx_mode=tx_mode,
        audibility=audibility,
        sync_status=sync_status,
        timestamp_start=timestamp_start,
        duration_ms=duration_ms,
        audio_path=audio_path,
        dsp_profile_json=json.dumps(dsp_profile),
        transcription=None,
        transcription_confidence=None,
        transcription_status=TranscriptionStatus.PENDING,
    )
    session.add(row)
    return row


def list_events(session: Session, session_id: str) -> list[EventRow]:
    return list(
        session.scalars(
            select(EventRow)
            .where(EventRow.session_id == session_id)
            .order_by(EventRow.timestamp_start.asc())
        )
    )


def get_event(session: Session, event_id: str) -> EventRow | None:
    return session.get(EventRow, event_id)


def set_transcription(
    session: Session,
    event_id: str,
    *,
    text_value: str | None,
    confidence: float | None,
    status: TranscriptionStatus,
) -> EventRow | None:
    row = session.get(EventRow, event_id)
    if row is None:
        return None
    row.transcription = text_value
    row.transcription_confidence = confidence
    row.transcription_status = status
    return row


# --- instructor radios ----------------------------------------------------- #


def list_instructor_radios(session: Session) -> list[InstructorRadioRow]:
    return list(session.scalars(select(InstructorRadioRow).order_by(InstructorRadioRow.id)))


def add_instructor_radio(session: Session, label: str, frequency: str) -> InstructorRadioRow:
    row = InstructorRadioRow(label=label, frequency=frequency, mode=RadioMode.PLAIN)
    session.add(row)
    session.flush()  # assign the autoincrement id so the caller can read it
    return row


def remove_instructor_radio(session: Session, radio_id: int) -> bool:
    row = session.get(InstructorRadioRow, radio_id)
    if row is None:
        return False
    session.delete(row)
    return True

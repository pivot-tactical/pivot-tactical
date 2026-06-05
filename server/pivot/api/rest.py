"""REST endpoints (spec §6.1)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from pivot.api.deps import get_manager, require_local
from pivot.api.schemas import (
    LoginRequest,
    LoginResponse,
    ModeRequest,
    RadioResponse,
    ScenarioRequest,
    SessionResponse,
    StartSessionRequest,
    TuneRequest,
)
from pivot.audio.render import AarCryptoView, PlaybackMode, render_event_wav_bytes
from pivot.core.bands import parse_frequency
from pivot.core.radios import RadioBusyError
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot import exporting

router = APIRouter(prefix="/api")


# --- trainee --------------------------------------------------------------- #


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, manager=Depends(get_manager)) -> LoginResponse:
    trainee_id = req.trainee_id or str(uuid.uuid4())
    info = manager.login(req.name.strip(), trainee_id)
    return LoginResponse(**info)


@router.post("/radio/tune", response_model=RadioResponse)
def tune(req: TuneRequest, manager=Depends(get_manager)) -> RadioResponse:
    try:
        return RadioResponse(**manager.tune(req.radio_id, req.frequency))
    except RadioBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown radio") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/radio/mode", response_model=RadioResponse)
def set_mode(req: ModeRequest, manager=Depends(get_manager)) -> RadioResponse:
    try:
        return RadioResponse(**manager.set_mode(req.radio_id, req.mode))
    except RadioBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown radio") from exc


@router.get("/band-profile")
def band_profile(manager=Depends(get_manager)) -> dict:
    return manager.band_profile_snapshot()


# --- AAR / sessions -------------------------------------------------------- #


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(manager=Depends(get_manager)) -> list[SessionResponse]:
    with manager.db.session() as s:
        out = []
        for row in repo.list_sessions(s):
            out.append(
                SessionResponse(
                    id=row.id,
                    name=row.name,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    event_count=len(repo.list_events(s, row.id)),
                )
            )
    return out


@router.get("/sessions/{session_id}/events")
def session_events(session_id: str, manager=Depends(get_manager)) -> list[dict]:
    with manager.db.session() as s:
        return [e.to_dict() for e in repo.list_events(s, session_id)]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, manager=Depends(get_manager)) -> dict:
    with manager.db.session() as s:
        ok = repo.delete_session(s, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": session_id}


@router.get("/events/{event_id}/audio")
def event_audio(
    event_id: str,
    mode: PlaybackMode = Query(PlaybackMode.CLEAN),
    view: AarCryptoView = Query(AarCryptoView.PLAIN),
    manager=Depends(get_manager),
) -> Response:
    """Stream clean or DSP-re-rendered audio for an event (§3.6.3, §4.5)."""
    with manager.db.session() as s:
        row = repo.get_event(s, event_id)
        if row is None:
            raise HTTPException(status_code=404, detail="event not found")
        try:
            wav = render_event_wav_bytes(row, manager.settings.recordings_dir, mode, view)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="recording missing") from exc
    return Response(content=wav, media_type="audio/wav")


@router.post("/sessions/{session_id}/export")
def export_session(
    session_id: str,
    fmt: str = Query("zip", pattern="^(zip|text|csv)$"),
    manager=Depends(get_manager),
) -> Response:
    """Export a session as ZIP (logs + WAVs), plain text, or CSV (§3.6.4)."""
    if fmt == "text":
        body = exporting.export_text(manager.db, session_id)
        return Response(content=body, media_type="text/plain",
                        headers=_attachment(f"{session_id}.txt"))
    if fmt == "csv":
        body = exporting.export_csv(manager.db, session_id)
        return Response(content=body, media_type="text/csv",
                        headers=_attachment(f"{session_id}.csv"))
    blob = exporting.export_zip(manager.db, manager.settings, session_id)
    return Response(content=blob, media_type="application/zip",
                    headers=_attachment(f"{session_id}.zip"))


# --- instructor / admin (local only, §8.4) --------------------------------- #


@router.get("/admin/terminals", dependencies=[Depends(require_local)])
def admin_terminals(manager=Depends(get_manager)) -> dict:
    return {
        "session_active": manager.session_active,
        "session_id": manager.current_session_id,
        "terminals": manager.monitor_snapshot(),
    }


@router.post("/admin/session/start", dependencies=[Depends(require_local)])
def admin_start_session(req: StartSessionRequest, manager=Depends(get_manager)) -> dict:
    return manager.start_session(req.name)


@router.post("/admin/session/end", dependencies=[Depends(require_local)])
def admin_end_session(manager=Depends(get_manager)) -> dict:
    return manager.end_session() or {"ended": None}


@router.post("/admin/scenario", dependencies=[Depends(require_local)])
def admin_scenario(req: ScenarioRequest, manager=Depends(get_manager)) -> dict:
    """Apply one or more scenario changes (§3.1.5)."""
    if req.atmospheric_multiplier is not None:
        manager.set_atmospheric(req.atmospheric_multiplier)
    if req.crypto_enabled is not None:
        manager.set_crypto_enabled(req.crypto_enabled)
    if req.jamming_on is not None:
        manager.set_jamming([(lo, hi) for lo, hi in req.jamming_on])
    if req.noise_burst is not None and len(req.noise_burst) == 2:
        manager.inject_noise_burst(req.noise_burst[0], req.noise_burst[1])
    if req.curve is not None:
        manager.update_curve(req.curve)
    if req.display_timezone is not None:
        manager.set_display_timezone(req.display_timezone)
    if req.kick_trainee_id is not None:
        manager.kick(req.kick_trainee_id)
    return manager.band_profile_snapshot()


@router.get("/admin/instructor-radios", dependencies=[Depends(require_local)])
def admin_list_instructor_radios(manager=Depends(get_manager)) -> list[dict]:
    return manager.instructor_radios()


@router.post("/admin/instructor-radios", dependencies=[Depends(require_local)])
def admin_add_instructor_radio(
    label: str | None = None,
    frequency: str = "145.500 MHz",
    manager=Depends(get_manager),
) -> dict:
    return manager.add_instructor_radio(label, frequency)


@router.delete("/admin/instructor-radios/{radio_id}", dependencies=[Depends(require_local)])
def admin_remove_instructor_radio(radio_id: str, manager=Depends(get_manager)) -> dict:
    ok = manager.remove_instructor_radio(radio_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instructor radio not found")
    return {"removed": radio_id}


@router.get("/config", dependencies=[Depends(require_local)])
def admin_config(manager=Depends(get_manager)) -> dict:
    with manager.db.session() as s:
        return ConfigStore(s).all()


@router.get("/status")
def status(request: Request, manager=Depends(get_manager)) -> dict:
    """Public health/info endpoint for the Status tab and trainee connect."""
    from pivot.version import version_info

    return {
        "name": "PIVOT",
        "version": version_info.version,
        "git_sha": version_info.git_sha,
        "session_active": manager.session_active,
        "terminals": len(manager.terminals),
    }


def _attachment(filename: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}

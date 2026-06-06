"""REST endpoints (spec §6.1; auth per product direction).

Trainee endpoints are open (callsign only). Instructor/admin endpoints require a
valid instructor bearer token (:func:`pivot.api.deps.require_instructor`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from pivot import exporting
from pivot.api.deps import get_auth, get_manager, require_instructor
from pivot.api.schemas import (
    ApplyUpdateRequest,
    LoginRequest,
    LoginResponse,
    ModeRequest,
    PasswordChangeRequest,
    RadioResponse,
    ScenarioRequest,
    SessionResponse,
    StartSessionRequest,
    TuneRequest,
)
from pivot.audio.render import AarCryptoView, PlaybackMode, render_event_wav_bytes
from pivot.core.crypto import RadioMode
from pivot.core.radios import RadioBusyError
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore

router = APIRouter(prefix="/api")

# Config keys the instructor may change from the Settings page.
_SETTABLE_KEYS = {
    "whisper_model",
    "whisper_compute_type",
    "whisper_language",
    "transcription_confidence_threshold",
    "transcription_skip_under_seconds",
    "whisper_initial_prompt",
    "whisper_custom_vocabulary",
    "display_timezone",
    "crypto_enabled",
    "crypto_delay_ms",
    "crypto_tone_preset",
    "tuning_step_hz",
    "update_channel",
    "update_check_on_startup",
    "auto_update",
    "github_repo",
    "github_token",
    "log_level",
}


# --- auth / login ---------------------------------------------------------- #


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, manager=Depends(get_manager), auth=Depends(get_auth)) -> LoginResponse:
    """Trainee login (callsign) or instructor login (password → bearer token)."""
    if req.role == "instructor":
        if not req.password or not auth.verify(req.password):
            raise HTTPException(status_code=401, detail="invalid instructor password")
        return LoginResponse(
            role="instructor",
            token=auth.issue_token(),
            must_change_password=auth.is_default(),
        )

    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="callsign required")
    trainee_id = req.trainee_id or str(uuid.uuid4())
    info = manager.login(name, trainee_id)
    return LoginResponse(role="trainee", **info)


@router.post("/logout")
def logout(request: Request, auth=Depends(get_auth)) -> dict:
    """Revoke the caller's instructor token, if any."""
    from pivot.api.deps import _extract_token

    auth.revoke(_extract_token(request, request.headers.get("authorization")))
    return {"ok": True}


# --- trainee radio --------------------------------------------------------- #


@router.post("/radio/tune", response_model=RadioResponse)
def tune(req: TuneRequest, manager=Depends(get_manager)) -> RadioResponse:
    _reject_instructor_radio(manager, req.radio_id)
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
    _reject_instructor_radio(manager, req.radio_id)
    try:
        return RadioResponse(**manager.set_mode(req.radio_id, req.mode))
    except RadioBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown radio") from exc


@router.get("/band-profile")
def band_profile(manager=Depends(get_manager)) -> dict:
    return manager.band_profile_snapshot()


# --- AAR / sessions (instructor only) -------------------------------------- #


@router.get("/sessions", response_model=list[SessionResponse],
            dependencies=[Depends(require_instructor)])
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


@router.get("/sessions/{session_id}/events", dependencies=[Depends(require_instructor)])
def session_events(session_id: str, manager=Depends(get_manager)) -> list[dict]:
    with manager.db.session() as s:
        return [e.to_dict() for e in repo.list_events(s, session_id)]


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_instructor)])
def delete_session(session_id: str, manager=Depends(get_manager)) -> dict:
    with manager.db.session() as s:
        ok = repo.delete_session(s, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": session_id}


@router.get("/events/{event_id}/audio", dependencies=[Depends(require_instructor)])
def event_audio(
    event_id: str,
    mode: PlaybackMode = Query(PlaybackMode.CLEAN),
    view: AarCryptoView = Query(AarCryptoView.PLAIN),
    manager=Depends(get_manager),
) -> Response:
    """Stream clean or DSP-re-rendered audio for an event (§3.6.3, §4.5).

    Instructor-only: students operate their radio and do not browse recordings.
    """
    with manager.db.session() as s:
        row = repo.get_event(s, event_id)
        if row is None:
            raise HTTPException(status_code=404, detail="event not found")
        # No WAV on disk means no audio was captured for this transmission (the
        # voice transport is not yet wired — see ROADMAP). Return a clean 404
        # rather than letting soundfile raise a 500.
        if not (manager.settings.recordings_dir / row.audio_path).exists():
            raise HTTPException(
                status_code=404, detail="no recording was captured for this transmission"
            )
        try:
            wav = render_event_wav_bytes(row, manager.settings.recordings_dir, mode, view)
        except Exception as exc:  # decode/render failure must not 500
            raise HTTPException(
                status_code=422, detail=f"could not render recording: {exc}"
            ) from exc
    return Response(content=wav, media_type="audio/wav")


@router.post("/sessions/{session_id}/export", dependencies=[Depends(require_instructor)])
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


# --- instructor / admin (instructor token required) ------------------------ #


@router.get("/admin/terminals", dependencies=[Depends(require_instructor)])
def admin_terminals(manager=Depends(get_manager)) -> dict:
    return {
        "session_active": manager.session_active,
        "session_id": manager.current_session_id,
        "terminals": manager.monitor_snapshot(),
    }


@router.post("/admin/session/start", dependencies=[Depends(require_instructor)])
def admin_start_session(req: StartSessionRequest, manager=Depends(get_manager)) -> dict:
    return manager.start_session(req.name)


@router.post("/admin/session/end", dependencies=[Depends(require_instructor)])
def admin_end_session(manager=Depends(get_manager)) -> dict:
    return manager.end_session() or {"ended": None}


@router.post("/admin/scenario", dependencies=[Depends(require_instructor)])
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


@router.get("/admin/instructor-radios", dependencies=[Depends(require_instructor)])
def admin_list_instructor_radios(manager=Depends(get_manager)) -> list[dict]:
    return manager.instructor_radios()


@router.post("/admin/instructor-radios", dependencies=[Depends(require_instructor)])
def admin_add_instructor_radio(
    label: str | None = Body(default=None),
    frequency: str = Body(default="145.500 MHz"),
    manager=Depends(get_manager),
) -> dict:
    return manager.add_instructor_radio(label, frequency)


@router.delete("/admin/instructor-radios/{radio_id}", dependencies=[Depends(require_instructor)])
def admin_remove_instructor_radio(radio_id: str, manager=Depends(get_manager)) -> dict:
    ok = manager.remove_instructor_radio(radio_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instructor radio not found")
    return {"removed": radio_id}


@router.post("/admin/instructor-radios/{radio_id}/tune", dependencies=[Depends(require_instructor)])
def admin_tune_instructor_radio(
    radio_id: str, frequency: str = Body(..., embed=True), manager=Depends(get_manager)
) -> dict:
    try:
        return manager.tune(radio_id, frequency)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown radio") from exc
    except RadioBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/admin/instructor-radios/{radio_id}/mode", dependencies=[Depends(require_instructor)])
def admin_mode_instructor_radio(
    radio_id: str, mode: RadioMode = Body(..., embed=True), manager=Depends(get_manager)
) -> dict:
    try:
        return manager.set_mode(radio_id, mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown radio") from exc
    except RadioBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/admin/config", dependencies=[Depends(require_instructor)])
def admin_config(manager=Depends(get_manager)) -> dict:
    with manager.db.session() as s:
        return ConfigStore(s).all()


@router.post("/admin/settings", dependencies=[Depends(require_instructor)])
def admin_update_settings(updates: dict = Body(...), manager=Depends(get_manager)) -> dict:
    """Persist instructor-tunable settings (whitelisted keys). Timezone, crypto
    enable and crypto delay are applied live."""
    applied: dict = {}
    with manager.db.session() as s:
        cfg = ConfigStore(s)
        for key, value in updates.items():
            if key not in _SETTABLE_KEYS:
                continue
            cfg.set(key, value)
            applied[key] = value
    if "display_timezone" in applied:
        manager.set_display_timezone(applied["display_timezone"])
    if "crypto_enabled" in applied:
        manager.set_crypto_enabled(bool(applied["crypto_enabled"]))
    if "crypto_delay_ms" in applied:
        manager.band_profile.crypto_delay_ms = int(applied["crypto_delay_ms"])
    return {"applied": applied}


@router.post("/admin/password", dependencies=[Depends(require_instructor)])
def admin_change_password(req: PasswordChangeRequest, auth=Depends(get_auth)) -> dict:
    """Change the instructor password (invalidates all instructor sessions)."""
    if not auth.verify(req.current_password):
        raise HTTPException(status_code=403, detail="current password incorrect")
    auth.set_password(req.new_password)
    return {"changed": True}


@router.get("/admin/updates/check", dependencies=[Depends(require_instructor)])
def admin_check_updates(manager=Depends(get_manager)) -> dict:
    """Check GitHub for releases on the configured channel (§3.7.3).

    Out-of-band and internet-touching; degrades gracefully (``reachable: false``)
    so air-gapped sites can ignore it and use offline import instead (§3.7.1).
    """
    from pivot.updates import github
    from pivot.updates.manager import UpdateManager, classify_release
    from pivot.version import SemVer, version_info

    with manager.db.session() as s:
        cfg = ConfigStore(s).all()
    repo = str(cfg.get("github_repo") or "")
    token = str(cfg.get("github_token") or "") or None
    channel = str(cfg.get("update_channel", "stable"))
    include_pre = channel == "include_prereleases"

    mgr = UpdateManager(
        version_info.version, manager.settings.versions_dir, include_prereleases=include_pre
    )
    result = {
        "current_version": version_info.version,
        "channel": channel,
        "auto_update": bool(cfg.get("auto_update", False)),
        "reachable": True,
        "error": None,
        "releases": [],
        "available": [],
    }
    try:
        raw = github.fetch_releases(repo, token)
    except Exception as exc:  # network/HTTP/JSON — stay graceful for air-gapped
        result["reachable"] = False
        result["error"] = str(exc)
        return result

    cur = SemVer.parse(version_info.version)

    def to_dict(r) -> dict:
        return {
            "tag": r.tag,
            "name": r.name,
            "published_at": r.published_at,
            "prerelease": r.prerelease,
            "asset_name": r.asset_name,
            "asset_url": r.asset_url,
            "sha256_url": r.sha256_url,
            "has_asset": bool(r.asset_url),
            "standing": classify_release(r, cur).value,
        }

    available = mgr.available_updates(raw)
    result["releases"] = [to_dict(r) for r in mgr.list_releases(raw)]
    result["available"] = [to_dict(r) for r in available]

    # Auto-update: download and stage the newest available release immediately.
    if bool(cfg.get("auto_update", False)) and available:
        newest = available[0]
        if newest.asset_url:
            try:
                asset_path = mgr.download(newest, token)
                mgr.stage(asset_path, newest)
                result["auto_staged"] = newest.tag
            except Exception as exc:
                result["auto_update_error"] = str(exc)

    return result


@router.post("/admin/updates/apply", dependencies=[Depends(require_instructor)])
def admin_apply_update(req: ApplyUpdateRequest, manager=Depends(get_manager)) -> dict:
    """Download, verify and stage an update for the next restart (§3.7.5).

    The actual directory swap happens out-of-process on restart so the running
    binary can be replaced on all platforms. Returns once the archive is
    verified on disk; the UI should prompt for a restart.
    """
    from pivot.updates.manager import Release, UpdateManager
    from pivot.version import version_info

    with manager.db.session() as s:
        cfg = ConfigStore(s).all()
    token = str(cfg.get("github_token") or "") or None

    mgr = UpdateManager(version_info.version, manager.settings.versions_dir)
    release = Release(
        tag=req.tag,
        asset_url=req.asset_url,
        asset_name=req.asset_name,
        sha256_url=req.sha256_url,
    )
    try:
        asset_path = mgr.download(release, token)
        staging_dir = mgr.stage(asset_path, release)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "staged": True,
        "tag": req.tag,
        "staging": str(staging_dir),
        "restart_required": True,
    }


# --- public status --------------------------------------------------------- #


@router.get("/status")
def status(request: Request, manager=Depends(get_manager)) -> dict:
    """Public health/info endpoint for the login screen and trainee connect."""
    from pivot.version import version_info

    with manager.db.session() as s:
        tz = ConfigStore(s).display_timezone()
    return {
        "name": "PIVOT",
        "version": version_info.version,
        "git_sha": version_info.git_sha,
        "session_active": manager.session_active,
        "terminals": len(manager.terminals),
        "display_timezone": tz,
    }


# --- helpers --------------------------------------------------------------- #


def _reject_instructor_radio(manager, radio_id: str) -> None:
    """Trainees may only operate their own radio, never an instructor radio."""
    radio = manager.registry.get(radio_id)
    if radio is not None and radio.is_instructor:
        raise HTTPException(status_code=403, detail="instructor radio")


def _attachment(filename: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}

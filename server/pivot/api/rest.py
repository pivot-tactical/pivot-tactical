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
    RestartRequest,
    RollbackRequest,
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


@router.post("/auth/refresh", dependencies=[Depends(require_instructor)])
def refresh_token(auth=Depends(get_auth)) -> dict:
    """Slide the instructor session: issue a fresh token for a still-valid one.

    The browser calls this on load (to confirm a stored token still works after a
    refresh or a server restart, and restore the console without re-login) and
    periodically while the console is open (so a long scenario never expires
    mid-exercise). A failure (401) means the token is gone — show the login.
    """
    return {"token": auth.issue_token(), "must_change_password": auth.is_default()}


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
        "session_name": manager.current_session_name,
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
    """Return the latest known update status (§3.7).

    The background update service polls GitHub out-of-band on an interval and
    caches the result, so this endpoint just returns that cache — the instructor
    UI never blocks on the network. Use ``POST /admin/updates/refresh`` to force
    an immediate re-check. Falls back to a one-shot live check when no service is
    running (e.g. unit tests or a pure-backend dev process).
    """
    service = getattr(manager, "update_service", None)
    if service is not None:
        return _shape_update_status(service.snapshot())
    return _live_update_check(manager)


@router.post("/admin/updates/refresh", dependencies=[Depends(require_instructor)])
def admin_refresh_updates(manager=Depends(get_manager)) -> dict:
    """Force an immediate, synchronous re-check (the "Check now" button, §3.7.3).

    Touches the internet and degrades gracefully (``reachable: false``) so
    air-gapped sites can ignore it and use offline import instead (§3.7.1).
    """
    service = getattr(manager, "update_service", None)
    if service is not None:
        return _shape_update_status(service.refresh())
    return _live_update_check(manager)


def _shape_update_status(snap: dict) -> dict:
    """Add backward-compatible derived fields the UI reads (auto_staged/error).

    The service snapshot models auto-update outcome as ``auto_state`` /
    ``auto_message``; the existing console also understands ``auto_staged`` and
    ``auto_update_error``, so surface both for a clean transition.
    """
    out = dict(snap)
    if snap.get("auto_state") == "applied":
        avail = snap.get("available") or []
        out["auto_staged"] = avail[0]["tag"] if avail else snap.get("auto_message", "")
    elif snap.get("auto_state") == "error":
        out["auto_update_error"] = snap.get("auto_message", "Auto-update failed.")
    return out


def _live_update_check(manager) -> dict:
    """One-shot live check used when the background service isn't running."""
    from pivot.updates import github
    from pivot.updates.manager import UpdateManager, classify_release
    from pivot.version import SemVer, version_info

    with manager.db.session() as s:
        cfg = ConfigStore(s).all()
    repo = str(cfg.get("github_repo") or "")
    token = str(cfg.get("github_token") or "") or None
    channel = str(cfg.get("update_channel", "stable"))
    include_pre = channel == "include_prereleases"

    # One verified download + staged swap on every platform and channel.
    updater = "staged"

    mgr = UpdateManager(
        version_info.version, manager.settings.versions_dir, include_prereleases=include_pre
    )
    result = {
        "current_version": version_info.version,
        "channel": channel,
        "auto_update": bool(cfg.get("auto_update", False)),
        "updater": updater,
        "reachable": True,
        "error": None,
        "releases": [],
        "available": [],
        "retained": mgr.retained_versions(),
        "previous": mgr.previous_version(),
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
            "sig_url": r.sig_url,
            "has_asset": bool(r.asset_url),
            "standing": classify_release(r, cur).value,
        }

    available = mgr.available_updates(raw)
    result["releases"] = [to_dict(r) for r in mgr.list_releases(raw)]
    result["available"] = [to_dict(r) for r in available]

    # Auto-update: download + stage the newest available release on the channel.
    if bool(cfg.get("auto_update", False)) and available:
        newest = available[0]
        if newest.asset_url:
            try:
                mgr.download_and_stage(newest, token)
                result["auto_staged"] = newest.tag
            except Exception as exc:
                result["auto_update_error"] = str(exc)

    return result


@router.post("/admin/updates/apply", dependencies=[Depends(require_instructor)])
def admin_apply_update(req: ApplyUpdateRequest, manager=Depends(get_manager)) -> dict:
    """Apply an update (spec §3.7.5).

    One path on every platform and channel: download the chosen release's
    archive, verify it (SHA-256 integrity + Ed25519 authenticity against the
    embedded public key), and stage it. The swap is applied on the next restart
    — use ``/admin/restart`` to do that from the browser. This uniformity is what
    lets stable and prerelease behave identically and channel switches take
    effect on the fly.
    """
    from pivot.updates.manager import Release, UpdateManager
    from pivot.version import version_info

    with manager.db.session() as s:
        cfg = ConfigStore(s).all()
    token = str(cfg.get("github_token") or "") or None

    mgr = UpdateManager(version_info.version, manager.settings.versions_dir)
    # Already staged this exact release — skip the redundant download + extract
    # and just tell the client to restart (§3.7.5).
    if mgr.staged_tag() == req.tag:
        return {
            "staged": True,
            "tag": req.tag,
            "already_staged": True,
            "restart_required": True,
        }

    release = Release(
        tag=req.tag,
        asset_url=req.asset_url,
        asset_name=req.asset_name,
        sha256_url=req.sha256_url,
        sig_url=req.sig_url,
    )
    try:
        staging_dir = mgr.download_and_stage(release, token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "staged": True,
        "tag": req.tag,
        "staging": str(staging_dir),
        "restart_required": True,
    }


@router.post("/admin/updates/rollback", dependencies=[Depends(require_instructor)])
def admin_rollback(
    req: RollbackRequest | None = None, manager=Depends(get_manager)
) -> dict:
    """Roll back to a retained version (instant, offline downgrade, §3.7.7).

    Recovery for a bad update without re-downloading: the chosen retained version
    (default: the most recent) is staged and applied on the next restart. This is
    the in-app counterpart to the ``--rollback`` recovery flag. A downgrade may
    cross a DB schema migration; the UI warns before calling this.
    """
    from pivot.runtime.lifecycle import install_dir
    from pivot.updates.manager import UpdateManager
    from pivot.version import version_info

    req = req or RollbackRequest()
    mgr = UpdateManager(version_info.version, manager.settings.versions_dir)
    target = req.tag or mgr.previous_version()
    if target is None:
        raise HTTPException(status_code=409, detail="No retained version to roll back to.")
    try:
        mgr.stage_rollback(target, install_dir())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"staged": True, "tag": target, "rollback": True, "restart_required": True}


@router.get("/admin/updates/retained", dependencies=[Depends(require_instructor)])
def admin_retained_versions(manager=Depends(get_manager)) -> dict:
    """Versions kept on disk for instant rollback, with their size (§3.7.7).

    Distinct from the older *releases* in the update list: those re-download,
    these are already on disk. Lets the Updates pane show what's stored and how
    much space it uses before deleting it.
    """
    from pivot.updates.manager import UpdateManager
    from pivot.version import version_info

    mgr = UpdateManager(version_info.version, manager.settings.versions_dir)
    return {"retained": mgr.retained_details(), "current_version": version_info.version}


@router.delete("/admin/updates/retained/{tag}", dependencies=[Depends(require_instructor)])
def admin_delete_retained(tag: str, manager=Depends(get_manager)) -> dict:
    """Delete a retained version to free disk space (§3.7.7)."""
    from pivot.updates.manager import UpdateManager
    from pivot.version import version_info

    mgr = UpdateManager(version_info.version, manager.settings.versions_dir)
    if not mgr.delete_retained(tag):
        raise HTTPException(status_code=404, detail=f"No retained version: {tag}")
    return {"deleted": tag, "retained": mgr.retained_details()}


@router.post("/admin/restart", dependencies=[Depends(require_instructor)])
def admin_restart(
    request: Request,
    req: RestartRequest | None = None,
    manager=Depends(get_manager),
) -> dict:
    """Restart the server from the browser (spec §3.7.5).

    Mainly used to apply a staged update without reaching the server console:
    the process stops gracefully and is brought back by its supervisor (systemd)
    or a detached relauncher (packaged exe), which applies any staged update on
    the way back up. Refused while a session is live unless ``force`` is set, so
    trainees are never cut off mid-exercise by accident.
    """
    import threading
    import time as _time

    from pivot.runtime.lifecycle import restart_mode

    req = req or RestartRequest()
    if manager.session_active and not req.force:
        raise HTTPException(
            status_code=409,
            detail="A training session is running. End it first, or force the restart.",
        )

    request_restart = getattr(request.app.state, "request_restart", None)
    mode = restart_mode()
    if request_restart is None:
        # No server host wired this up (e.g. dev via TestClient): nothing to do.
        raise HTTPException(
            status_code=503,
            detail="Restart is not available in this run mode.",
        )

    # Stop just after the response is flushed so the browser gets the ack and can
    # switch to its reconnecting state before the socket drops.
    def _go() -> None:
        _time.sleep(0.4)
        request_restart()

    threading.Thread(target=_go, name="restart", daemon=True).start()

    from pivot.updates.manager import UpdateManager
    from pivot.version import version_info

    staged = UpdateManager(version_info.version, manager.settings.versions_dir).staged_tag()
    return {"restarting": True, "mode": mode, "staged": staged}


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

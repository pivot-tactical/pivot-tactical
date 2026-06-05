"""FastAPI application factory (spec §2.3, §6).

Builds the ASGI app, initialises the SQLite database and the live
:class:`~pivot.runtime.manager.SessionManager`, mounts the REST + WebSocket
routers, and serves the built React frontend so trainees reach the UI at
``http://[server-ip]:8080`` (spec §2.1).
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pivot.api import rest, ws
from pivot.auth import AuthService
from pivot.config import Settings
from pivot.config import settings as default_settings
from pivot.db.database import init_database
from pivot.runtime.manager import SessionManager
from pivot.version import version_info


def _maybe_start_transcription(manager: SessionManager, cfg: Settings):
    """Start the async transcription worker if faster-whisper is installed.

    Returns the worker (already started and wired to the manager) or None. The
    worker broadcasts ``transcription_updated`` via ``manager.notify_transcription``
    so the instructor's live log fills in transcripts as they complete (§3.5.2).
    """
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return None
    from pivot.transcription.worker import TranscriptionWorker

    worker = TranscriptionWorker(manager.db, cfg)
    worker.on_complete = manager.notify_transcription
    manager.transcription_worker = worker
    worker.start()
    return worker


def frontend_dist_dir() -> Path | None:
    """Locate the built frontend (``vite build`` output), if present.

    Checks an explicit override, the PyInstaller bundle location, and the repo
    layout. Returns None during pure-backend development (API still works).
    """
    candidates = []
    env = os.environ.get("PIVOT_FRONTEND_DIST")
    if env:
        candidates.append(Path(env))
    # PyInstaller bundle: --add-data places the build at the bundle root
    # (sys._MEIPASS) under 'frontend_dist'. Covers both --onedir and --onefile.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "frontend_dist")
    candidates.append(Path(__file__).resolve().parent.parent / "frontend_dist")
    # Repo layout: server/pivot/api/app.py -> repo/frontend/dist
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "dist")
    for c in candidates:
        if c and c.is_dir() and (c / "index.html").exists():
            return c
    return None


def create_app(settings: Settings | None = None, manager: SessionManager | None = None) -> FastAPI:
    """Build the app. A shared ``manager`` may be supplied (e.g. by the CLI
    entry point) so the caller and the server operate on the same live state."""
    cfg = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        if manager is not None:
            app.state.db = manager.db
            app.state.manager = manager
        else:
            db = init_database(cfg)
            app.state.db = db
            app.state.manager = SessionManager(db, cfg)
        app.state.settings = cfg
        # Record the running loop so cross-thread broadcasts marshal correctly.
        app.state.manager.loop = asyncio.get_running_loop()
        app.state.audio_router = None  # set by the audio plane when available

        # Instructor authentication: seed the default password on first run.
        app.state.auth = AuthService(app.state.manager.db)
        app.state.auth.ensure_default()

        # Async transcription worker — only started if faster-whisper is present
        # (the live event log still works without it; transcripts stay pending).
        worker = _maybe_start_transcription(app.state.manager, cfg)
        app.state.transcription_worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()

    app = FastAPI(
        title="PIVOT — Procedural Interactive Voice Operations Trainer",
        version=version_info.version,
        lifespan=lifespan,
    )

    # LAN-only deployment: same-origin frontend, but allow LAN origins for dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(rest.router)
    app.include_router(ws.router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist = frontend_dist_dir()
    if dist is None:
        @app.get("/")
        def _no_frontend() -> JSONResponse:  # pragma: no cover - dev convenience
            return JSONResponse(
                {
                    "name": "PIVOT",
                    "version": version_info.version,
                    "note": "Frontend build not found. Run `npm run build` in frontend/.",
                    "api": "/api/status",
                }
            )
        return

    # Serve hashed assets, with an index.html fallback for SPA client routes.
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(dist / "index.html")

    @app.get("/{path:path}")
    def _spa(path: str) -> FileResponse:
        target = dist / path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(dist / "index.html")

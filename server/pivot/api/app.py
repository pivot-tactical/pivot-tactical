"""FastAPI application factory (spec §2.3, §6).

Builds the ASGI app, initialises the SQLite database and the live
:class:`~pivot.runtime.manager.SessionManager`, mounts the REST + WebSocket
routers, and serves the built React frontend so trainees reach the UI at
``http://[server-ip]:8080`` (spec §2.1).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pivot.api import rest, ws
from pivot.config import Settings, settings as default_settings
from pivot.db.database import init_database
from pivot.runtime.manager import SessionManager
from pivot.version import version_info


def frontend_dist_dir() -> Path | None:
    """Locate the built frontend (``vite build`` output), if present.

    Checks an explicit override, the PyInstaller bundle location, and the repo
    layout. Returns None during pure-backend development (API still works).
    """
    candidates = []
    env = os.environ.get("PIVOT_FRONTEND_DIST")
    if env:
        candidates.append(Path(env))
    # PyInstaller: frontend added via --add-data lands next to the package.
    candidates.append(Path(__file__).resolve().parent.parent / "frontend_dist")
    # Repo layout: server/pivot/api/app.py -> repo/frontend/dist
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "dist")
    for c in candidates:
        if c and c.is_dir() and (c / "index.html").exists():
            return c
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = init_database(cfg)
        app.state.settings = cfg
        app.state.db = db
        app.state.manager = SessionManager(db, cfg)
        app.state.audio_router = None  # set by the audio plane when available
        yield

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

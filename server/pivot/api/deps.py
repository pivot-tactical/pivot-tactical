"""Shared API dependencies (spec §6, §8.4)."""

from __future__ import annotations

from fastapi import HTTPException, Request

from pivot.runtime.manager import SessionManager

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def get_manager(request: Request) -> SessionManager:
    return request.app.state.manager


def require_local(request: Request) -> None:
    """Gate instructor/admin endpoints to the server machine itself (§8.4).

    Instructor controls are only accessible from the server machine; trainees on
    the LAN must not reach them. We allow loopback only — the in-process GUI does
    not use HTTP, so this is purely a guard against LAN access to admin routes.
    """
    client = request.client
    host = client.host if client else None
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="instructor controls are local-only")

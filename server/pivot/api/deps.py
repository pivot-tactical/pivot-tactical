"""Shared API dependencies (spec §6; auth per product direction).

The headless server gates instructor/admin endpoints behind an instructor bearer
token (see :mod:`pivot.auth`) rather than loopback-only access — the instructor
operates from a browser over the LAN. Trainees are unauthenticated.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from pivot.auth import AuthService
from pivot.runtime.manager import SessionManager


def get_manager(request: Request) -> SessionManager:
    return request.app.state.manager


def get_auth(request: Request) -> AuthService:
    return request.app.state.auth


def _extract_token(request: Request, authorization: str | None) -> str | None:
    """Instructor token from the HttpOnly session cookie or the Authorization header.

    The cookie is the primary mechanism for browser clients (set by the server,
    never readable by JS). The Bearer header is kept as a fallback for API
    clients and scripts that cannot use cookies.
    """
    cookie_token = request.cookies.get("pivot_token")
    if cookie_token:
        return cookie_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_instructor(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Reject callers without a valid instructor token (401)."""
    auth = get_auth(request)
    token = _extract_token(request, authorization)
    if not auth.validate(token):
        raise HTTPException(status_code=401, detail="instructor authentication required")

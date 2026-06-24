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
    """Bearer token from the Authorization header, or a ``?token=`` query param.

    The query-param form lets the browser's ``<audio>`` element and the
    WebSocket pass the token where custom headers are awkward.
    """
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.query_params.get("token")


def require_instructor(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Reject callers without a valid instructor token (401)."""
    auth = get_auth(request)
    token = _extract_token(request, authorization)
    if not auth.validate(token):
        raise HTTPException(status_code=401, detail="instructor authentication required")

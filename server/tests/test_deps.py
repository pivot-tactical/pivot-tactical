"""Tests for FastAPI dependency functions."""
from unittest.mock import MagicMock
import pytest
from fastapi import Request, HTTPException

from pivot.api.deps import (
    get_manager,
    get_auth,
    _extract_token,
    require_instructor,
)

def test_get_manager():
    """Verify that get_manager extracts the SessionManager from the request state."""
    mock_request = MagicMock(spec=Request)
    mock_manager = MagicMock()
    mock_request.app.state.manager = mock_manager

    assert get_manager(mock_request) is mock_manager

def test_get_auth():
    """Verify that get_auth extracts the AuthService from the request state."""
    mock_request = MagicMock(spec=Request)
    mock_auth = MagicMock()
    mock_request.app.state.auth = mock_auth

    assert get_auth(mock_request) is mock_auth

def test_extract_token_from_header():
    """Verify token extraction from the Authorization header."""
    mock_request = MagicMock(spec=Request)
    mock_request.cookies = {}
    mock_request.query_params = {}

    assert _extract_token(mock_request, "Bearer some_token") == "some_token"
    assert _extract_token(mock_request, "bearer another_token") == "another_token"
    assert _extract_token(mock_request, "Bearer   spaced_token  ") == "spaced_token"

def test_extract_token_from_cookie():
    """Verify token extraction from the pivot_token cookie when header is absent."""
    mock_request = MagicMock(spec=Request)
    mock_request.cookies = {"pivot_token": "cookie_token"}
    mock_request.query_params = {}

    assert _extract_token(mock_request, None) == "cookie_token"
    assert _extract_token(mock_request, "") == "cookie_token"

def test_extract_token_from_query_param():
    """Verify token extraction from the query parameter when others are absent."""
    mock_request = MagicMock(spec=Request)
    mock_request.cookies = {}
    mock_request.query_params = {"token": "query_token"}

    assert _extract_token(mock_request, None) == "query_token"

def test_extract_token_missing():
    """Verify token extraction returns None when no token is present."""
    mock_request = MagicMock(spec=Request)
    mock_request.cookies = {}
    mock_request.query_params = {}

    assert _extract_token(mock_request, None) is None

def test_require_instructor_valid():
    """Verify require_instructor allows access with a valid token."""
    mock_request = MagicMock(spec=Request)
    mock_auth = MagicMock()
    mock_auth.validate.return_value = True
    mock_request.app.state.auth = mock_auth
    mock_request.cookies = {}
    mock_request.query_params = {}

    # Should not raise any exception
    require_instructor(mock_request, "Bearer valid_token")
    mock_auth.validate.assert_called_once_with("valid_token")

def test_require_instructor_invalid():
    """Verify require_instructor raises HTTPException(401) with an invalid token."""
    mock_request = MagicMock(spec=Request)
    mock_auth = MagicMock()
    mock_auth.validate.return_value = False
    mock_request.app.state.auth = mock_auth
    mock_request.cookies = {}
    mock_request.query_params = {}

    with pytest.raises(HTTPException) as exc_info:
        require_instructor(mock_request, "Bearer invalid_token")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "instructor authentication required"
    mock_auth.validate.assert_called_once_with("invalid_token")

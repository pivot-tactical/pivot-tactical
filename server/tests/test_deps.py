from unittest.mock import MagicMock
from fastapi import Request
from pivot.api.deps import get_auth

def test_get_auth():
    """Verify get_auth dependency properly extracts AuthService from app state."""
    app = MagicMock()
    app.state.auth = "mocked_auth_service"

    request = MagicMock(spec=Request)
    request.app = app

    assert get_auth(request) == "mocked_auth_service"

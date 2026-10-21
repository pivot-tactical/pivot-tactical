import pytest
from fastapi import WebSocket, status
from unittest.mock import AsyncMock, MagicMock, patch

from pivot.api.ws import websocket_endpoint

@pytest.mark.asyncio
async def test_origin_validation():
    # Malicious origin
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.headers = {"origin": "http://malicious.com"}

    await websocket_endpoint(mock_ws)
    mock_ws.close.assert_called_once_with(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Origin")
    mock_ws.accept.assert_not_called()

    # Valid origin
    mock_ws_valid = AsyncMock(spec=WebSocket)
    mock_ws_valid.headers = {"origin": "http://192.168.1.100:8080"}
    mock_ws_valid.cookies = {}
    mock_ws_valid.app.state.auth = None
    mock_ws_valid.app.state.manager = MagicMock()

    with patch("pivot.api.ws._trainee_session", new_callable=AsyncMock) as mock_trainee:
        await websocket_endpoint(mock_ws_valid)
        mock_ws_valid.accept.assert_called_once()
        mock_trainee.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin, allowed",
    [
        ("http://10.0.0.1:8080", True),
        ("https://172.16.0.5:8443", True),
        ("http://192.168.1.1:8080", True),
        ("http://127.0.0.1:5173", True),
        ("http://localhost:5173", True),
        ("http://8.8.8.8:8080", False),
        ("http://1.1.1.1", False),
        ("ftp://192.168.1.100", False),
        ("javascript:alert(1)", False),
        ("http://evil.com", False),
        ("http://192.168.1.100.evil.com", False),
    ],
)
async def test_origin_validation_extended(origin: str, allowed: bool):
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.headers = {"origin": origin}
    mock_ws.cookies = {}
    mock_ws.app.state.auth = None
    mock_ws.app.state.manager = MagicMock()

    with patch("pivot.api.ws._trainee_session", new_callable=AsyncMock):
        await websocket_endpoint(mock_ws)
        if allowed:
            mock_ws.accept.assert_called_once()
            mock_ws.close.assert_not_called()
        else:
            mock_ws.close.assert_called_once_with(
                code=status.WS_1008_POLICY_VIOLATION, reason="Invalid Origin"
            )
            mock_ws.accept.assert_not_called()

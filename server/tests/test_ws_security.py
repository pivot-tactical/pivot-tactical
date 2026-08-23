from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, status

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

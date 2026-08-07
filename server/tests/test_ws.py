import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from pivot.api.ws import _cancel, _instructor_session, _safe, _trainee_session
from pivot.core.radios import RadioBusyError


def test_cancel_none():
    _cancel(None)


def test_cancel_already_done():
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = True
    _cancel(mock_task)
    mock_task.cancel.assert_not_called()


def test_cancel_active_task():
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    _cancel(mock_task)
    mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_safe_success():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_action = MagicMock(return_value="test_payload")

    await _safe(mock_ws, "test_ok", mock_action)

    mock_ws.send_json.assert_called_once_with({"type": "test_ok", "payload": "test_payload"})
    mock_action.assert_called_once()


@pytest.mark.asyncio
async def test_safe_radio_busy_error():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_action = MagicMock(side_effect=RadioBusyError("Busy"))

    await _safe(mock_ws, "test_ok", mock_action)

    mock_ws.send_json.assert_called_once_with({"type": "error", "payload": {"detail": "Busy"}})


@pytest.mark.asyncio
async def test_safe_key_error():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_action = MagicMock(side_effect=KeyError("Missing Key"))

    await _safe(mock_ws, "test_ok", mock_action)

    mock_ws.send_json.assert_called_once_with(
        {"type": "error", "payload": {"detail": "'Missing Key'"}}
    )


@pytest.mark.asyncio
async def test_safe_value_error():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_action = MagicMock(side_effect=ValueError("Invalid Value"))

    await _safe(mock_ws, "test_ok", mock_action)

    mock_ws.send_json.assert_called_once_with(
        {"type": "error", "payload": {"detail": "Invalid Value"}}
    )


@pytest.mark.asyncio
async def test_trainee_session_disconnect():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.query_params.get.return_value = "test"
    mock_ws.receive.side_effect = WebSocketDisconnect()
    mock_manager = MagicMock()
    mock_manager.login.return_value = {"radio_id": "r1", "epoch": 123}

    await _trainee_session(mock_ws, mock_manager)

    mock_ws.receive.assert_called_once()
    mock_manager.disconnect.assert_called_once_with("test", epoch=123)


@pytest.mark.asyncio
async def test_instructor_session_disconnect():
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.receive.side_effect = WebSocketDisconnect()
    mock_manager = MagicMock()
    mock_manager.instructor_radios.return_value = [{"radio_id": "r1"}]

    await _instructor_session(mock_ws, mock_manager)

    mock_ws.receive.assert_called_once()

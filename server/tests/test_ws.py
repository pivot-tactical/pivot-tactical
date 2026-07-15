import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket

from pivot.api.ws import _cancel, _safe
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

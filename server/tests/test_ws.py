import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    ws = AsyncMock()
    action = MagicMock(return_value="test_payload")
    await _safe(ws, "test_ok", action)
    ws.send_json.assert_awaited_once_with({"type": "test_ok", "payload": "test_payload"})


@pytest.mark.asyncio
async def test_safe_error_radio_busy():
    ws = AsyncMock()
    action = MagicMock(side_effect=RadioBusyError("busy"))
    await _safe(ws, "test_ok", action)
    ws.send_json.assert_awaited_once_with({"type": "error", "payload": {"detail": "busy"}})


@pytest.mark.asyncio
async def test_safe_error_key_error():
    ws = AsyncMock()
    action = MagicMock(side_effect=KeyError("missing_key"))
    await _safe(ws, "test_ok", action)
    ws.send_json.assert_awaited_once_with({"type": "error", "payload": {"detail": "'missing_key'"}})


@pytest.mark.asyncio
async def test_safe_error_value_error():
    ws = AsyncMock()
    action = MagicMock(side_effect=ValueError("bad value"))
    await _safe(ws, "test_ok", action)
    ws.send_json.assert_awaited_once_with({"type": "error", "payload": {"detail": "bad value"}})

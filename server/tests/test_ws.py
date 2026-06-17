import asyncio
from unittest.mock import MagicMock

from pivot.api.ws import _cancel


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

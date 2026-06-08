"""Live runtime orchestration.

:class:`pivot.runtime.manager.SessionManager` is the control-plane brain shared
by the API (REST + WebSocket) and the browser instructor console. It owns the live radio
frequency map, the active band profile (including instructor scenario state), the
transmit lifecycle (PTT → crypto sync → on-air → recording → event), and the
pub/sub used to push state to connected clients.
"""

from pivot.runtime.manager import SessionManager, TerminalInfo

__all__ = ["SessionManager", "TerminalInfo"]

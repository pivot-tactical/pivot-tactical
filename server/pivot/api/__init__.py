"""HTTP + WebSocket API (spec §6).

* :mod:`pivot.api.app` — the FastAPI application factory, owning a
  :class:`~pivot.runtime.manager.SessionManager` on ``app.state`` and serving the
  built React frontend.
* :mod:`pivot.api.rest` — REST endpoints (§6.1).
* :mod:`pivot.api.ws` — the single ``/ws`` channel: state sync, PTT control and
  WebRTC signalling (§6.2, §6.3).
"""

from pivot.api.app import create_app

__all__ = ["create_app"]

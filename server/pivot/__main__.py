"""PIVOT entry point (spec §2.3).

On a normal Windows deployment the executable shows the PySide6 instructor window
on the main thread and runs FastAPI (HTTP + WebSocket + WebRTC signalling) on a
background thread. For headless/dev use (or when PySide6 is not installed) the
server runs in the foreground.

    python -m pivot                 # GUI + server (falls back to headless)
    python -m pivot --headless      # server only
    python -m pivot --port 9000     # override bind port
"""

from __future__ import annotations

import argparse
import socket
import threading

import uvicorn

from pivot.api.app import create_app
from pivot.config import Settings
from pivot.version import version_info


def _lan_ip() -> str:
    """Best-effort primary LAN IP to display for trainees (§3.1.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pivot", description="PIVOT radio voice trainer")
    p.add_argument("--headless", action="store_true", help="run the server without the GUI")
    p.add_argument("--host", default=None, help="bind host (default from settings)")
    p.add_argument("--port", type=int, default=None, help="bind port (default 8080)")
    p.add_argument("--data-dir", default=None, help="override the data directory")
    p.add_argument("--version", action="store_true", help="print version and exit")
    return p.parse_args(argv)


def _settings_from_args(args: argparse.Namespace) -> Settings:
    overrides: dict = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.data_dir:
        from pathlib import Path

        overrides["data_dir"] = Path(args.data_dir)
    return Settings(**overrides)


def run_server(settings: Settings) -> None:
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


def run_server_thread(settings: Settings) -> threading.Thread:
    app = create_app(settings)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="pivot-server")
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(version_info.title)
        return 0

    settings = _settings_from_args(args)
    print(f"PIVOT {version_info.version} — LAN address: http://{_lan_ip()}:{settings.port}")

    if args.headless:
        run_server(settings)
        return 0

    # Try to launch the GUI; fall back to headless if PySide6 is unavailable.
    try:
        from pivot.gui.app import run_gui
    except Exception as exc:  # ImportError or platform/display issue
        print(f"GUI unavailable ({exc}); running headless. Use --headless to silence.")
        run_server(settings)
        return 0

    run_server_thread(settings)
    return run_gui(settings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

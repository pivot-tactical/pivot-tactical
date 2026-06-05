"""PIVOT entry point — headless server.

The server runs headless: both the instructor and the trainees use a web
browser over the LAN (the instructor authenticates with a password). There is no
desktop GUI.

    python -m pivot                 # run the server
    python -m pivot --port 9000     # override the bind port
    python -m pivot --data-dir DIR  # override the data directory
"""

from __future__ import annotations

import argparse
import socket

import uvicorn

from pivot.api.app import create_app
from pivot.auth import DEFAULT_INSTRUCTOR_PASSWORD, AuthService
from pivot.config import Settings
from pivot.db.database import init_database
from pivot.runtime.manager import SessionManager
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
    p = argparse.ArgumentParser(prog="pivot", description="PIVOT radio voice trainer (headless)")
    p.add_argument("--host", default=None, help="bind host (default from settings)")
    p.add_argument("--port", type=int, default=None, help="bind port (default 8080)")
    p.add_argument("--data-dir", default=None, help="override the data directory")
    # Accepted but ignored: the server is always headless now.
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
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


def run_server(settings: Settings, manager: SessionManager) -> None:
    app = create_app(settings, manager=manager)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(version_info.title)
        return 0

    settings = _settings_from_args(args)
    db = init_database(settings)
    manager = SessionManager(db, settings)

    # Seed the instructor password on first run and warn if it is still default.
    auth = AuthService(db)
    auth.ensure_default()

    ip = _lan_ip()
    print(f"PIVOT {version_info.version} — open http://{ip}:{settings.port} in a browser")
    print("  Trainees: enter a callsign.  Instructor: 'Log in as instructor'.")
    if auth.is_default():
        print(
            f"  NOTE: instructor password is the default ('{DEFAULT_INSTRUCTOR_PASSWORD}'). "
            "Change it in Settings after logging in."
        )

    run_server(settings, manager)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

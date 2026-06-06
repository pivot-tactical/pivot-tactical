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
import sys

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
    # Windows tray: minimise to the notification area instead of a console
    # window. Defaults on for the packaged exe; --no-tray forces the console.
    p.add_argument("--tray", dest="tray", action="store_true", default=None,
                   help="run minimised to the Windows system tray")
    p.add_argument("--no-tray", dest="tray", action="store_false",
                   help="run in the console (disable the system tray)")
    # Out-of-band update apply (Linux): the systemd service runs this before
    # starting, swapping any staged update into place while the app is stopped.
    p.add_argument("--apply-staged", action="store_true",
                   help="apply a staged update and exit (used by the service)")
    p.add_argument("--version", action="store_true", help="print version and exit")
    return p.parse_args(argv)


def _use_tray(arg_tray: bool | None) -> bool:
    """Decide whether to host the server in the Windows system tray.

    Explicit --tray/--no-tray always wins; otherwise default to the tray only
    when running as the packaged Windows executable (a double-clicked app), not
    for ``python -m pivot`` development.
    """
    if sys.platform != "win32":
        return False
    if arg_tray is not None:
        return arg_tray
    return bool(getattr(sys, "frozen", False))


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


def _install_dir() -> "object":
    """The directory the app is installed in (the frozen exe's folder)."""
    from pathlib import Path

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Source checkout: the repo root is a sensible stand-in for dev testing.
    return Path(__file__).resolve().parents[2]


def _apply_staged(settings: Settings) -> int:
    """Swap any staged update into place, then exit (service ExecStartPre)."""
    from pivot.updates.manager import UpdateManager

    mgr = UpdateManager(version_info.version, settings.versions_dir)
    applied = mgr.apply_pending(_install_dir())
    if applied:
        print(f"Applied staged update {applied}.")
    else:
        print("No staged update pending.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(version_info.title)
        return 0

    settings = _settings_from_args(args)
    if args.apply_staged:
        return _apply_staged(settings)
    db = init_database(settings)
    manager = SessionManager(db, settings)

    # Seed the instructor password on first run and warn if it is still default.
    auth = AuthService(db)
    auth.ensure_default()

    ip = _lan_ip()
    url = f"http://{ip}:{settings.port}"
    print(f"PIVOT {version_info.version} — open {url} in a browser")
    print("  Trainees: enter a callsign.  Instructor: 'Log in as instructor'.")
    if auth.is_default():
        print(
            f"  NOTE: instructor password is the default ('{DEFAULT_INSTRUCTOR_PASSWORD}'). "
            "Change it in Settings after logging in."
        )

    if _use_tray(args.tray):
        # Headless on Windows: tuck the server into the notification area. The
        # tray menu offers Open / Copy address / Show log / Quit.
        from pivot.win_tray import run_with_tray

        run_with_tray(lambda: run_server(settings, manager), url,
                      tooltip=f"PIVOT {version_info.version} — {ip}:{settings.port}")
    else:
        run_server(settings, manager)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

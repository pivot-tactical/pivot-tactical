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
import os
import socket
import sys
from pathlib import Path

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
    # Out-of-band downgrade recovery: roll back to a retained version and exit.
    # Optional TAG selects which retained version; omitted = the most recent.
    p.add_argument("--rollback", nargs="?", const="", default=None,
                   metavar="TAG",
                   help="roll back to a retained version and exit (recovery)")
    # Detached relaunch helper (packaged exe with no supervisor): wait for the
    # given pid to exit, apply any staged update, then start the app again.
    p.add_argument("--relaunch-after", type=int, default=None,
                   help=argparse.SUPPRESS)
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
        overrides["data_dir"] = Path(args.data_dir)
    if getattr(sys, "frozen", False):
        # Packaged exe: anchor relative-path defaults to the exe's directory so
        # they don't depend on cwd, which is undefined when launched via a
        # Start-menu shortcut or a different working directory.
        exe_dir = Path(sys.executable).parent
        overrides.setdefault("data_dir", exe_dir / "data")
        overrides.setdefault("versions_dir", exe_dir / "versions")
    return Settings(**overrides)


def run_server(settings: Settings, manager: SessionManager) -> None:
    app = create_app(settings, manager=manager)
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    server = uvicorn.Server(config)

    # Let the browser restart endpoint stop us gracefully. We record the intent
    # so that, once uvicorn has shut down cleanly, we relaunch (or let the
    # supervisor do it) — applying any staged update on the way back up (§3.7.5).
    def request_restart() -> None:
        app.state.restart_requested = True
        server.should_exit = True

    app.state.request_restart = request_restart
    app.state.restart_requested = False

    server.run()

    if getattr(app.state, "restart_requested", False):
        from pivot.runtime.lifecycle import perform_relaunch, restart_mode

        print("Restart requested — bringing PIVOT back up…")
        perform_relaunch()
        # In "relaunch" mode a detached helper is waiting for this process to
        # exit before it can swap any staged update and start the new app.
        # When run_server is on a daemon thread (tray mode), returning here
        # won't exit the process — the Win32 tray message loop owns the main
        # thread and keeps it alive indefinitely. Force the whole process out
        # now so the relauncher can proceed.
        if restart_mode() == "relaunch":
            os._exit(0)


def _apply_staged(settings: Settings) -> int:
    """Swap any staged update into place, then exit (service ExecStartPre)."""
    from pivot.runtime.lifecycle import install_dir
    from pivot.updates.manager import UpdateManager

    mgr = UpdateManager(version_info.version, settings.versions_dir)
    applied = mgr.apply_pending(install_dir())
    if applied:
        print(f"Applied staged update {applied}.")
    else:
        print("No staged update pending.")
    return 0


def _rollback(settings: Settings, tag: str | None) -> int:
    """Roll the install back to a retained version and exit (out-of-band).

    Recovery for a bad update that won't even boot: run
    ``PIVOT-Tactical --rollback`` (optionally with a specific TAG) from the
    install folder, then start PIVOT normally. With no TAG it rolls back to the
    most recent retained version.
    """
    from pivot.runtime.lifecycle import install_dir
    from pivot.updates.manager import UpdateManager

    mgr = UpdateManager(version_info.version, settings.versions_dir)
    target = tag or mgr.previous_version()
    if target is None:
        print("No retained version to roll back to.")
        return 1
    mgr.stage_rollback(target, install_dir())
    applied = mgr.apply_pending(install_dir())
    if applied:
        print(f"Rolled back to {applied}. Start PIVOT normally to run it.")
        return 0
    print(f"Rollback to {target} failed (nothing applied).")
    return 1


def _open_relaunch_log(settings: Settings):
    """Open the relaunch helper's log file, or return None if it can't be made.

    The helper runs detached with no console, so its inherited stdout/stderr are
    dead — pointing them at a file makes every ``print`` (ours and the update
    swap's) both safe and, finally, debuggable after a failed restart.
    """
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return open(settings.data_dir / "relaunch.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return None


def _relaunch_after(pid: int, settings: Settings) -> int:
    """Wait for ``pid`` to exit, apply any staged update, then start the app.

    The detached helper a browser-initiated restart spawns on platforms without
    a supervisor (the packaged Windows exe). Running the swap only after the old
    process is gone lets the install files be replaced safely (§3.7.5).

    Detached means no usable stdout: a stray ``print`` would raise and kill the
    helper before it relaunches — which looked like "PIVOT closes on restart and
    never comes back". Redirect output to a log file, and always bring the app
    back, even if applying an update fails.
    """
    import traceback

    from pivot.runtime.lifecycle import spawn_app, wait_for_exit

    old_out, old_err = sys.stdout, sys.stderr
    log = _open_relaunch_log(settings)
    if log is not None:
        sys.stdout = sys.stderr = log
    try:
        print(f"[relaunch] waiting for pid {pid} to exit")
        wait_for_exit(pid)
        try:
            _apply_staged(settings)
        except Exception:  # a bad update swap must not stop us coming back up
            traceback.print_exc()
        print("[relaunch] starting PIVOT again")
        spawn_app()  # the freshly-applied app, with its own console (tray hides it)
        print("[relaunch] done")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        if log is not None:
            log.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(version_info.title)
        return 0

    settings = _settings_from_args(args)
    if args.apply_staged:
        return _apply_staged(settings)
    if args.rollback is not None:
        return _rollback(settings, args.rollback or None)
    if args.relaunch_after is not None:
        return _relaunch_after(args.relaunch_after, settings)
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

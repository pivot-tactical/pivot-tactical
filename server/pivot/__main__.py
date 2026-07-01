"""PIVOT entry point — headless server.

The server runs headless: both the instructor and the trainees use a web
browser over the LAN (the instructor authenticates with a password). There is no
desktop GUI.

    python -m pivot                 # run the server
    python -m pivot --port 9000     # override the bind port
    python -m pivot --data-dir DIR  # override the data directory
"""

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

# Printed before the slow imports below (uvicorn, pivot.api.app — which pulls in
# the DSP/transcription stack: numpy, scipy, faster-whisper, …) so the console
# shows *something* immediately on launch rather than sitting blank for several
# seconds, which reads as "PIVOT isn't starting" (flush: stdout may be
# block-buffered when it isn't a real console, e.g. the packaged exe).
print("PIVOT is starting — please wait…", flush=True)

import uvicorn  # noqa: E402

from pivot.api.app import create_app  # noqa: E402
from pivot.auth import DEFAULT_INSTRUCTOR_PASSWORD, AuthService  # noqa: E402
from pivot.config import Settings  # noqa: E402
from pivot.db.database import init_database  # noqa: E402
from pivot.runtime.manager import SessionManager  # noqa: E402
from pivot.runtime.redirect import serve_redirector  # noqa: E402
from pivot.version import version_info  # noqa: E402


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
    p.add_argument(
        "--tray",
        dest="tray",
        action="store_true",
        default=None,
        help="run minimised to the Windows system tray",
    )
    p.add_argument(
        "--no-tray",
        dest="tray",
        action="store_false",
        help="run in the console (disable the system tray)",
    )
    # Out-of-band update apply (Linux): the systemd service runs this before
    # starting, swapping any staged update into place while the app is stopped.
    p.add_argument(
        "--apply-staged",
        action="store_true",
        help="apply a staged update and exit (used by the service)",
    )
    # Out-of-band downgrade recovery: roll back to a retained version and exit.
    # Optional TAG selects which retained version; omitted = the most recent.
    p.add_argument(
        "--rollback",
        nargs="?",
        const="",
        default=None,
        metavar="TAG",
        help="roll back to a retained version and exit (recovery)",
    )
    # Detached relaunch helper (packaged exe with no supervisor): wait for the
    # given pid to exit, apply any staged update, then start the app again.
    p.add_argument("--relaunch-after", type=int, default=None, help=argparse.SUPPRESS)
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
        # Packaged exe: anchor paths to the install ROOT (the parent of the
        # versions/ tree), not the exe's own folder — under the side-by-side
        # layout the exe runs from versions/current or versions/app-<ver>, while
        # data and the version store live at the root and are shared across
        # versions. install_root() resolves this however we were launched.
        from pivot.runtime.lifecycle import install_root

        root = install_root()
        overrides.setdefault("data_dir", root / "data")
        overrides.setdefault("versions_dir", root / "versions")
    return Settings(**overrides)


def _run_uvicorn_coro(coro, config: uvicorn.Config) -> None:
    """Run ``coro`` to completion the same way ``uvicorn.Server.run`` would.

    Replicates ``Server.run``'s event-loop setup (uvloop where available)
    across the uvicorn versions we support: >=0.36 exposes
    ``Config.get_loop_factory``/``uvicorn._compat.asyncio_run``; older
    versions use ``Config.setup_event_loop`` + plain ``asyncio.run``.
    """
    get_loop_factory = getattr(config, "get_loop_factory", None)
    if get_loop_factory is not None:
        from uvicorn._compat import asyncio_run

        asyncio_run(coro, loop_factory=get_loop_factory())
    else:
        config.setup_event_loop()
        asyncio.run(coro)


async def _serve_with_redirect(server: uvicorn.Server, settings: Settings) -> None:
    """Run uvicorn on a loopback-only port behind the public HTTP->HTTPS redirector.

    The TLS server binds an ephemeral loopback port; the redirector owns the
    public ``settings.port``, proxying real TLS connections through to it and
    answering plain-HTTP ones with a redirect to ``https://``.
    """
    sock = server.config.bind_socket()
    https_port = sock.getsockname()[1]
    redirector = await serve_redirector(settings.host, settings.port, https_port)
    async with redirector:
        await server.serve(sockets=[sock])


def run_server(
    settings: Settings, manager: SessionManager, tls: tuple[Path, Path] | None = None
) -> None:
    app = create_app(settings, manager=manager, force_https=tls is not None)
    if tls is not None:
        certfile, keyfile = tls
        tls_kwargs = {"ssl_certfile": str(certfile), "ssl_keyfile": str(keyfile)}
        # Loopback + ephemeral port: the public port is owned by the redirector
        # (see _serve_with_redirect), which proxies TLS connections here.
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="info", **tls_kwargs)
    else:
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

    coro = _serve_with_redirect(server, settings) if tls is not None else server.serve()
    _run_uvicorn_coro(coro, config)

    if getattr(app.state, "restart_requested", False):
        from pivot.runtime.lifecycle import perform_relaunch, restart_mode

        print("Restart requested — bringing PIVOT back up…")
        perform_relaunch(settings)
        # In "relaunch" mode a detached helper is waiting for this process to
        # exit before it can swap any staged update and start the new app.
        # When run_server is on a daemon thread (tray mode), returning here
        # won't exit the process — the Win32 tray message loop owns the main
        # thread and keeps it alive indefinitely. Force the whole process out
        # now so the relauncher can proceed.
        if restart_mode() == "relaunch":
            os._exit(0)


def _apply_staged(settings: Settings) -> int:
    """Activate any staged update, then exit (service ExecStartPre).

    Side-by-side: "applying" is just re-pointing the ``current`` link at the
    already-installed ``app-<tag>`` folder — no file is copied or replaced.
    """
    from pivot.updates.manager import UpdateConfig, UpdateManager

    mgr = UpdateManager(UpdateConfig(version_info.version, settings.versions_dir))
    applied = mgr.apply_pending()
    if applied:
        print(f"Applied staged update {applied}.")
    else:
        print("No staged update pending.")
    return 0


def _apply_staged_for_relaunch(settings: Settings) -> None:
    """Apply a pending update during relaunch, elevating the flip if required.

    The junction flip needs admin rights in an all-users (Program Files) install
    — a junction re-pointed by a non-elevated process is rejected on traversal by
    Windows Redirection Guard (WinError 448). So when something is pending and we
    are not already elevated, re-run ``--apply-staged`` through a UAC prompt and
    let the elevated child do the flip. A plain restart (no pending marker) must
    never prompt, and platforms without this constraint (Linux service, dev runs,
    already-admin) apply directly in-process.
    """
    from pivot.runtime.lifecycle import is_elevated, run_elevated_apply
    from pivot.updates.manager import UpdateConfig, UpdateManager

    mgr = UpdateManager(UpdateConfig(version_info.version, settings.versions_dir))
    if mgr.read_pending_marker(mgr.pending_marker_path) is None:
        print("No staged update pending.")
        return
    if sys.platform == "win32" and getattr(sys, "frozen", False) and not is_elevated():
        # Run the flip from the staged build's exe (avoids depending on the
        # current link, which may itself be the broken/untrusted one we're
        # replacing). Fall back to this helper's own exe.
        exe = mgr.staged_app_exe(Path(sys.executable).name) or Path(sys.executable)
        print("[relaunch] applying staged update with elevation (UAC)…")
        code = run_elevated_apply(str(exe))
        print(f"[relaunch] elevated apply exited with {code}")
        return
    _apply_staged(settings)


def _rollback(settings: Settings, tag: str | None) -> int:
    """Roll the install back to a retained version and exit (out-of-band).

    Recovery for a bad update that won't even boot: run
    ``PIVOT-Tactical --rollback`` (optionally with a specific TAG) from the
    install folder, then start PIVOT normally. With no TAG it rolls back to the
    most recent retained version.
    """
    from pivot.updates.manager import UpdateConfig, UpdateManager

    mgr = UpdateManager(UpdateConfig(version_info.version, settings.versions_dir))
    target = tag or mgr.previous_version()
    if target is None:
        print("No retained version to roll back to.")
        return 1
    mgr.stage_rollback(target)
    applied = mgr.apply_pending()
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

    from pivot.runtime.lifecycle import app_exe, spawn_app, wait_for_exit

    old_out, old_err = sys.stdout, sys.stderr
    log = _open_relaunch_log(settings)
    if log is not None:
        sys.stdout = sys.stderr = log
    try:
        print(f"[relaunch] waiting for pid {pid} to exit")
        wait_for_exit(pid)
        try:
            _apply_staged_for_relaunch(settings)
        except Exception:  # a bad update swap must not stop us coming back up
            traceback.print_exc()
        print("[relaunch] starting PIVOT again")
        # Go through the `current` link so the freshly activated version is the
        # one that runs — not whatever build this helper happens to live in.
        spawn_app(app_exe(settings))
        print("[relaunch] done")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        if log is not None:
            log.close()


def _print_startup_message(
    ip: str, port: int, tls: tuple[Path, Path] | None, is_default_password: bool
) -> str:
    scheme = "https" if tls else "http"
    url = f"{scheme}://{ip}:{port}"
    print(f"PIVOT {version_info.version} — open {url} in a browser")
    print("  Trainees: enter a callsign.  Instructor: 'Log in as instructor'.")
    if tls:
        print(
            "  NOTE: the browser will warn that the connection isn't private — "
            "that's expected for a self-hosted LAN address (the certificate is "
            "self-signed). Choose Advanced → Proceed once; this is what lets the "
            "microphone work."
        )
    else:
        print(
            "  WARNING: couldn't set up a secure connection — the microphone may "
            "be unavailable in some browsers (notably Firefox) at this address."
        )
    if is_default_password:
        print(
            f"  NOTE: instructor password is the default ('{DEFAULT_INSTRUCTOR_PASSWORD}'). "
            "Change it in Settings after logging in."
        )
    return url


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
    # Browsers only grant microphone access in a secure context (https or
    # localhost) — over plain HTTP at a LAN address, Firefox never even shows
    # the permission prompt (`navigator.mediaDevices` is undefined there).
    # Self-signed TLS is the only option for an offline LAN tool, so generate
    # one covering this address; on any failure fall back to plain HTTP rather
    # than refuse to start.
    from pivot.runtime.tls import ensure_cert

    tls = ensure_cert(settings.data_dir / "tls", ip)
    url = _print_startup_message(ip, settings.port, tls, auth.is_default())

    if _use_tray(args.tray):
        # Headless on Windows: tuck the server into the notification area. The
        # tray menu offers Open / Copy address / Show log / Quit.
        from pivot.win_tray import run_with_tray

        run_with_tray(
            lambda: run_server(settings, manager, tls),
            url,
            tooltip=f"PIVOT {version_info.version} — {ip}:{settings.port}",
        )
    else:
        run_server(settings, manager, tls)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

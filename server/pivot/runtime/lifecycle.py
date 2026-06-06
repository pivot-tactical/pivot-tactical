"""Server self-restart that actually comes back (spec §3.7.5).

The instructor can restart PIVOT from the browser — chiefly to apply a staged
update without touching the server console. How the process is brought back up
depends on how it is hosted:

* ``systemd``  — the Linux service. ``Restart=always`` relaunches us when we
  exit and ``ExecStartPre=--apply-staged`` swaps any staged update first, so we
  only have to exit cleanly.
* ``relaunch`` — the packaged Windows exe, which has no supervisor. We spawn a
  small detached helper (the same exe with ``--relaunch-after <pid>``) that
  waits for us to exit, applies the staged update while the files are free, then
  starts the app again.
* ``exec``     — a source / dev run. We re-exec the interpreter in place.

The detection is deliberately conservative: only treat ourselves as supervised
when we can prove it (systemd sets ``INVOCATION_ID``), so we never exit into a
dead state expecting a relaunch that will not come.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# Windows process-creation flags (avoid importing for their values on POSIX).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def restart_mode() -> str:
    """Return how this process gets relaunched after it exits.

    One of ``"systemd"``, ``"relaunch"`` or ``"exec"``. An explicit
    ``PIVOT_RESTART_MODE`` env var overrides the auto-detection (useful for
    other supervisors, e.g. ``systemd`` to defer to any process manager that
    relaunches on exit).
    """
    override = os.environ.get("PIVOT_RESTART_MODE")
    if override:
        return override
    if os.environ.get("INVOCATION_ID"):  # set by systemd for the unit
        return "systemd"
    if getattr(sys, "frozen", False):
        return "relaunch"
    return "exec"


def wait_for_exit(pid: int, timeout: float = 60.0) -> None:
    """Block until ``pid`` has exited (or ``timeout`` elapses).

    Used by the relauncher to hold off the file swap until the old process is
    gone. On Windows this waits on a real process handle; on POSIX it polls
    ``kill(pid, 0)`` (never used to *signal* — Windows ``os.kill`` would
    terminate the target, so that path is avoided there).
    """
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        import ctypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return  # already gone
        try:
            kernel32.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000))
        finally:
            kernel32.CloseHandle(handle)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)


def spawn_relauncher() -> None:
    """Spawn the detached helper that relaunches us after we exit (frozen exe).

    The helper is this same executable invoked with ``--relaunch-after <pid>``;
    it waits for us, applies any staged update, then starts the app again.
    """
    exe = sys.executable
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([exe, "--relaunch-after", str(os.getpid())], **kwargs)


def perform_relaunch() -> None:
    """Bring the app back after a graceful stop, per :func:`restart_mode`.

    Called once the server has shut down. For ``systemd`` it is a no-op (the
    supervisor handles it); for ``exec`` it re-execs in place; for ``relaunch``
    it spawns the detached helper. Returns only in the systemd/relaunch cases —
    ``exec`` replaces the process image.
    """
    mode = restart_mode()
    if mode == "systemd":
        return
    if mode == "exec":
        os.execv(sys.executable, [sys.executable, *sys.argv])  # pragma: no cover
        return
    spawn_relauncher()

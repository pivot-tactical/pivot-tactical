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
from pathlib import Path


def install_root() -> Path:
    """The install root that holds ``versions/`` and ``data/`` (spec §3.7.5).

    Side-by-side layout: the running exe lives in ``<root>/versions/current/`` (a
    link) or ``<root>/versions/app-<ver>/``, so the root is the parent of the
    ``versions`` folder. Discover it by walking up from the exe — robust whether
    we were launched through the ``current`` link or directly from a version
    folder. Falls back to the exe's own folder (a legacy flat install) and, in a
    source checkout, to the repo root for dev testing.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.name == "versions":
                return parent.parent
        return exe.parent  # legacy flat layout
    return Path(__file__).resolve().parents[3]


# Windows process-creation flags (avoid importing for their values on POSIX).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010


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
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32
        # HANDLE is a 64-bit pointer on 64-bit Windows. Without explicit
        # restype/argtypes, ctypes marshals it as a 32-bit int and truncates
        # the value, producing a corrupt handle — WaitForSingleObject then
        # fails immediately (WAIT_FAILED) instead of actually waiting, so the
        # relauncher races ahead and starts the new process before the old one
        # has released the port/files (it then dies silently, headless).
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

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


def _relauncher_exe(settings) -> str:
    """Which exe should run the detached relaunch helper.

    When an update is staged, run the helper from the **new** ``app-<tag>``
    build, not the active one. That way the process performing the version flip
    lives outside the active version's folder, so nothing it needs is the file we
    are switching away from. With no staged update (a plain restart) the running
    exe is fine — there is no flip to do. Any trouble resolving the staged exe
    falls back to the running exe.
    """
    if settings is None:
        return sys.executable
    try:
        from pivot.updates.manager import UpdateManager
        from pivot.version import version_info

        mgr = UpdateManager(version_info.version, settings.versions_dir)
        staged_exe = mgr.staged_app_exe(Path(sys.executable).name)
        if staged_exe is not None:
            return str(staged_exe)
    except Exception:
        pass
    return sys.executable


def spawn_relauncher(settings=None) -> None:
    """Spawn the detached helper that relaunches us after we exit (frozen exe).

    The helper is invoked with ``--relaunch-after <pid>``; it waits for us,
    activates any staged/rolled-back version (a link flip — no file is replaced),
    then starts the app again. See :func:`_relauncher_exe` for which build runs
    it.
    """
    exe = _relauncher_exe(settings)
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([exe, "--relaunch-after", str(os.getpid())], **kwargs)


def app_exe(settings=None) -> str:
    """Path to the active app exe — what the relauncher should start.

    Always go through the ``current`` link so we launch whatever version is now
    active (after a flip). Falls back to the running exe for a legacy flat
    install or a dev run.
    """
    exe_name = Path(sys.executable).name
    if settings is not None:
        candidate = Path(settings.versions_dir) / "current" / exe_name
        if candidate.exists():
            return str(candidate)
    return sys.executable


def spawn_app(exe: str | None = None) -> None:
    """Start a fresh PIVOT process — the relaunched app after a restart.

    The relauncher that calls this is detached with no console (see
    :func:`spawn_relauncher`), so a child that merely inherited its handles would
    have no console either and would die on its first ``print()`` at startup.
    Give the new app its own console, exactly as a normal double-click launch
    gets — the tray then hides it — so its logging works and the tray's "show
    log" can still surface it. This is the process that keeps running; the
    relauncher exits once it has spawned us.

    ``exe`` defaults to this process's executable; the relauncher passes the
    active ``current`` exe so the freshly activated version is the one that runs.
    """
    target = exe or sys.executable
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        kwargs["creationflags"] = _CREATE_NEW_CONSOLE
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([target], **kwargs)


def perform_relaunch(settings=None) -> None:
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
    spawn_relauncher(settings)


def is_elevated() -> bool:
    """True if this process can write protected install locations.

    Admin on Windows / root on POSIX. The version flip re-points the ``current``
    junction inside the install dir; in an all-users (Program Files) install only
    an elevated process can create a *trusted* junction — a non-elevated one is
    blocked on traversal by Windows Redirection Guard (WinError 448).
    """
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX without geteuid
        return False


def run_elevated_apply(exe: str, timeout: float = 300.0) -> int:  # pragma: no cover - Windows-only
    """Re-run ``--apply-staged`` elevated (UAC) and wait for it to finish.

    Used during relaunch when a staged update or rollback is pending and we are
    not already admin: ``ShellExecuteEx`` with the ``runas`` verb raises the UAC
    prompt, the elevated child performs the junction flip (creating a *trusted*
    junction Windows will let anyone traverse), then we start the app normally.
    Returns the child's exit code (a non-zero / cancelled prompt leaves the
    pending marker in place so the next restart can retry).
    """
    import ctypes
    from ctypes import wintypes

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.c_void_p]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = "--apply-staged"
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError()

    handle = info.hProcess
    if not handle:
        return 0
    try:
        kernel32.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000))
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(handle)

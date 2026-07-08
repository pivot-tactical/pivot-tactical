"""Side-by-side install layout for professional-grade in-place upgrades (§3.7.5).

The flat "replace the install folder" model can't work on Windows: the running
exe and its loaded ``_internal`` DLLs are locked, so a swap that runs out of the
same folder fails with ``WinError 5`` and the app falls back to the old build.

Instead PIVOT uses the Chrome / VS Code (Squirrel) model — each version lives in
its own folder and a stable ``current`` link always points at the active one::

    <root>/
      versions/
        current            -> app-<ver>      (junction on Windows, symlink on POSIX)
        app-1.0.0-dev.61/   a full bundle
        app-1.0.0-dev.62/   a newer full bundle
        _staging/<tag>/...  download + extract scratch
        pending_update.json which app-<tag> to activate on next launch
      data/                 DB, recordings, logs — shared, never touched by updates

Shortcuts and the service point at ``versions/current/PIVOT-Tactical.exe``.

An update extracts a new ``app-<tag>`` sibling (nothing running there, nothing to
lock) and then atomically re-points ``current``. Rollback just re-points
``current`` at an older ``app-<ver>`` — instant and offline. No running file is
ever deleted or overwritten, so there is no lock to fight and no half-swapped
install to recover from. The whole subtree lives under ``versions/`` (granted
user-write by the installer), so updates need no elevation even for an all-users
install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pivot.version import SemVer

_APP_PREFIX = "app-"


def _remove_link(link: Path) -> None:
    """Remove a ``current`` link without touching the version it points at.

    Handles all three shapes the link can take across platforms: a POSIX
    symlink, a Windows directory junction (a reparse point that ``unlink`` can't
    remove but ``rmdir`` can), and — defensively — a real directory left behind
    by a botched earlier flip.
    """
    if not (link.exists() or link.is_symlink() or os.path.lexists(link)):
        return
    try:
        os.unlink(link)
        return
    except OSError:
        pass
    try:
        os.rmdir(link)  # an empty junction reparse point
        return
    except OSError:
        pass
    shutil.rmtree(link, ignore_errors=True)


def _make_link(target: Path, link: Path) -> None:
    """Point ``link`` at directory ``target``.

    POSIX uses a symlink. Windows prefers a symlink too, but creating one needs
    privilege/Developer Mode, so it falls back to a directory *junction* via
    ``mklink /J`` — which any user can create and which resolves transparently
    for shortcuts and the relaunch path.
    """
    if sys.platform == "win32":  # pragma: no cover - Windows-only
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except OSError:
            link_str = str(link)
            target_str = str(target)
            if '"' in link_str or '"' in target_str:
                raise ValueError("Paths cannot contain quotes") from None

            # cmd.exe /c requires manual quoting. Passing a single string bypasses
            # subprocess.list2cmdline which fails to quote shell operators (like &).
            cmd_line = f'cmd /c mklink /J "{link_str}" "{target_str}"'
            subprocess.run(
                cmd_line,
                check=True,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
    else:
        os.symlink(target, link)


class Layout:
    """Filesystem operations for the side-by-side versions tree (§3.7.5/§3.7.7)."""

    def __init__(self, versions_dir: Path) -> None:
        self.versions = Path(versions_dir)

    # -- well-known paths -------------------------------------------------- #

    @property
    def current_link(self) -> Path:
        return self.versions / "current"

    @property
    def staging(self) -> Path:
        return self.versions / "_staging"

    @property
    def marker(self) -> Path:
        return self.versions / "pending_update.json"

    def app_dir(self, tag: str) -> Path:
        return self.versions / f"{_APP_PREFIX}{tag}"

    # -- queries ----------------------------------------------------------- #

    def installed_versions(self) -> list[str]:
        """Tags of the ``app-<ver>`` folders present, newest semver first."""
        if not self.versions.is_dir():
            return []
        # Filter by name BEFORE is_dir(): the `current` link (and any other
        # non-app entry) must never be stat'd here. A junction re-pointed by a
        # non-elevated process is flagged untrusted by Windows Redirection Guard,
        # and is_dir() on it then raises WinError 448 — which would otherwise
        # crash pruning/discovery just for walking past it.
        tags = [
            p.name[len(_APP_PREFIX):]
            for p in self.versions.iterdir()
            if p.name.startswith(_APP_PREFIX) and p.is_dir()
        ]
        tags.sort(key=lambda t: SemVer.try_parse(t) or SemVer(0, 0, 0), reverse=True)
        return tags

    def active_version(self) -> str | None:
        """The version ``current`` resolves to, or None if it isn't set up."""
        try:
            target = self.current_link.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        name = target.name
        return name[len(_APP_PREFIX):] if name.startswith(_APP_PREFIX) else None

    def app_exe(self, tag: str, exe_name: str) -> Path | None:
        exe = self.app_dir(tag) / exe_name
        return exe if exe.exists() else None

    def current_exe(self, exe_name: str) -> Path:
        return self.current_link / exe_name

    # -- mutations --------------------------------------------------------- #

    def place_version(self, tag: str, bundle_root: Path) -> Path:
        """Install an extracted bundle as ``app-<tag>`` (a move, nothing locked)."""
        dest = self.app_dir(tag)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(bundle_root), str(dest))
        return dest

    def activate(self, tag: str) -> Path:
        """Re-point ``current`` at ``app-<tag>``. The atomic-ish version flip."""
        target = self.app_dir(tag)
        if not target.is_dir():
            raise ValueError(f"Version not installed: {tag}")
        self.versions.mkdir(parents=True, exist_ok=True)
        _remove_link(self.current_link)
        _make_link(target, self.current_link)
        return self.current_link

    def delete_version(self, tag: str) -> bool:
        """Remove an installed version's folder. Refuses the active one."""
        if tag not in self.installed_versions() or tag == self.active_version():
            return False
        shutil.rmtree(self.app_dir(tag), ignore_errors=True)
        return True

    def prune(self, keep: int, protect: tuple[str, ...] = ()) -> None:
        """Keep the newest ``keep`` versions (plus ``protect``); delete the rest."""
        keepset = set(self.installed_versions()[:keep]) | set(protect)
        active = self.active_version()
        if active:
            keepset.add(active)
        for tag in self.installed_versions():
            if tag not in keepset:
                shutil.rmtree(self.app_dir(tag), ignore_errors=True)

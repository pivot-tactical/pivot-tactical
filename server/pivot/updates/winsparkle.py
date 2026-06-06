"""Thin ctypes wrapper around WinSparkle, the in-app updater (spec §3.7).

WinSparkle (https://winsparkle.org, MIT) is the professional, audited update
engine for Windows desktop apps. It reads our signed appcast, verifies the
Ed25519 signature of the downloaded Inno Setup installer against the embedded
public key, runs the installer (which closes PIVOT, swaps the install, and
relaunches), and remembers the user's choices.

This module loads ``WinSparkle.dll`` (shipped beside the binary by the build)
and exposes a tiny, safe surface. Everything is guarded:

* On non-Windows, or when the DLL or the public key is absent, every call is a
  no-op and :func:`available` returns ``False`` — the rest of the app (and the
  Linux tarball update path) is unaffected.
* WinSparkle owns its own Win32 UI thread; we only configure it and ask it to
  check. We never block the server's event loop on it.

The appcast URL points at the ``appcast.xml`` published next to the installer on
each GitHub release. The instructor triggers a check from the browser Updates
panel; WinSparkle takes over from there on the server machine.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from pivot.updates import signing
from pivot.version import version_info

_dll: ctypes.CDLL | None = None
_initialised = False


def _find_dll() -> Path | None:
    """Locate WinSparkle.dll next to the frozen binary (or via env override)."""
    import os

    override = os.environ.get("PIVOT_WINSPARKLE_DLL", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    # PyInstaller unpacks data/binaries beside the executable (onedir) — the DLL
    # is bundled there by the spec. Fall back to the module dir for source runs.
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        cand = root / "WinSparkle.dll"
        if cand.is_file():
            return cand
    return None


def available() -> bool:
    """True only on Windows with the DLL present and a verification key set."""
    return (
        sys.platform.startswith("win")
        and signing.is_configured()
        and _find_dll() is not None
    )


def _load() -> ctypes.CDLL | None:
    global _dll
    if _dll is not None:
        return _dll
    dll_path = _find_dll()
    if dll_path is None:
        return None
    try:
        _dll = ctypes.CDLL(str(dll_path))
    except OSError:
        return None
    return _dll


def init(appcast_url: str) -> bool:
    """Configure WinSparkle (appcast, public key, app metadata). Idempotent.

    Returns False (and does nothing) when WinSparkle is not usable here.
    """
    global _initialised
    if not available() or not appcast_url:
        return False
    dll = _load()
    if dll is None:
        return False
    if _initialised:
        return True

    def _w(s: str) -> ctypes.c_wchar_p:
        return ctypes.c_wchar_p(s)

    dll.win_sparkle_set_appcast_url(ctypes.c_char_p(appcast_url.encode()))
    dll.win_sparkle_set_eddsa_public_key(ctypes.c_char_p(signing.public_key().encode()))
    dll.win_sparkle_set_app_details(
        _w("PIVOT"), _w("PIVOT-Tactical"), _w(version_info.version)
    )
    # We verify via EdDSA; DSA is disabled by not setting a DSA key.
    dll.win_sparkle_init()
    _initialised = True
    return True


def check_with_ui() -> bool:
    """Ask WinSparkle to check now and show its update UI if one is available."""
    if not _initialised:
        return False
    _load().win_sparkle_check_update_with_ui()  # type: ignore[union-attr]
    return True


def check_and_install() -> bool:
    """Check and, if an update exists, download + verify + install it.

    Uses WinSparkle's no-UI check that proceeds to install; the installer then
    restarts PIVOT. Returns False if WinSparkle is unusable.
    """
    if not _initialised:
        return False
    fn = getattr(
        _load(), "win_sparkle_check_update_without_ui_and_install", None
    ) or getattr(_load(), "win_sparkle_check_update_without_ui", None)
    if fn is None:
        return False
    fn()
    return True


def cleanup() -> None:
    """Tear WinSparkle down on shutdown (safe to call when never initialised)."""
    global _initialised
    if _initialised and _dll is not None:
        try:
            _dll.win_sparkle_cleanup()
        finally:
            _initialised = False

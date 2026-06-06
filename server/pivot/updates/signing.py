"""The Ed25519 public key WinSparkle uses to verify update installers (§3.7.5).

WinSparkle (and the appcast it reads) carry a base64 Ed25519 signature on every
enclosure; before running any downloaded installer, WinSparkle verifies it
against this public key. The matching private key lives only as a CI secret
(``PIVOT_EDDSA_PRIVATE_KEY``) and signs each installer in the release workflow
via ``packaging/sign_appcast.py``.

Replace ``PUBLIC_KEY`` with the public half printed by
``python packaging/sign_appcast.py keygen`` once you mint the project's key.
It can also be overridden at runtime with the ``PIVOT_EDDSA_PUBLIC_KEY``
environment variable (handy for staging feeds). An empty key disables WinSparkle
verification wiring — the app then simply offers no in-app Windows updates.
"""

from __future__ import annotations

import os

# Base64 of the raw 32-byte Ed25519 public key. Empty until the project key is
# minted; CI/release docs cover the one-off keygen step (REBUILD/RELEASING).
_EMBEDDED_PUBLIC_KEY = ""


def public_key() -> str:
    """The active public key — env override wins, else the embedded constant."""
    return os.environ.get("PIVOT_EDDSA_PUBLIC_KEY", "").strip() or _EMBEDDED_PUBLIC_KEY


def is_configured() -> bool:
    """True when a verification key is available (in-app Windows updates wired)."""
    return bool(public_key())

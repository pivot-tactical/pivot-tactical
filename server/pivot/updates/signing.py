"""Ed25519 signature verification for downloaded updates (§3.7.5).

Authenticity for the in-app updater: the release workflow signs every published
asset with the project's Ed25519 *private* key (held only as the
``PIVOT_EDDSA_PRIVATE_KEY`` CI secret) and publishes a ``<asset>.sig`` sidecar.
Before staging any download, the app verifies that signature against the *public*
key embedded here — so a tampered or unofficial build is rejected even if its
SHA-256 sidecar matches.

Replace ``_EMBEDDED_PUBLIC_KEY`` with the public half printed by
``python packaging/sign_appcast.py keygen`` when the project key is minted. It
can be overridden at runtime with ``PIVOT_EDDSA_PUBLIC_KEY`` (handy for staging
feeds). An empty key disables verification — the app then trusts the SHA-256
integrity check alone.
"""

from __future__ import annotations

import base64
import os

# Base64 of the raw 32-byte Ed25519 public key. CI/release docs cover the
# one-off keygen step (REBUILD/RELEASING).
_EMBEDDED_PUBLIC_KEY = "i6e2oZpNHyoI5v7mAUA36tYBku354FlLjXZt5Hbyvoc="


def public_key() -> str:
    """The active public key — env override wins, else the embedded constant."""
    return os.environ.get("PIVOT_EDDSA_PUBLIC_KEY", "").strip() or _EMBEDDED_PUBLIC_KEY


def is_configured() -> bool:
    """True when a verification key is available (signature checking is wired)."""
    return bool(public_key())


def verify_bytes(data: bytes, signature_b64: str, public_b64: str | None = None) -> bool:
    """Verify a base64 Ed25519 signature of ``data`` against the public key."""
    pub = (public_b64 or public_key()).strip()
    if not pub or not signature_b64:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub))
        key.verify(base64.b64decode(signature_b64), data)
        return True
    except Exception:  # any failure (bad sig, bad key, missing dep) -> untrusted
        return False


def verify_file(path, signature_b64: str, public_b64: str | None = None) -> bool:
    """Verify the signature of a file's bytes (the downloaded archive)."""
    from pathlib import Path

    return verify_bytes(Path(path).read_bytes(), signature_b64, public_b64)

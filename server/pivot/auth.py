"""Instructor authentication (password + bearer tokens).

**Deviation from spec §8.4 (deliberate, per product direction):** the server is
headless and the instructor operates from a browser over the LAN, so instructor
controls are gated by a **password** and a per-session **bearer token** rather
than by loopback-only access. Trainees remain unauthenticated — a callsign only.

The password is stored only as a salted PBKDF2-HMAC-SHA256 hash in the ``config``
table (never in plaintext), seeded to a default on first run and changeable from
instructor mode. Tokens are random, held in memory, and all invalidated when the
password changes. No third-party crypto dependency is used (stdlib ``hashlib``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from pivot.db.config_store import ConfigStore
from pivot.db.database import Database

# Default instructor password, seeded on first run. The UI nudges the instructor
# to change it while it is still the default (see AuthService.is_default).
DEFAULT_INSTRUCTOR_PASSWORD = "instructor"

_PBKDF2_ROUNDS = 200_000
_CONFIG_KEY = "instructor_password_hash"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def hash_password(password: str, salt: bytes | None = None, rounds: int = _PBKDF2_ROUNDS) -> str:
    """Return a ``pbkdf2_sha256$rounds$salt$hash`` encoded string."""
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify a password against an encoded hash."""
    try:
        algo, rounds, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt_b64), int(rounds))
        return hmac.compare_digest(dk, _unb64(hash_b64))
    except (ValueError, TypeError):
        return False


class AuthService:
    """Password store + instructor bearer-token registry."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._tokens: set[str] = set()

    # -- password ---------------------------------------------------------- #

    def ensure_default(self, default_password: str = DEFAULT_INSTRUCTOR_PASSWORD) -> None:
        """Seed the instructor password hash on first run if not already set."""
        with self.db.session() as s:
            cfg = ConfigStore(s)
            if not cfg.get(_CONFIG_KEY):
                cfg.set(_CONFIG_KEY, hash_password(default_password))

    def _stored_hash(self) -> str:
        with self.db.session() as s:
            return str(ConfigStore(s).get(_CONFIG_KEY, "") or "")

    def verify(self, password: str) -> bool:
        encoded = self._stored_hash()
        return bool(encoded) and verify_password(password, encoded)

    def set_password(self, new_password: str) -> None:
        with self.db.session() as s:
            ConfigStore(s).set(_CONFIG_KEY, hash_password(new_password))
        # A password change logs out every existing instructor session.
        self._tokens.clear()

    def is_default(self) -> bool:
        """True while the password is still the shipped default (UI warns)."""
        return self.verify(DEFAULT_INSTRUCTOR_PASSWORD)

    # -- tokens ------------------------------------------------------------ #

    def login(self, password: str) -> str | None:
        """Verify a password and, on success, issue a bearer token."""
        return self.issue_token() if self.verify(password) else None

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens.add(token)
        return token

    def validate(self, token: str | None) -> bool:
        return bool(token) and token in self._tokens

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.discard(token)

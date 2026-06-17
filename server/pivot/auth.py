"""Instructor authentication (password + bearer tokens).

**Deviation from spec §8.4 (deliberate, per product direction):** the server is
headless and the instructor operates from a browser over the LAN, so instructor
controls are gated by a **password** and a **bearer token** rather than by
loopback-only access. Trainees remain unauthenticated — a callsign only.

The password is stored only as a salted PBKDF2-HMAC-SHA256 hash in the ``config``
table (never in plaintext), seeded to a default on first run and changeable from
instructor mode.

Tokens are **stateless and signed** (HMAC-SHA256) with an embedded expiry, so a
token issued before a server restart keeps working afterwards — the instructor's
browser is not logged out by a restart or a page refresh (a hard requirement: a
running scenario must survive both). The signing secret lives in the ``config``
table, so it persists across restarts; rotating it (on a password change)
invalidates every outstanding token at once. No third-party crypto dependency is
used (stdlib ``hashlib``/``hmac``).
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

from pivot.db.config_store import ConfigStore
from pivot.db.database import Database

# Default instructor password, seeded on first run. The UI nudges the instructor
# to change it while it is still the default (see AuthService.is_default).
DEFAULT_INSTRUCTOR_PASSWORD = "instructor"

_PBKDF2_ROUNDS = 200_000
_CONFIG_KEY = "instructor_password_hash"
_SECRET_KEY = "instructor_token_secret"

# How long an issued token stays valid. Short by design — long enough to bridge a
# refresh or a restart (and to be refreshed while the console is open), not a
# long-lived credential. The frontend slides it by re-issuing while active.
TOKEN_TTL_SECONDS = 60 * 60  # 1 hour


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
    """Password store + stateless instructor bearer-token signer."""

    def __init__(self, db: Database, token_ttl: int = TOKEN_TTL_SECONDS) -> None:
        self.db = db
        self.token_ttl = token_ttl
        # Best-effort, process-local revocation (explicit logout). Stateless
        # tokens can't be un-issued across a restart, but they expire on their
        # own within token_ttl, which is acceptable for a LAN training tool.
        self._revoked: set[str] = set()

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
        # A password change logs out every existing instructor session by
        # rotating the signing secret (so all outstanding tokens stop verifying).
        self._rotate_secret()
        self._revoked.clear()

    def is_default(self) -> bool:
        """True while the password is still the shipped default (UI warns)."""
        return self.verify(DEFAULT_INSTRUCTOR_PASSWORD)

    # -- token signing secret ---------------------------------------------- #

    def _secret(self) -> bytes:
        """The persisted HMAC signing secret, generated once on first use.

        Stored in the ``config`` table so it survives restarts — that is what
        lets a token issued before a restart still validate afterwards.
        """
        with self.db.session() as s:
            cfg = ConfigStore(s)
            stored = cfg.get(_SECRET_KEY)
            if stored:
                return _unb64(str(stored))
            secret = os.urandom(32)
            cfg.set(_SECRET_KEY, _b64(secret))
            return secret

    def _rotate_secret(self) -> None:
        with self.db.session() as s:
            ConfigStore(s).set(_SECRET_KEY, _b64(os.urandom(32)))

    # -- tokens ------------------------------------------------------------ #

    def login(self, password: str) -> str | None:
        """Verify a password and, on success, issue a bearer token."""
        return self.issue_token() if self.verify(password) else None

    def issue_token(self) -> str:
        """Mint a signed ``v1.<exp>.<nonce>.<sig>`` token valid for token_ttl.

        The nonce keeps successive tokens distinct (so a refreshed token differs
        from the one it replaces) without needing any server-side storage.
        """
        exp = int(time.time()) + self.token_ttl
        nonce = secrets.token_urlsafe(8)
        payload = f"v1.{exp}.{nonce}"
        return f"{payload}.{self._sign(payload)}"

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()

    def validate(self, token: str | None) -> bool:
        if not token or token in self._revoked:
            return False
        try:
            version, exp_s, nonce, sig = token.split(".")
        except ValueError:
            return False
        if version != "v1":
            return False
        payload = f"{version}.{exp_s}.{nonce}"
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        try:
            return int(exp_s) > int(time.time())
        except ValueError:
            return False

    def refresh(self, token: str | None) -> str | None:
        """Issue a fresh token if ``token`` is currently valid (sliding session)."""
        return self.issue_token() if self.validate(token) else None

    def revoke(self, token: str | None) -> None:
        if token:
            self._revoked.add(token)

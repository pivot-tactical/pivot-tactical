"""Tests for instructor authentication (password + bearer tokens)."""

from pivot.auth import (
    DEFAULT_INSTRUCTOR_PASSWORD,
    AuthService,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    encoded = hash_password("s3cret")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", encoded) is True
    assert verify_password("wrong", encoded) is False


def test_hash_is_salted():
    # Two hashes of the same password differ (random salt) but both verify.
    a = hash_password("same")
    b = hash_password("same")
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_verify_rejects_malformed():
    assert verify_password("x", "") is False
    assert verify_password("x", "not-an-encoded-hash") is False


def test_ensure_default_seeds_password(database):
    auth = AuthService(database)
    auth.ensure_default()
    assert auth.verify(DEFAULT_INSTRUCTOR_PASSWORD) is True
    assert auth.is_default() is True


def test_ensure_default_does_not_overwrite(database):
    auth = AuthService(database)
    auth.ensure_default()
    auth.set_password("changed-password")
    auth.ensure_default()  # must not reset to the default
    assert auth.verify("changed-password") is True
    assert auth.is_default() is False


def test_login_issues_token(database):
    auth = AuthService(database)
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    assert token and auth.validate(token) is True
    assert auth.login("wrong") is None


def test_validate_and_revoke(database):
    auth = AuthService(database)
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    assert auth.validate(token) is True
    auth.revoke(token)
    assert auth.validate(token) is False
    assert auth.validate(None) is False
    assert auth.validate("bogus") is False


def test_password_change_invalidates_tokens(database):
    auth = AuthService(database)
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    assert auth.validate(token) is True
    auth.set_password("new-password")
    # Old token no longer valid; old password rejected; new one works.
    assert auth.validate(token) is False
    assert auth.verify(DEFAULT_INSTRUCTOR_PASSWORD) is False
    assert auth.verify("new-password") is True


def test_token_survives_server_restart(database):
    """A token issued before a restart must still validate afterwards — the
    instructor's browser is not logged out by a restart. Modelled by a fresh
    AuthService over the same DB (the signing secret is persisted there)."""
    issuer = AuthService(database)
    issuer.ensure_default()
    token = issuer.login(DEFAULT_INSTRUCTOR_PASSWORD)
    assert token is not None

    # New process: a brand-new AuthService instance, same database.
    after_restart = AuthService(database)
    assert after_restart.validate(token) is True


def test_expired_token_is_rejected(database):
    auth = AuthService(database, token_ttl=-1)  # already expired on issue
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    assert auth.validate(token) is False


def test_refresh_issues_a_new_valid_token(database):
    auth = AuthService(database)
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    refreshed = auth.refresh(token)
    assert refreshed and refreshed != token
    assert auth.validate(refreshed) is True
    # Refreshing an invalid token yields nothing.
    assert auth.refresh("bogus") is None


def test_tampered_token_is_rejected(database):
    auth = AuthService(database)
    auth.ensure_default()
    token = auth.login(DEFAULT_INSTRUCTOR_PASSWORD)
    version, exp_s, nonce, sig = token.split(".")
    # Push the expiry far out but keep the original signature — must not verify.
    forged = f"{version}.{int(exp_s) + 10_000_000}.{nonce}.{sig}"
    assert auth.validate(forged) is False

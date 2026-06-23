"""Tests for the signing functionality in pivot.updates.signing."""

from unittest.mock import patch

from pivot.updates.signing import is_configured, verify_bytes, public_key, _EMBEDDED_PUBLIC_KEY


def test_public_key_default(monkeypatch):
    """If no environment variable is set, it should return the embedded key."""
    monkeypatch.delenv("PIVOT_EDDSA_PUBLIC_KEY", raising=False)
    assert public_key() == _EMBEDDED_PUBLIC_KEY

def test_public_key_env_override(monkeypatch):
    """If the environment variable is set, it should be used (and stripped)."""
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", "  custom_env_key  ")
    assert public_key() == "custom_env_key"

def test_public_key_empty_env(monkeypatch):
    """If the environment variable is empty or only whitespace, it should fall back to the embedded key."""
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", "   ")
    assert public_key() == _EMBEDDED_PUBLIC_KEY

def test_verify_bytes_empty_signature():
    """An empty signature string should immediately return False."""
    assert verify_bytes(b"data", "") is False

def test_verify_bytes_empty_public_key():
    """A whitespace-only public key string should evaluate to empty after strip and return False."""
    assert verify_bytes(b"data", "some_signature", "   ") is False





def test_is_configured_true():
    """is_configured should be True when public_key returns a truthy value."""
    with patch("pivot.updates.signing.public_key", return_value="some_key"):
        assert is_configured() is True

def test_is_configured_false():
    """is_configured should be False when public_key returns an empty string."""
    with patch("pivot.updates.signing.public_key", return_value=""):
        assert is_configured() is False

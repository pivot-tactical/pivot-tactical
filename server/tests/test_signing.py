"""Tests for the signing functionality in pivot.updates.signing."""

import base64
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pivot.updates.signing import (
    _EMBEDDED_PUBLIC_KEY,
    is_configured,
    public_key,
    verify_bytes,
    verify_file,
)


def test_verify_file_delegates(tmp_path, monkeypatch):
    """verify_file should read the file and pass its contents to verify_bytes."""
    test_file = tmp_path / "test_update.zip"
    test_file.write_bytes(b"file_content")

    # Mock verify_bytes to simply check its arguments
    def mock_verify_bytes(data, signature_b64, public_b64=None):
        assert data == b"file_content"
        assert signature_b64 == "test_sig"
        assert public_b64 == "test_pub"
        return True

    monkeypatch.setattr("pivot.updates.signing.verify_bytes", mock_verify_bytes)

    assert verify_file(test_file, "test_sig", "test_pub") is True


def test_verify_file_propagates_false(tmp_path, monkeypatch):
    """verify_file should return False if verify_bytes returns False."""
    test_file = tmp_path / "test_update.zip"
    test_file.write_bytes(b"bad_content")

    def mock_verify_bytes(data, signature_b64, public_b64=None):
        return False

    monkeypatch.setattr("pivot.updates.signing.verify_bytes", mock_verify_bytes)

    assert verify_file(test_file, "bad_sig") is False


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


def test_verify_bytes_valid():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    data = b"hello world"
    sig = private_key.sign(data)

    sig_b64 = base64.b64encode(sig).decode("utf-8")
    pub_b64 = base64.b64encode(public_key.public_bytes_raw()).decode("utf-8")

    assert verify_bytes(data, sig_b64, pub_b64) is True


def test_verify_bytes_invalid():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    data = b"hello world"
    sig = private_key.sign(data)

    # modify data
    sig_b64 = base64.b64encode(sig).decode("utf-8")
    pub_b64 = base64.b64encode(public_key.public_bytes_raw()).decode("utf-8")

    assert verify_bytes(b"hello world modified", sig_b64, pub_b64) is False


def test_verify_file_real(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    data = b"file contents"
    sig = private_key.sign(data)

    sig_b64 = base64.b64encode(sig).decode("utf-8")
    pub_b64 = base64.b64encode(public_key.public_bytes_raw()).decode("utf-8")

    f = tmp_path / "update.zip"
    f.write_bytes(data)

    assert verify_file(f, sig_b64, pub_b64) is True


def test_verify_file_real_invalid(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    data = b"file contents"
    sig = private_key.sign(data)

    sig_b64 = base64.b64encode(sig).decode("utf-8")
    pub_b64 = base64.b64encode(public_key.public_bytes_raw()).decode("utf-8")

    f = tmp_path / "update.zip"
    f.write_bytes(b"tampered contents")

    assert verify_file(f, sig_b64, pub_b64) is False

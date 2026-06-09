"""Tests for the signing functionality in pivot.updates.signing."""

from pivot.updates.signing import verify_bytes


def test_verify_bytes_empty_signature():
    """An empty signature string should immediately return False."""
    assert verify_bytes(b"data", "") is False

def test_verify_bytes_empty_public_key():
    """A whitespace-only public key string should evaluate to empty after strip and return False."""
    assert verify_bytes(b"data", "some_signature", "   ") is False

"""Self-signed TLS for the secure-context fix (mic capture over the LAN)."""

from __future__ import annotations

import ipaddress

from cryptography import x509

from pivot.runtime.tls import ensure_cert


def _saved_ips(cert_path) -> set:
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(san.get_values_for_type(x509.IPAddress))


def test_ensure_cert_generates_and_covers_the_lan_ip(tmp_path):
    result = ensure_cert(tmp_path / "tls", "192.168.1.50")
    assert result is not None
    cert_path, key_path = result
    assert cert_path.exists() and key_path.exists()

    ips = _saved_ips(cert_path)
    assert ipaddress.ip_address("192.168.1.50") in ips
    assert ipaddress.ip_address("127.0.0.1") in ips


def test_ensure_cert_reuses_existing_cert_for_same_ip(tmp_path):
    tls_dir = tmp_path / "tls"
    first = ensure_cert(tls_dir, "192.168.1.50")
    second = ensure_cert(tls_dir, "192.168.1.50")

    assert first == second
    assert first[0].read_bytes() == second[0].read_bytes()  # not regenerated


def test_ensure_cert_regenerates_when_the_lan_ip_changes(tmp_path):
    tls_dir = tmp_path / "tls"
    first = ensure_cert(tls_dir, "192.168.1.50")
    before = first[0].read_bytes()

    second = ensure_cert(tls_dir, "10.0.0.7")
    after = second[0].read_bytes()

    assert before != after  # a fresh cert was minted for the new address
    ips = _saved_ips(second[0])
    assert ipaddress.ip_address("10.0.0.7") in ips
    assert ipaddress.ip_address("192.168.1.50") not in ips


def test_ensure_cert_returns_none_when_dir_cannot_be_created(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")  # mkdir(tls_dir) must fail under this

    assert ensure_cert(blocker / "tls", "192.168.1.50") is None

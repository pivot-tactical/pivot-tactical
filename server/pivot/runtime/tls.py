"""Self-signed TLS so the browser grants a secure context (mic capture).

Browsers only expose ``navigator.mediaDevices``/``getUserMedia`` in a *secure
context* — ``https:`` or ``localhost``. PIVOT is reached over plain HTTP at a
LAN address (e.g. ``http://192.168.1.20:8080``), which is not a secure context
in any spec-compliant browser: Firefox enforces this strictly, so
``navigator.mediaDevices`` is simply ``undefined`` there and the microphone
permission prompt never appears (Chrome only "looks" fine to developers because
they test from ``localhost``, which is whitelisted).

Since PIVOT is an offline, LAN-only tool, a CA-signed certificate isn't an
option — so we generate and persist a self-signed one covering the detected LAN
IP (plus localhost). The browser shows a one-time "connection isn't private"
warning that the user clicks through (Advanced → Proceed); the origin is then a
secure context and the microphone prompt works exactly as it would over HTTPS
anywhere else.
"""

from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_CERT_NAME = "cert.pem"
_KEY_NAME = "key.pem"
_VALIDITY_DAYS = 3650
_RENEW_WITHIN_DAYS = 30  # regenerate a bit before expiry rather than at the wire
_LOOPBACK = ipaddress.ip_address("127.0.0.1")


def _generate(ip: str | None) -> tuple[bytes, bytes]:
    """Build a fresh self-signed cert/key pair covering ``ip`` (and localhost)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PIVOT Tactical (self-signed)")])
    san: list[x509.GeneralName] = [x509.DNSName("localhost"), x509.IPAddress(_LOOPBACK)]
    if ip:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            addr = None
        if addr is not None and addr != _LOOPBACK:
            san.append(x509.IPAddress(addr))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _still_good(cert_pem: bytes, ip: str | None) -> bool:
    """True if the persisted certificate already covers ``ip`` and isn't expiring soon.

    Re-used across restarts so the browser doesn't make trainees click through
    the warning again every time — only the LAN IP changing (DHCP) or the
    certificate nearing expiry forces a fresh one.
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
        soon = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=_RENEW_WITHIN_DAYS)
        if cert.not_valid_after_utc < soon:
            return False
        if not ip:
            return True
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return ipaddress.ip_address(ip) in san.get_values_for_type(x509.IPAddress)
    except Exception:
        return False


def ensure_cert(tls_dir: Path, ip: str | None) -> tuple[Path, Path] | None:
    """Return ``(certfile, keyfile)`` covering ``ip``, generating/renewing as needed.

    Persisted under ``tls_dir`` (the data directory, so it survives updates and
    rollbacks) and reused across restarts. Returns ``None`` — falling back to
    plain HTTP — if ``tls_dir`` can't be written to.
    """
    cert_path = tls_dir / _CERT_NAME
    key_path = tls_dir / _KEY_NAME
    try:
        if cert_path.exists() and key_path.exists() and _still_good(cert_path.read_bytes(), ip):
            return cert_path, key_path
        tls_dir.mkdir(parents=True, exist_ok=True)
        cert_pem, key_pem = _generate(ip)
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        try:
            key_path.chmod(0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX permissions
        return cert_path, key_path
    except OSError:
        return None

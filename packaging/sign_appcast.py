"""Sign release installers and emit a WinSparkle-compatible appcast (spec §3.7).

PIVOT's professional update path on Windows is an Inno Setup installer fetched
and applied by **WinSparkle** (MIT). WinSparkle reads an *appcast* — an RSS 2.0
feed with the Sparkle namespace — and, before running any installer, verifies an
**Ed25519 (EdDSA) signature** of the downloaded file against a public key
embedded in the app. This module is the *build-time* counterpart: it signs the
installer and renders that appcast.

Everything here is pure and unit-tested. It runs in the release workflow only —
the private key never leaves CI (it is a repository secret), and the public key
is the only half embedded in the shipped binary. The signing dependency
(`cryptography`, Apache-2.0/BSD) is a build tool, not a runtime dependency, so it
adds nothing to the distributed binary's licence surface.

    # one-off, locally: mint a key pair (store the private half as a CI secret)
    python packaging/sign_appcast.py keygen

    # in CI, on a tag:
    PIVOT_EDDSA_PRIVATE_KEY=... python packaging/sign_appcast.py appcast \
        --installer dist/PIVOT-Tactical-Setup.exe \
        --version 1.2.0 \
        --url https://github.com/<owner>/<repo>/releases/download/v1.2.0/PIVOT-Tactical-Setup.exe \
        --notes-file notes.html \
        --out appcast.xml
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"


# --------------------------------------------------------------------------- #
# Ed25519 keys & signatures (WinSparkle's `sparkle:edSignature` scheme)
# --------------------------------------------------------------------------- #


def generate_keypair() -> tuple[str, str]:
    """Mint a new Ed25519 key pair as ``(private_b64, public_b64)``.

    Both halves are the raw 32-byte keys, standard-base64 encoded — the exact
    form WinSparkle's ``win_sparkle_set_eddsa_public_key`` expects for the public
    half. Run once; keep the private half secret (a CI secret), ship the public.
    """
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def public_key_for(private_b64: str) -> str:
    """Derive the base64 public key from a base64 private key."""
    priv = _load_private(private_b64)
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(pub_raw).decode()


def _load_private(private_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))


def _load_public(public_b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))


def sign_bytes(data: bytes, private_b64: str) -> str:
    """Return the base64 Ed25519 signature of ``data``."""
    return base64.b64encode(_load_private(private_b64).sign(data)).decode()


def sign_file(path: Path, private_b64: str) -> str:
    """Sign a file's bytes (the installer enclosure WinSparkle will download)."""
    return sign_bytes(Path(path).read_bytes(), private_b64)


def verify_bytes(data: bytes, signature_b64: str, public_b64: str) -> bool:
    """Verify a base64 signature against the public key (the client-side check)."""
    try:
        _load_public(public_b64).verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def verify_file(path: Path, signature_b64: str, public_b64: str) -> bool:
    return verify_bytes(Path(path).read_bytes(), signature_b64, public_b64)


# --------------------------------------------------------------------------- #
# Appcast rendering
# --------------------------------------------------------------------------- #


@dataclass
class AppcastItem:
    """One downloadable build in the appcast feed."""

    version: str
    url: str
    length: int
    ed_signature: str
    pub_date: str            # RFC-822, e.g. "Mon, 06 Jun 2026 12:00:00 +0000"
    notes_html: str = ""
    os: str = "windows"
    min_os_version: str = ""  # e.g. "10.0.0" to gate on Windows 10+


def rfc822(dt: _dt.datetime) -> str:
    """Format a datetime as the RFC-822 date the appcast <pubDate> uses."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def build_appcast(title: str, items: list[AppcastItem], *, link: str = "") -> str:
    """Render the full appcast XML for ``items`` (newest first by convention)."""
    parts: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<rss version="2.0" xmlns:sparkle="{SPARKLE_NS}">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
    ]
    if link:
        parts.append(f"    <link>{escape(link)}</link>")
    for it in items:
        enc_attrs = [
            f"url={quoteattr(it.url)}",
            f'sparkle:version={quoteattr(it.version)}',
            f'sparkle:shortVersionString={quoteattr(it.version)}',
            f'sparkle:os={quoteattr(it.os)}',
            f'length={quoteattr(str(it.length))}',
            'type="application/octet-stream"',
            f'sparkle:edSignature={quoteattr(it.ed_signature)}',
        ]
        parts += [
            "    <item>",
            f"      <title>{escape('Version ' + it.version)}</title>",
        ]
        if it.notes_html:
            # Release notes travel inline as CDATA so HTML renders in the dialog.
            parts.append(
                f"      <description><![CDATA[{it.notes_html}]]></description>"
            )
        parts.append(f"      <pubDate>{escape(it.pub_date)}</pubDate>")
        if it.min_os_version:
            parts.append(
                f"      <sparkle:minimumSystemVersion>{escape(it.min_os_version)}"
                "</sparkle:minimumSystemVersion>"
            )
        parts.append("      <enclosure " + " ".join(enc_attrs) + " />")
        parts.append("    </item>")
    parts += ["  </channel>", "</rss>", ""]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _read_private_from_env() -> str:
    import os

    key = os.environ.get("PIVOT_EDDSA_PRIVATE_KEY", "").strip()
    if not key:
        sys.exit("PIVOT_EDDSA_PRIVATE_KEY is not set (the base64 Ed25519 private key)")
    return key


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sign_appcast")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="mint a new Ed25519 key pair")

    sg = sub.add_parser("sign", help="write a base64 Ed25519 .sig sidecar for a file")
    sg.add_argument("--file", required=True, type=Path, help="asset to sign")
    sg.add_argument("--out", type=Path, default=None,
                    help="signature path (default: <file>.sig)")

    ac = sub.add_parser("appcast", help="sign an installer and render the appcast")
    ac.add_argument("--installer", required=True, type=Path)
    ac.add_argument("--version", required=True)
    ac.add_argument("--url", required=True)
    ac.add_argument("--title", default="PIVOT-Tactical")
    ac.add_argument("--notes-file", type=Path, default=None)
    ac.add_argument("--min-os-version", default="")
    ac.add_argument("--out", type=Path, default=Path("appcast.xml"))

    args = p.parse_args(argv)

    if args.cmd == "keygen":
        priv, pub = generate_keypair()
        print("PIVOT_EDDSA_PRIVATE_KEY (store as a CI secret, never commit):")
        print(priv)
        print("\nPublic key (embed in the app — pivot.updates.signing.PUBLIC_KEY):")
        print(pub)
        return 0

    if args.cmd == "sign":
        private = _read_private_from_env()
        target: Path = args.file
        if not target.is_file():
            sys.exit(f"file not found: {target}")
        out = args.out or target.with_name(target.name + ".sig")
        out.write_text(sign_file(target, private))
        print(f"wrote {out} (signature of {target.name})")
        return 0

    # appcast
    private = _read_private_from_env()
    installer: Path = args.installer
    if not installer.is_file():
        sys.exit(f"installer not found: {installer}")
    notes = args.notes_file.read_text() if args.notes_file and args.notes_file.is_file() else ""
    item = AppcastItem(
        version=args.version,
        url=args.url,
        length=installer.stat().st_size,
        ed_signature=sign_file(installer, private),
        pub_date=rfc822(_dt.datetime.now(_dt.timezone.utc)),
        notes_html=notes,
        min_os_version=args.min_os_version,
    )
    args.out.write_text(build_appcast(args.title, [item]))
    print(f"wrote {args.out} (version={args.version}, length={item.length} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

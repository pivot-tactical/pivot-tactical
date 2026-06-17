"""Tests for the Ed25519 signing + WinSparkle appcast generator (spec §3.7.5)."""

import datetime
import importlib.util
import pathlib
import sys
import xml.etree.ElementTree as ET

import pytest

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"


def _load_module():
    path = pathlib.Path(__file__).resolve().parents[2] / "packaging" / "sign_appcast.py"
    spec = importlib.util.spec_from_file_location("sign_appcast", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve the module by name.
    sys.modules["sign_appcast"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sa():
    return _load_module()


def test_keypair_roundtrip_signs_and_verifies(sa):
    priv, pub = sa.generate_keypair()
    sig = sa.sign_bytes(b"installer-bytes", priv)
    assert sa.verify_bytes(b"installer-bytes", sig, pub) is True


def test_public_key_derivable_from_private(sa):
    priv, pub = sa.generate_keypair()
    assert sa.public_key_for(priv) == pub


def test_tampered_payload_fails_verification(sa):
    priv, pub = sa.generate_keypair()
    sig = sa.sign_bytes(b"original", priv)
    assert sa.verify_bytes(b"tampered", sig, pub) is False


def test_wrong_key_fails_verification(sa):
    priv1, _ = sa.generate_keypair()
    _, pub2 = sa.generate_keypair()
    sig = sa.sign_bytes(b"payload", priv1)
    assert sa.verify_bytes(b"payload", sig, pub2) is False


def test_sign_file_matches_byte_signing(sa, tmp_path):
    priv, pub = sa.generate_keypair()
    f = tmp_path / "Setup.exe"
    f.write_bytes(b"\x00\x01binary\xff")
    assert sa.verify_file(f, sa.sign_file(f, priv), pub) is True


def test_appcast_has_signed_enclosure_and_parses(sa):
    priv, pub = sa.generate_keypair()
    payload = b"the-installer"
    sig = sa.sign_bytes(payload, priv)
    item = sa.AppcastItem(
        version="1.2.0",
        url="https://example.test/PIVOT-Tactical-Setup-v1.2.0.exe",
        length=len(payload),
        ed_signature=sig,
        pub_date=sa.rfc822(datetime.datetime(2026, 6, 6, 12, 0, 0, tzinfo=datetime.UTC)),
        notes_html="<h1>Notes</h1>",
    )
    xml = sa.build_appcast("PIVOT-Tactical", [item])

    root = ET.fromstring(xml)  # must be well-formed
    enclosure = root.find(".//enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == item.url
    assert enclosure.get(f"{{{SPARKLE_NS}}}version") == "1.2.0"
    assert enclosure.get(f"{{{SPARKLE_NS}}}edSignature") == sig
    assert enclosure.get("length") == str(len(payload))
    # The signature in the feed verifies against the public key.
    assert sa.verify_bytes(payload, enclosure.get(f"{{{SPARKLE_NS}}}edSignature"), pub)


def test_appcast_escapes_special_characters(sa):
    item = sa.AppcastItem(
        version="1.0.0",
        url="https://example.test/a?b=1&c=2",
        length=1,
        ed_signature="sig",
        pub_date="Mon, 06 Jun 2026 12:00:00 +0000",
    )
    xml = sa.build_appcast("PIVOT & <Tactical>", [item])
    root = ET.fromstring(xml)  # raw & / < would break parsing if unescaped
    assert root.find(".//enclosure").get("url") == "https://example.test/a?b=1&c=2"


def test_rfc822_is_appcast_shaped(sa):
    s = sa.rfc822(datetime.datetime(2026, 6, 6, 12, 0, 0, tzinfo=datetime.UTC))
    assert s == "Sat, 06 Jun 2026 12:00:00 +0000"

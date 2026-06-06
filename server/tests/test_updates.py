"""Tests for version management & updates (spec §3.7)."""

from pathlib import Path

import pytest

from pivot.updates.manager import (
    Release,
    ReleaseStanding,
    UpdateManager,
    classify_release,
    default_asset_pattern,
    filter_channel,
    order_releases,
    verify_sha256,
)
from pivot.version import SemVer


def rel(tag, prerelease=False):
    return Release(tag=tag, prerelease=prerelease)


def test_order_releases_newest_first():
    rels = [rel("1.0.0"), rel("1.2.0"), rel("1.1.0"), rel("2.0.0")]
    ordered = [r.tag for r in order_releases(rels)]
    assert ordered == ["2.0.0", "1.2.0", "1.1.0", "1.0.0"]


def test_order_handles_prerelease_precedence():
    rels = [rel("1.0.0"), rel("1.0.0-rc.1", prerelease=True)]
    assert [r.tag for r in order_releases(rels)] == ["1.0.0", "1.0.0-rc.1"]


def test_filter_channel_stable_only():
    rels = [rel("1.0.0"), rel("1.1.0-rc.1", prerelease=True)]
    stable = filter_channel(rels, include_prereleases=False)
    assert [r.tag for r in stable] == ["1.0.0"]
    allr = filter_channel(rels, include_prereleases=True)
    assert len(allr) == 2


def test_classify_release():
    cur = SemVer.parse("1.0.0")
    assert classify_release(rel("1.1.0"), cur) is ReleaseStanding.NEWER
    assert classify_release(rel("1.0.0"), cur) is ReleaseStanding.CURRENT
    assert classify_release(rel("0.9.0"), cur) is ReleaseStanding.OLDER
    assert classify_release(rel("not-a-version"), cur) is ReleaseStanding.UNKNOWN


def test_available_updates_filters_newer():
    raw = [
        {"tag_name": "1.0.0", "assets": []},
        {"tag_name": "1.1.0", "assets": []},
        {"tag_name": "0.9.0", "assets": []},
    ]
    mgr = UpdateManager("1.0.0", versions_dir=Path("/tmp/none"))
    updates = mgr.available_updates(raw)
    assert [r.tag for r in updates] == ["1.1.0"]


def _release_data():
    return {
        "tag_name": "1.2.0",
        "name": "Release 1.2.0",
        "prerelease": False,
        "body": "notes",
        "assets": [
            {
                "name": "PIVOT-Tactical-v1.2.0-linux-x86_64.tar.gz",
                "browser_download_url": "http://x/linux",
            },
            {"name": "PIVOT-Tactical-v1.2.0-win64.zip", "browser_download_url": "http://x/win"},
        ],
    }


def test_release_from_github_picks_win64_zip():
    r = Release.from_github(_release_data(), asset_pattern="win64")
    assert r.asset_name == "PIVOT-Tactical-v1.2.0-win64.zip"
    assert r.asset_url == "http://x/win"


def test_release_from_github_picks_linux_tarball():
    r = Release.from_github(_release_data(), asset_pattern="linux-x86_64")
    assert r.asset_name == "PIVOT-Tactical-v1.2.0-linux-x86_64.tar.gz"
    assert r.asset_url == "http://x/linux"


def test_default_asset_pattern_matches_current_platform():
    import sys

    pattern = default_asset_pattern()
    if sys.platform.startswith("win"):
        assert pattern == "win64"
    elif sys.platform.startswith("linux"):
        assert pattern.startswith("linux-")
    # from_github with no pattern uses the running platform's build.
    r = Release.from_github(_release_data())
    assert pattern in r.asset_name or r.asset_name == ""


def test_verify_sha256(tmp_path):
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(b"hello world")
    import hashlib

    digest = hashlib.sha256(b"hello world").hexdigest()
    assert verify_sha256(pkg, digest) is True
    assert verify_sha256(pkg, "deadbeef") is False
    assert verify_sha256(pkg, "") is False


def test_retained_versions_and_rollback(tmp_path):
    versions = tmp_path / "versions"
    install = tmp_path / "install"
    install.mkdir()
    (install / "PIVOT-Tactical.exe").write_text("v1")

    mgr = UpdateManager("1.0.0", versions_dir=versions, retained_count=3)
    assert mgr.can_rollback() is False

    mgr.retain_current(install, "1.0.0")
    mgr.retain_current(install, "1.1.0")
    assert mgr.can_rollback() is True
    assert mgr.previous_version() == "1.1.0"  # newest retained first
    assert set(mgr.retained_versions()) == {"1.0.0", "1.1.0"}


def test_retained_versions_pruned_to_count(tmp_path):
    versions = tmp_path / "versions"
    install = tmp_path / "install"
    install.mkdir()
    (install / "f").write_text("x")
    mgr = UpdateManager("9.9.9", versions_dir=versions, retained_count=2)
    for tag in ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]:
        mgr.retain_current(install, tag)
    # Only the 2 newest are kept.
    assert mgr.retained_versions() == ["1.3.0", "1.2.0"]


def test_downgrade_plan_warns_on_schema_boundary(tmp_path):
    mgr = UpdateManager("2.0.0", versions_dir=tmp_path)
    plan = mgr.plan(rel("1.0.0"), schema_versions=(2, 1))
    assert plan.standing is ReleaseStanding.OLDER
    assert plan.crosses_schema_boundary is True
    assert plan.warnings


def test_upgrade_plan_no_warning(tmp_path):
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path)
    plan = mgr.plan(rel("1.1.0"), schema_versions=(1, 1))
    assert plan.standing is ReleaseStanding.NEWER
    assert plan.warnings == []


def test_pending_marker_roundtrip(tmp_path):
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path)
    marker = tmp_path / "pending.json"
    mgr.write_pending_marker(marker, "1.1.0", tmp_path / "staging")
    data = mgr.read_pending_marker(marker)
    assert data["target"] == "1.1.0"


def test_apply_pending_swaps_install_and_retains_old(tmp_path):
    # Current install on disk (the "running" version 1.0.0).
    install = tmp_path / "app"
    install.mkdir()
    (install / "PIVOT-Tactical").write_text("old binary v1.0.0")
    (install / "data.txt").write_text("keep-name-stable")

    # A staged 1.1.0 extracted under <staging>/app/ (matches install dir name).
    versions = tmp_path / "versions"
    staging = versions / "_staging" / "1.1.0" / "extracted"
    (staging / "app").mkdir(parents=True)
    (staging / "app" / "PIVOT-Tactical").write_text("new binary v1.1.0")
    (staging / "app" / "added.txt").write_text("brand new")

    mgr = UpdateManager("1.0.0", versions_dir=versions)
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", staging)

    applied = mgr.apply_pending(install)

    assert applied == "1.1.0"
    # Install path is unchanged but now holds the new bundle.
    assert (install / "PIVOT-Tactical").read_text() == "new binary v1.1.0"
    assert (install / "added.txt").exists()
    assert not (install / "data.txt").exists()  # old contents replaced
    # Old version retained for rollback; marker + staging cleaned up.
    assert "1.0.0" in mgr.retained_versions()
    assert mgr.read_pending_marker(mgr.pending_marker_path) is None
    assert not staging.exists()


def test_apply_pending_noop_without_marker(tmp_path):
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "v")
    assert mgr.apply_pending(tmp_path / "app") is None


def _make_signed(data: bytes):
    """Return (public_b64, signature_b64) for ``data`` from a fresh Ed25519 key."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return pub_b64, base64.b64encode(priv.sign(data)).decode()


def _patch_download(monkeypatch, archive: bytes, sig_b64: str):
    import hashlib

    from pivot.updates import manager as mgrmod

    sha = hashlib.sha256(archive).hexdigest()

    def fake_download(url, dest, token=None, timeout=600.0):
        dest.write_bytes(archive)

    def fake_get(url, token=None):
        if url.endswith(".sha256"):
            return f"{sha}  asset".encode()
        if url.endswith(".sig"):
            return sig_b64.encode()
        raise AssertionError(url)

    monkeypatch.setattr(mgrmod, "_http_download", fake_download)
    monkeypatch.setattr(mgrmod, "_http_get", fake_get)


def test_download_accepts_valid_signature(tmp_path, monkeypatch):
    archive = b"PIVOT bundle bytes"
    pub_b64, sig_b64 = _make_signed(archive)
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", pub_b64)
    _patch_download(monkeypatch, archive, sig_b64)

    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "versions")
    rel = Release(tag="1.1.0", asset_url="http://x/a.zip", asset_name="a.zip",
                  sha256_url="http://x/a.zip.sha256", sig_url="http://x/a.zip.sig")
    dest = mgr.download(rel)
    assert dest.read_bytes() == archive


def test_download_rejects_bad_signature(tmp_path, monkeypatch):
    archive = b"PIVOT bundle bytes"
    pub_b64, _ = _make_signed(archive)
    _, wrong_sig = _make_signed(b"some other payload")  # signature won't match
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", pub_b64)
    _patch_download(monkeypatch, archive, wrong_sig)

    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "versions")
    rel = Release(tag="1.1.0", asset_url="http://x/a.zip", asset_name="a.zip",
                  sha256_url="http://x/a.zip.sha256", sig_url="http://x/a.zip.sig")
    with pytest.raises(ValueError, match="not trusted"):
        mgr.download(rel)
    # The rejected download must not be left on disk to be staged.
    assert not (mgr.versions_dir / "_staging" / "1.1.0" / "a.zip").exists()


def test_download_allows_unsigned_release_when_no_sig_published(tmp_path, monkeypatch):
    # A release without a .sig sidecar (e.g. pre-rollout) still installs on the
    # SHA-256 check alone, so enabling signing doesn't brick older releases.
    archive = b"PIVOT bundle bytes"
    pub_b64, _ = _make_signed(archive)
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", pub_b64)
    _patch_download(monkeypatch, archive, "unused")

    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "versions")
    rel = Release(tag="1.1.0", asset_url="http://x/a.zip", asset_name="a.zip",
                  sha256_url="http://x/a.zip.sha256", sig_url="")  # no signature
    dest = mgr.download(rel)
    assert dest.read_bytes() == archive


def test_stage_is_idempotent_on_restage(tmp_path):
    """Applying an already-staged release again must not collide with the
    previous extraction (Windows WinError 183). Re-staging starts clean."""
    import zipfile

    versions = tmp_path / "versions"
    staging = versions / "_staging" / "1.1.0"
    staging.mkdir(parents=True)
    asset = staging / "PIVOT-Tactical-v1.1.0-win64.zip"
    with zipfile.ZipFile(asset, "w") as zf:
        zf.writestr("PIVOT-Tactical/_internal/watchfiles/x.txt", "data")
        zf.writestr("PIVOT-Tactical/PIVOT-Tactical.exe", "binary")

    mgr = UpdateManager("1.0.0", versions_dir=versions)
    release = Release(tag="1.1.0", asset_name=asset.name)

    first = mgr.stage(asset, release)
    assert (first / "PIVOT-Tactical" / "_internal" / "watchfiles" / "x.txt").exists()
    # Second stage over the populated `extracted/` must succeed, not raise.
    second = mgr.stage(asset, release)
    assert (second / "PIVOT-Tactical" / "PIVOT-Tactical.exe").exists()


def test_staged_tag_reports_pending_stage(tmp_path):
    versions = tmp_path / "versions"
    staging = versions / "_staging" / "1.1.0" / "extracted"
    staging.mkdir(parents=True)
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    assert mgr.staged_tag() is None  # no marker yet
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", staging)
    assert mgr.staged_tag() == "1.1.0"


def test_staged_tag_ignores_marker_when_staging_gone(tmp_path):
    versions = tmp_path / "versions"
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    # Marker points at a staging dir that no longer exists -> nothing staged.
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", versions / "gone")
    assert mgr.staged_tag() is None


def test_offline_import_verification(tmp_path):
    pkg = tmp_path / "PIVOT-Tactical-v1.1.0-win64.zip"
    pkg.write_bytes(b"PIVOT package bytes")
    import hashlib

    digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path)
    assert mgr.verify_import_package(pkg, digest) is True
    assert mgr.verify_import_package(pkg, "00") is False

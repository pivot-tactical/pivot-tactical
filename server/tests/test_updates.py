"""Tests for version management & updates (spec §3.7)."""

from pathlib import Path

from pivot.updates.manager import (
    Release,
    ReleaseStanding,
    UpdateManager,
    classify_release,
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


def test_release_from_github_picks_win64_asset():
    data = {
        "tag_name": "1.2.0",
        "name": "Release 1.2.0",
        "prerelease": False,
        "body": "notes",
        "assets": [
            {"name": "RadioTrainer-v1.2.0-linux.zip", "browser_download_url": "http://x/linux"},
            {"name": "RadioTrainer-v1.2.0-win64.zip", "browser_download_url": "http://x/win"},
        ],
    }
    r = Release.from_github(data)
    assert r.asset_name == "RadioTrainer-v1.2.0-win64.zip"
    assert r.asset_url == "http://x/win"


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
    (install / "RadioTrainer.exe").write_text("v1")

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


def test_offline_import_verification(tmp_path):
    pkg = tmp_path / "RadioTrainer-v1.1.0-win64.zip"
    pkg.write_bytes(b"PIVOT package bytes")
    import hashlib

    digest = hashlib.sha256(pkg.read_bytes()).hexdigest()
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path)
    assert mgr.verify_import_package(pkg, digest) is True
    assert mgr.verify_import_package(pkg, "00") is False

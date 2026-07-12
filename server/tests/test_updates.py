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
    sha256_of,
)
from pivot.version import SemVer


def rel(tag, prerelease=False):
    return Release(tag=tag, prerelease=prerelease)


def _make_app(versions_dir, tag, exe_name="PIVOT-Tactical.exe", content="binary"):
    """Drop an installed ``app-<tag>`` bundle directly onto disk (side-by-side)."""
    app_dir = versions_dir / f"app-{tag}"
    app_dir.mkdir(parents=True)
    (app_dir / exe_name).write_text(content)
    return app_dir


def test_order_releases_newest_first():
    rels = [rel("1.0.0"), rel("1.2.0"), rel("1.1.0"), rel("2.0.0")]
    ordered = [r.tag for r in order_releases(rels)]
    assert ordered == ["2.0.0", "1.2.0", "1.1.0", "1.0.0"]


def test_order_handles_prerelease_precedence():
    rels = [rel("1.0.0"), rel("1.0.0-rc.1", prerelease=True)]
    assert [r.tag for r in order_releases(rels)] == ["1.0.0", "1.0.0-rc.1"]


def test_order_unparseable_tags_last():
    rels = [rel("invalid-2"), rel("1.0.0"), rel("invalid-1"), rel("2.0.0")]
    ordered = [r.tag for r in order_releases(rels)]
    assert ordered == ["2.0.0", "1.0.0", "invalid-2", "invalid-1"]


def test_ttl_cache_serves_within_ttl_and_clears_on_demand():
    """The TTL cache reuses results within the window, but ``cache_clear`` forces
    a fresh call — that is what the "Check now" endpoint relies on to override it.
    """
    from pivot.updates.github import ttl_cache

    calls = []

    @ttl_cache(ttl_seconds=300.0)
    def fetch(repo):
        calls.append(repo)
        return [{"repo": repo, "n": len(calls)}]

    first = fetch("o/r")
    second = fetch("o/r")
    assert calls == ["o/r"]  # second call served from cache
    assert first == second

    # A different key is fetched independently.
    fetch("o/other")
    assert calls == ["o/r", "o/other"]

    # Clearing forces the next call back to the wrapped function ("Check now").
    fetch.cache_clear()
    fetch("o/r")
    assert calls == ["o/r", "o/other", "o/r"]


def test_filter_channel():
    rels = [
        rel("1.0.0"),
        rel("1.1.0-rc.1", prerelease=True),
        rel("1.2.0"),
        rel("2.0.0-beta", prerelease=True)
    ]
    # Test stable only (exclude prereleases)
    stable = filter_channel(rels, include_prereleases=False)
    assert [r.tag for r in stable] == ["1.0.0", "1.2.0"]

    # Test include prereleases (keep all)
    allr = filter_channel(rels, include_prereleases=True)
    assert [r.tag for r in allr] == ["1.0.0", "1.1.0-rc.1", "1.2.0", "2.0.0-beta"]
    # Ensure it returns a new list
    assert allr is not rels


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
    assert verify_sha256(pkg, f"  {digest.upper()}  \n") is True


def test_sha256_of(tmp_path):
    pkg = tmp_path / "pkg.zip"
    pkg.write_bytes(b"hello world")
    import hashlib

    digest = hashlib.sha256(b"hello world").hexdigest()
    assert sha256_of(pkg) == digest


def test_retained_versions_and_rollback(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _make_app(versions, "1.0.0")
    _make_app(versions, "1.1.0")

    mgr = UpdateManager("1.1.0", versions_dir=versions, retained_count=3)
    mgr.layout.activate("1.1.0")

    assert mgr.can_rollback() is True
    assert mgr.previous_version() == "1.0.0"
    assert set(mgr.retained_versions()) == {"1.0.0"}


def test_retained_versions_pruned_to_count(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    for tag in ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]:
        _make_app(versions, tag)
    mgr = UpdateManager("1.3.0", versions_dir=versions, retained_count=2)
    mgr.layout.activate("1.3.0")

    mgr._prune_retained()

    # The active version plus the 2 newest are kept; the rest are pruned.
    assert mgr.layout.installed_versions() == ["1.3.0", "1.2.0"]
    assert mgr.retained_versions() == ["1.2.0"]


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


def test_apply_pending_activates_staged_version(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _make_app(versions, "1.0.0", content="old binary v1.0.0")
    new_dir = _make_app(versions, "1.1.0", content="new binary v1.1.0")

    mgr = UpdateManager("1.0.0", versions_dir=versions)
    mgr.layout.activate("1.0.0")
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", new_dir)

    applied = mgr.apply_pending()

    assert applied == "1.1.0"
    # The `current` link now points at the new build — no file was touched.
    assert mgr.layout.active_version() == "1.1.0"
    assert (mgr.layout.current_link / "PIVOT-Tactical.exe").read_text() == "new binary v1.1.0"
    # Old version retained for rollback; marker cleaned up.
    assert "1.0.0" in mgr.retained_versions()
    assert mgr.read_pending_marker(mgr.pending_marker_path) is None


def test_apply_pending_noop_without_marker(tmp_path):
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "v")
    assert mgr.apply_pending() is None


def test_stage_rollback_then_apply_restores_retained_version(tmp_path):
    """A retained version can be staged and applied without any download — the
    instant, offline downgrade for a bad update (§3.7.7)."""
    versions = tmp_path / "versions"
    versions.mkdir()
    _make_app(versions, "1.1.0", content="good binary v1.1.0")
    _make_app(versions, "1.2.0", content="new (broken) binary v1.2.0")

    mgr = UpdateManager("1.2.0", versions_dir=versions)
    mgr.layout.activate("1.2.0")
    assert mgr.previous_version() == "1.1.0"

    mgr.stage_rollback("1.1.0")
    assert mgr.staged_tag() == "1.1.0"

    applied = mgr.apply_pending()
    assert applied == "1.1.0"
    assert mgr.layout.active_version() == "1.1.0"
    assert (mgr.layout.current_link / "PIVOT-Tactical.exe").read_text() == "good binary v1.1.0"
    # The broken 1.2.0 is retained so you can still roll forward.
    assert "1.2.0" in mgr.retained_versions()


def test_stage_rollback_rejects_unknown_tag(tmp_path):
    mgr = UpdateManager("1.2.0", versions_dir=tmp_path / "versions")
    with pytest.raises(ValueError, match="No retained version"):
        mgr.stage_rollback("9.9.9")


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

    def fake_download(url, dest, token=None, timeout=600.0, progress_cb=None):
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
    assert (first / "_internal" / "watchfiles" / "x.txt").exists()
    # Second stage over the populated `extracted/` must succeed, not raise.
    second = mgr.stage(asset, release)
    assert (second / "PIVOT-Tactical.exe").exists()


def test_download_and_stage_skips_redownload_when_already_staged(tmp_path, monkeypatch):
    """The two auto-update triggers (background poll + console /updates/check)
    both stage the newest release. The second to run must short-circuit on the
    pending marker rather than re-download into — and collide on — the shared
    _staging archive (the Windows WinError 32 the user hit)."""
    import hashlib
    import io
    import zipfile

    from pivot.updates import manager as mgrmod

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("PIVOT-Tactical/PIVOT-Tactical.exe", "binary")
    archive = buf.getvalue()
    pub_b64, sig_b64 = _make_signed(archive)
    monkeypatch.setenv("PIVOT_EDDSA_PUBLIC_KEY", pub_b64)

    sha = hashlib.sha256(archive).hexdigest()
    downloads = {"n": 0}

    def fake_download(url, dest, token=None, timeout=600.0, progress_cb=None):
        downloads["n"] += 1
        dest.write_bytes(archive)

    def fake_get(url, token=None):
        if url.endswith(".sha256"):
            return f"{sha}  asset".encode()
        if url.endswith(".sig"):
            return sig_b64.encode()
        raise AssertionError(url)

    monkeypatch.setattr(mgrmod, "_http_download", fake_download)
    monkeypatch.setattr(mgrmod, "_http_get", fake_get)

    mgr = UpdateManager("1.0.0", versions_dir=tmp_path / "versions")
    rel = Release(tag="1.1.0", asset_url="http://x/a.zip", asset_name="a.zip",
                  sha256_url="http://x/a.zip.sha256", sig_url="http://x/a.zip.sig")
    first = mgr.download_and_stage(rel)
    second = mgr.download_and_stage(rel)

    assert first == second
    assert downloads["n"] == 1  # second trigger reused the staged copy
    assert mgr.staged_tag() == "1.1.0"


def test_with_sharing_retry_rides_out_transient_lock(monkeypatch):
    """A freshly written archive briefly held by AV (WinError 32) is retried,
    not surfaced as an auto-update failure."""
    from pivot.updates import manager as mgrmod

    monkeypatch.setattr(mgrmod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            exc = OSError("being used by another process")
            exc.winerror = 32
            raise exc
        return "ok"

    assert mgrmod._with_sharing_retry(flaky) == "ok"
    assert calls["n"] == 3


def test_with_sharing_retry_reraises_non_sharing_errors(monkeypatch):
    """Network/other errors carry no winerror and must fail fast, not spin."""
    from pivot.updates import manager as mgrmod

    monkeypatch.setattr(mgrmod.time, "sleep", lambda *_: None)

    def boom():
        raise FileNotFoundError("nope")

    with pytest.raises(FileNotFoundError):
        mgrmod._with_sharing_retry(boom)


def test_with_http_retry_rides_out_transient_gateway_error(monkeypatch):
    """A one-off GitHub 502/503/504 (the user-reported 'Bad Gateway') is retried
    with backoff, not surfaced as 'Auto-update failed'."""
    import urllib.error

    from pivot.updates import manager as mgrmod

    monkeypatch.setattr(mgrmod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("http://x", 502, "Bad Gateway", {}, None)
        return b"payload"

    assert mgrmod._with_http_retry(flaky) == b"payload"
    assert calls["n"] == 3


def test_with_http_retry_retries_connection_failures(monkeypatch):
    """A dropped connection / timeout (URLError with no HTTP status) is retried."""
    import urllib.error

    from pivot.updates import manager as mgrmod

    monkeypatch.setattr(mgrmod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.URLError("connection reset")
        return b"ok"

    assert mgrmod._with_http_retry(flaky) == b"ok"
    assert calls["n"] == 2


def test_with_http_retry_reraises_permanent_http_errors(monkeypatch):
    """A 404 (release/asset genuinely gone) must fail fast, not spin on retries."""
    import urllib.error

    from pivot.updates import manager as mgrmod

    monkeypatch.setattr(mgrmod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def gone():
        calls["n"] += 1
        raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        mgrmod._with_http_retry(gone)
    assert calls["n"] == 1  # no retries for a permanent error


def test_retained_details_reports_size_and_delete_frees_space(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    _make_app(versions, "1.0.0", exe_name="app.bin", content="x" * 2048)
    _make_app(versions, "0.9.0", exe_name="app.bin", content="y" * 512)
    mgr = UpdateManager("1.0.0", versions_dir=versions)

    details = mgr.retained_details()
    assert [d["tag"] for d in details] == ["1.0.0", "0.9.0"]  # newest first
    by_tag = {d["tag"]: d["bytes"] for d in details}
    assert by_tag["1.0.0"] == 2048
    assert by_tag["0.9.0"] == 512

    assert mgr.delete_retained("0.9.0") is True
    assert not mgr.layout.app_dir("0.9.0").exists()
    assert mgr.retained_versions() == ["1.0.0"]
    # Unknown / non-version tags are refused, not deleted.
    assert mgr.delete_retained("9.9.9") is False
    assert mgr.delete_retained("_staging") is False


def test_retained_versions_excludes_staging_dir(tmp_path):
    versions = tmp_path / "versions"
    (versions / "_staging" / "1.1.0").mkdir(parents=True)
    _make_app(versions, "1.0.0")
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    assert mgr.retained_versions() == ["1.0.0"]  # _staging is not an app-<tag> dir


def test_staged_tag_reports_pending_stage(tmp_path):
    versions = tmp_path / "versions"
    versions.mkdir()
    app_dir = _make_app(versions, "1.1.0")
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    assert mgr.staged_tag() is None  # no marker yet
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", app_dir)
    assert mgr.staged_tag() == "1.1.0"


def test_staged_tag_ignores_marker_when_app_dir_missing(tmp_path):
    versions = tmp_path / "versions"
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    # Marker points at a tag whose app-<tag> folder isn't installed -> nothing staged.
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

def test_stage_prevents_path_traversal_tar(tmp_path):
    import tarfile
    from pivot.updates.manager import UpdateManager, Release

    from pivot.updates.layout import Layout
    layout = Layout(tmp_path / "app")
    manager = UpdateManager(layout, versions_dir=tmp_path / "versions")

    # Create fake release
    release = Release(
        tag="1.0.0",
        name="1.0.0",
        asset_name="pivot-linux-amd64.tar.gz",
        asset_url="http://example.com/asset",
    )

    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "pivot-linux-amd64.tar.gz"

    with tarfile.open(archive, "w:gz") as tf:
        with open(tmp_path / "evil.txt", "w") as f:
            f.write("evil")
        tf.add(tmp_path / "evil.txt", arcname="../evil.txt")

    import pytest
    with pytest.raises(ValueError, match="Path traversal detected"):
        manager.stage(archive, release)

def test_stage_prevents_path_traversal_zip(tmp_path):
    import zipfile
    from pivot.updates.manager import UpdateManager, Release

    from pivot.updates.layout import Layout
    layout = Layout(tmp_path / "app")
    manager = UpdateManager(layout, versions_dir=tmp_path / "versions")

    # Create fake release
    release = Release(
        tag="1.0.0",
        name="1.0.0",
        asset_name="pivot-win64.zip",
        asset_url="http://example.com/asset",
    )

    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "pivot-win64.zip"

    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "evil")

    import pytest
    with pytest.raises(ValueError, match="Path traversal detected"):
        manager.stage(archive, release)

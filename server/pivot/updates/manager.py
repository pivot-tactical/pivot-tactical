"""Update policy + orchestration (spec §3.7).

Pure functions cover the parts that must be exactly right and are easy to test:

* :func:`order_releases` — semantic-version ordering of GitHub release tags
  (§3.7.3).
* :func:`filter_channel` — Stable-only vs Include-prereleases (§3.7.3, §3.7.8).
* :func:`classify_release` — newer / current / older relative to the running
  build (§3.7.4).
* :func:`verify_sha256` — mandatory checksum verification before any swap
  (§3.7.5, §8.4).

:class:`UpdateManager` ties these together and owns the retained-versions store
for instant rollback (§3.7.7). Network fetches and the Windows staged swap are
isolated behind small methods so the policy above stays import-light and the
training runtime never pulls in HTTP code.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import threading
import time
import typing
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pivot.updates.layout import Layout
from pivot.version import SemVer

# Serialize the download+stage critical section across the whole process. Two
# triggers can fire it at once — the background update-service poll loop and the
# instructor console's /updates/check call (both auto-stage when auto_update is
# on) — and on Windows two writers to the same _staging archive collide with
# WinError 32 ("being used by another process"). One staging op at a time; the
# loser re-checks staged_tag() and skips the redundant download+extract.
_STAGE_LOCK = threading.RLock()


def _is_sharing_violation(exc: OSError) -> bool:
    # ERROR_SHARING_VIOLATION (32) / ERROR_LOCK_VIOLATION (33): a transient
    # Windows lock, typically real-time AV scanning the freshly written file.
    return getattr(exc, "winerror", None) in (32, 33)


# HTTP status codes that are transient gateway/rate-limit hiccups, not a real
# "this release is unavailable". GitHub's release-asset CDN occasionally returns
# 502/503/504 under load and 429 when rate-limited; retrying with backoff rides
# these out instead of surfacing "Auto-update failed: HTTP Error 502" to the
# instructor for what is almost always a momentary blip.
_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_transient_http(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_STATUS
    # A URLError with no HTTP status is a connection-level failure (reset DNS
    # hiccup, timeout) — also worth a retry. socket timeouts surface as these.
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def _with_http_retry(fn, attempts: int = 4, delay: float = 1.5):
    """Run a network ``fn``, retrying transient gateway/timeout failures.

    Backs off (1.5s, 3s, 4.5s) across attempts before giving up, so a one-off
    GitHub 502/503/504/429 or a dropped connection mid-download is retried
    rather than failing the whole update. Permanent errors (404, auth, checksum
    mismatch) carry no transient status and are re-raised immediately.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raised below unless transient
            if not _is_transient_http(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _with_sharing_retry(fn, *, attempts: int = 8, delay: float = 0.25):
    """Run ``fn``, retrying briefly while Windows reports a sharing violation.

    A freshly downloaded archive in Program Files is often held open for a
    moment by Windows Defender; the next open then fails with WinError 32.
    Backing off and retrying rides out that window instead of failing the whole
    update. Network errors (URLError) carry no ``winerror`` and so are re-raised
    immediately rather than retried here.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except OSError as exc:
            if not _is_sharing_violation(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _http_get(url: str, token: str | None = None, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PIVOT-Updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    def _fetch() -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    return _with_http_retry(_fetch)


def _http_download(url: str, dest: Path, token: str | None = None, timeout: float = 600.0) -> None:
    """Stream a potentially large file to disk, retrying transient failures."""
    req = urllib.request.Request(url, headers={"User-Agent": "PIVOT-Updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    def _fetch() -> None:
        # Re-open from scratch each attempt so a connection dropped mid-stream
        # doesn't leave a truncated file behind to be staged.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            with dest.open("wb") as fh:
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)

    _with_http_retry(_fetch)


def default_asset_pattern() -> str:
    """The release-asset name fragment for the current platform (§3.7.5).

    Matches the artifact names produced by the release workflow, so the in-app
    updater downloads the right build for the OS it is running on:
    ``win64`` (.zip), ``linux-x86_64`` / ``linux-arm64`` (.tar.gz).
    """
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        return "linux-x86_64"
    if sys.platform == "darwin":
        return "macos"
    return "win64"


# Archive extensions the updater accepts for a release asset.
_ASSET_EXTENSIONS = (".zip", ".tar.gz")


class ReleaseStanding(StrEnum):
    NEWER = "newer"
    CURRENT = "current"
    OLDER = "older"
    UNKNOWN = "unknown"


@dataclass
class Release:
    """A GitHub release, normalised from the REST API (§3.7.3)."""

    tag: str
    name: str = ""
    published_at: str = ""
    prerelease: bool = False
    body: str = ""
    asset_url: str = ""
    asset_name: str = ""
    sha256_url: str = ""  # URL of the .sha256 sidecar published alongside the asset
    sig_url: str = ""  # URL of the .sig Ed25519 signature sidecar (authenticity)

    @property
    def semver(self) -> SemVer | None:
        return SemVer.try_parse(self.tag)

    @classmethod
    def from_github(cls, data: dict, asset_pattern: str | None = None) -> Release:
        """Build from a GitHub releases API element (§3.7.3).

        ``asset_pattern`` selects the platform build; when omitted it defaults to
        the current platform's pattern so the running app picks its own OS asset.
        """
        pattern = asset_pattern or default_asset_pattern()
        asset_url = asset_name = sha256_url = ""
        assets = data.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if pattern in name and name.endswith(_ASSET_EXTENSIONS):
                asset_url = asset.get("browser_download_url", "")
                asset_name = name
                break
        sig_url = ""
        if asset_name:
            by_name = {a.get("name"): a.get("browser_download_url", "") for a in assets}
            sha256_url = by_name.get(asset_name + ".sha256", "")
            # Ed25519 signature of the archive (authenticity), verified against
            # the embedded public key before any staged swap.
            sig_url = by_name.get(asset_name + ".sig", "")
        return cls(
            tag=data.get("tag_name", ""),
            name=data.get("name", "") or data.get("tag_name", ""),
            published_at=data.get("published_at", ""),
            prerelease=bool(data.get("prerelease", False)),
            body=data.get("body", "") or "",
            asset_url=asset_url,
            asset_name=asset_name,
            sha256_url=sha256_url,
            sig_url=sig_url,
        )


@dataclass
class UpdatePlan:
    """A resolved intent to move to ``target`` from ``current`` (§3.7.5)."""

    target: Release
    current_version: str
    standing: ReleaseStanding
    crosses_schema_boundary: bool = False
    warnings: list[str] = field(default_factory=list)


def order_releases(releases: list[Release]) -> list[Release]:
    """Newest first by semantic version; unparseable tags sort last (§3.7.3)."""

    def key(r: Release):
        sv = r.semver
        return (0, sv) if sv is not None else (1, SemVer(0, 0, 0))

    parseable = [r for r in releases if r.semver is not None]
    unparseable = [r for r in releases if r.semver is None]
    parseable.sort(key=lambda r: r.semver, reverse=True)
    return parseable + unparseable


def filter_channel(releases: list[Release], include_prereleases: bool) -> list[Release]:
    """Stable-only (default) drops prereleases; otherwise keep all (§3.7.8)."""
    if include_prereleases:
        return list(releases)
    return [r for r in releases if not r.prerelease]


def classify_release(release: Release, current: SemVer) -> ReleaseStanding:
    """newer / current / older relative to the running build (§3.7.4)."""
    sv = release.semver
    if sv is None:
        return ReleaseStanding.UNKNOWN
    if sv > current:
        return ReleaseStanding.NEWER
    if sv < current:
        return ReleaseStanding.OLDER
    return ReleaseStanding.CURRENT


def verify_sha256(path: Path, expected_hex: str) -> bool:
    """Verify a downloaded package against its published SHA-256 (§3.7.5)."""
    if not expected_hex:
        return False
    return sha256_of(path).lower() == expected_hex.strip().lower()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_signature(dest: Path, release: Release, token: str | None) -> None:
    """Verify the archive's Ed25519 signature against the embedded public key.

    Authenticity (not just the SHA-256 integrity check): the release workflow
    signs every asset with the project's private key, and the running app trusts
    only that key. A published signature that fails verification is fatal —
    the download is deleted and an error raised. A *missing* signature is
    tolerated only while no key is configured (or the release predates signing),
    so the rollout never bricks the updater; once assets are signed the check is
    enforced for everyone.
    """
    from pivot.updates import signing

    if not signing.is_configured():
        return  # no trust anchor -> integrity-only (SHA-256) as before
    if not release.sig_url:
        return  # unsigned release (e.g. pre-rollout) -> accept on SHA-256 alone
    try:
        sig_b64 = _http_get(release.sig_url, token).decode().strip()
    except Exception as exc:  # signature published but unreachable -> refuse
        dest.unlink(missing_ok=True)
        raise ValueError(f"Could not fetch update signature ({release.asset_name})") from exc
    if not signing.verify_file(dest, sig_b64, signing.public_key()):
        dest.unlink(missing_ok=True)
        raise ValueError(
            f"Signature verification failed — update is not trusted ({release.asset_name})"
        )


@dataclass
class UpdateConfig:
    current_version: str
    versions_dir: Path | str
    retained_count: int = 3
    include_prereleases: bool = False
    releases_provider: typing.Any = None
    asset_pattern: str | None = None


class UpdateManager:
    """Coordinates checks, retained versions and rollback (§3.7).

    Pure-policy by default; ``releases_provider`` (a callable returning raw
    GitHub JSON) is injected so the network layer is swappable and testable, and
    so air-gapped builds can omit it entirely (offline import only).
    """

    def __init__(self, config: UpdateConfig) -> None:
        self.current_version = config.current_version
        self.versions_dir = Path(config.versions_dir)
        self.layout = Layout(self.versions_dir)
        self.retained_count = config.retained_count
        self.include_prereleases = config.include_prereleases
        self._releases_provider = config.releases_provider
        self.asset_pattern = config.asset_pattern or default_asset_pattern()

    # -- discovery --------------------------------------------------------- #

    def list_releases(self, raw: list[dict] | None = None) -> list[Release]:
        """Return channel-filtered, newest-first releases (§3.7.3)."""
        data = (
            raw
            if raw is not None
            else (self._releases_provider() if self._releases_provider else [])
        )
        releases = [Release.from_github(d, self.asset_pattern) for d in data]
        releases = filter_channel(releases, self.include_prereleases)
        return order_releases(releases)

    def available_updates(self, raw: list[dict] | None = None) -> list[Release]:
        cur = SemVer.parse(self.current_version)
        return [
            r for r in self.list_releases(raw) if classify_release(r, cur) is ReleaseStanding.NEWER
        ]

    def plan(self, target: Release, schema_versions: tuple[int, int] | None = None) -> UpdatePlan:
        """Build an :class:`UpdatePlan`, warning on a schema-boundary downgrade."""
        cur = SemVer.parse(self.current_version)
        standing = classify_release(target, cur)
        warnings: list[str] = []
        crosses = False
        if standing is ReleaseStanding.OLDER and schema_versions is not None:
            from pivot.db.migrations import crosses_migration_boundary

            crosses = crosses_migration_boundary(schema_versions[0], schema_versions[1])
            if crosses:
                warnings.append(
                    "This downgrade crosses a database schema migration. A one-off "
                    "data backup is recommended before proceeding (spec §3.7.9)."
                )
        return UpdatePlan(
            target=target,
            current_version=self.current_version,
            standing=standing,
            crosses_schema_boundary=crosses,
            warnings=warnings,
        )

    # -- retained versions / rollback (§3.7.7) ----------------------------- #
    #
    # Side-by-side model: every installed version is a full ``app-<ver>`` folder
    # under versions/. "Retained" versions are simply the installed ones other
    # than the active build — each is an instant, offline rollback target (just
    # re-point ``current``). No copies are made; retaining a version is free.

    def retained_versions(self) -> list[str]:
        """Installed versions available as rollback targets (not the active one)."""
        active = self.layout.active_version()
        return [t for t in self.layout.installed_versions() if t != active]

    def _prune_retained(self) -> None:
        # Keep the newest N installed versions (the active one is always kept).
        self.layout.prune(self.retained_count)

    def can_rollback(self) -> bool:
        return len(self.retained_versions()) > 0

    def retained_details(self) -> list[dict]:
        """Retained versions with their on-disk size, newest first (§3.7.7).

        Surfaced in the Updates pane so the instructor can see what is actually
        kept on disk (vs. an older release that would re-download) and how much
        space each takes before deleting it. Size is walked on demand, not on the
        status poll, so the hot path stays cheap.
        """
        out: list[dict] = []
        for tag in self.retained_versions():
            path = self.layout.app_dir(tag)
            total = 0
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:  # a file vanished mid-walk — skip it
                        pass
            out.append({"tag": tag, "bytes": total})
        return out

    def delete_retained(self, tag: str) -> bool:
        """Delete a retained version to free disk space (§3.7.7).

        Removes the version's ``app-<tag>`` folder. Refuses the active version and
        unknown tags. Returns False when there's nothing to delete.
        """
        return self.layout.delete_version(tag)

    def previous_version(self) -> str | None:
        """The most recent retained version (the instant-rollback target)."""
        retained = self.retained_versions()
        return retained[0] if retained else None

    def stage_rollback(self, tag: str) -> str:
        """Mark a retained version to activate on the next restart (§3.7.7).

        Side-by-side makes a downgrade trivial: no copy, no extract — the target
        ``app-<tag>`` is already on disk, so we just record it as the pending
        target. ``apply_pending`` then re-points ``current`` at it. Raises if the
        version isn't installed.
        """
        if not self.layout.app_dir(tag).is_dir():
            raise ValueError(f"No retained version to roll back to: {tag}")
        self.write_pending_marker(self.pending_marker_path, tag, self.layout.app_dir(tag))
        return tag

    # -- download + stage (§3.7.5) ------------------------------------------ #

    @property
    def pending_marker_path(self) -> Path:
        return self.versions_dir / "pending_update.json"

    def download(self, release: Release, token: str | None = None) -> Path:
        """Download the release asset to a staging folder and verify SHA-256.

        Returns the path to the downloaded archive. Raises on network error or
        checksum mismatch.
        """
        if not release.asset_url:
            raise ValueError(f"No asset available for this platform ({self.asset_pattern})")
        staging = self.versions_dir / "_staging" / release.tag
        staging.mkdir(parents=True, exist_ok=True)
        dest = staging / release.asset_name
        _http_download(release.asset_url, dest, token)
        if release.sha256_url:
            sha_text = _http_get(release.sha256_url, token).decode().strip()
            expected = sha_text.split()[0]
            if not verify_sha256(dest, expected):
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"SHA-256 mismatch — download may be corrupt ({release.asset_name})"
                )
        _verify_signature(dest, release, token)
        return dest

    def stage(self, asset_path: Path, release: Release) -> Path:
        """Extract the archive into a versioned ``app-<tag>`` folder and mark it.

        Side-by-side: the new build is installed *beside* the running one, never
        over it — so nothing is locked and there is no swap to fail. The relaunch
        then just re-points ``current`` at this folder (§3.7.5). Returns the
        ``app-<tag>`` path.
        """
        import tarfile
        import zipfile

        # Start from a clean extraction dir: a re-stage (e.g. the user applies an
        # already-staged release again) must not collide with leftovers from the
        # previous attempt. On Windows, extracting over an existing tree fails
        # with WinError 183 ("file already exists"), so wipe first.
        extracted = asset_path.parent / "extracted"
        if extracted.exists():
            shutil.rmtree(extracted, ignore_errors=True)
        extracted.mkdir(parents=True, exist_ok=True)
        if asset_path.name.endswith(".tar.gz"):
            with tarfile.open(asset_path) as tf:
                tf.extractall(extracted)
        elif asset_path.name.endswith(".zip"):
            with zipfile.ZipFile(asset_path) as zf:
                zf.extractall(extracted)

        bundle = self._bundle_root(extracted)
        if bundle is None:
            raise ValueError(f"Staged archive has no bundle root ({release.asset_name})")
        app_dir = self.layout.place_version(release.tag, bundle)
        shutil.rmtree(extracted, ignore_errors=True)  # bundle moved out; drop scratch
        self.write_pending_marker(self.pending_marker_path, release.tag, app_dir)
        return app_dir

    def download_and_stage(self, release: Release, token: str | None = None) -> Path:
        """Download + verify + extract ``release``, returning the staging dir.

        The single entry point every auto/manual apply path uses, so they can't
        collide on the shared ``_staging`` archive. Serialized process-wide and
        idempotent: if another trigger already staged this release while we
        waited for the lock, the existing staging dir is returned without
        re-downloading. Transient Windows sharing violations (AV scanning the
        fresh archive) are retried rather than failing the update (§3.7.5).
        """
        with _STAGE_LOCK:
            if self.staged_tag() == release.tag:
                marker = self.read_pending_marker(self.pending_marker_path) or {}
                staged = marker.get("staging")
                if staged and Path(staged).exists():
                    return Path(staged)
            asset_path = _with_sharing_retry(lambda: self.download(release, token))
            return _with_sharing_retry(lambda: self.stage(asset_path, release))

    # -- offline import (§3.7.6) ------------------------------------------- #

    def verify_import_package(self, package: Path, expected_sha256: str) -> bool:
        """Verify a manually-transferred package before applying it (§3.7.6)."""
        return verify_sha256(package, expected_sha256)

    # -- pending-update marker (§3.7.5) ------------------------------------ #

    def write_pending_marker(self, marker_path: Path, target_tag: str, staging: Path) -> None:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps({"target": target_tag, "staging": str(staging)}))

    def read_pending_marker(self, marker_path: Path) -> dict | None:
        if not marker_path.exists():
            return None
        try:
            return json.loads(marker_path.read_text())
        except (ValueError, OSError):
            return None

    def staged_tag(self) -> str | None:
        """The tag of an update that is staged and awaiting activation.

        Returns the pending marker's target only when its ``app-<tag>`` folder is
        actually on disk and isn't already the active version — so a stale marker
        (or one left after a successful apply) reads as "nothing staged". Lets the
        apply paths skip a redundant download+extract (§3.7.5).
        """
        marker = self.read_pending_marker(self.pending_marker_path)
        if not marker:
            return None
        target = marker.get("target")
        if not target or not self.layout.app_dir(target).is_dir():
            return None
        if target == self.layout.active_version():
            return None
        return target

    def staged_app_exe(self, exe_name: str) -> Path | None:
        """Path to the exe inside the staged ``app-<tag>``, if one is staged.

        Lets the relauncher run the helper *from the staged build* rather than the
        active one — so the version flip is performed by a process that lives in
        neither the old nor (well, actually the new) install's busy files. Returns
        None when nothing is staged.
        """
        target = self.staged_tag()
        if target is None:
            return None
        return self.layout.app_exe(target, exe_name)

    def apply_pending(self) -> str | None:
        """Activate any staged/rolled-back version (§3.7.5).

        Side-by-side: there is no file swap. The target ``app-<tag>`` is already
        installed; applying it just re-points ``current`` at it (an atomic-ish
        link flip that touches no running file) and prunes old versions. Returns
        the activated tag, or None when there is nothing pending.
        """
        marker = self.read_pending_marker(self.pending_marker_path)
        if not marker:
            return None
        target = str(marker.get("target", "")) or ""
        if not target or not self.layout.app_dir(target).is_dir():
            return None

        self.layout.activate(target)
        self.pending_marker_path.unlink(missing_ok=True)
        self._prune_retained()
        return target

    @staticmethod
    def _bundle_root(extracted: Path) -> Path | None:
        """Find the bundle root inside an extraction dir.

        The archive normally extracts to ``<extracted>/PIVOT-Tactical/…``; fall
        back to a lone top-level directory, or the extraction dir itself.
        """
        if not extracted.is_dir():
            return None
        named = extracted / "PIVOT-Tactical"
        if named.is_dir():
            return named
        subdirs = [p for p in extracted.iterdir() if p.is_dir()]
        if len(subdirs) == 1:
            return subdirs[0]
        return extracted if any(extracted.iterdir()) else None

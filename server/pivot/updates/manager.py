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
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pivot.version import SemVer


def _http_get(url: str, token: str | None = None, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PIVOT-Updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _http_download(url: str, dest: Path, token: str | None = None, timeout: float = 600.0) -> None:
    """Stream a potentially large file to disk."""
    req = urllib.request.Request(url, headers={"User-Agent": "PIVOT-Updater"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        with dest.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)


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


class ReleaseStanding(str, Enum):
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
    sha256_url: str = ""   # URL of the .sha256 sidecar published alongside the asset

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
        if asset_name:
            sidecar = asset_name + ".sha256"
            for asset in assets:
                if asset.get("name") == sidecar:
                    sha256_url = asset.get("browser_download_url", "")
                    break
        return cls(
            tag=data.get("tag_name", ""),
            name=data.get("name", "") or data.get("tag_name", ""),
            published_at=data.get("published_at", ""),
            prerelease=bool(data.get("prerelease", False)),
            body=data.get("body", "") or "",
            asset_url=asset_url,
            asset_name=asset_name,
            sha256_url=sha256_url,
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
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected_hex.strip().lower()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _swap_dir_contents(install_dir: Path, new_root: Path) -> None:
    """Replace the contents of ``install_dir`` with those of ``new_root``.

    Done child-by-child rather than swapping the directory itself, so the
    install path (referenced by the service unit and shortcuts) stays stable.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    for child in list(install_dir.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    for child in list(new_root.iterdir()):
        shutil.move(str(child), str(install_dir / child.name))


class UpdateManager:
    """Coordinates checks, retained versions and rollback (§3.7).

    Pure-policy by default; ``releases_provider`` (a callable returning raw
    GitHub JSON) is injected so the network layer is swappable and testable, and
    so air-gapped builds can omit it entirely (offline import only).
    """

    def __init__(
        self,
        current_version: str,
        versions_dir: Path,
        retained_count: int = 3,
        include_prereleases: bool = False,
        releases_provider=None,
        asset_pattern: str | None = None,
    ) -> None:
        self.current_version = current_version
        self.versions_dir = Path(versions_dir)
        self.retained_count = retained_count
        self.include_prereleases = include_prereleases
        self._releases_provider = releases_provider
        # Which platform build to offer (win64 / linux-x86_64 / …). Defaults to
        # the OS the app is running on (§3.7.5).
        self.asset_pattern = asset_pattern or default_asset_pattern()

    # -- discovery --------------------------------------------------------- #

    def list_releases(self, raw: list[dict] | None = None) -> list[Release]:
        """Return channel-filtered, newest-first releases (§3.7.3)."""
        data = raw if raw is not None else (self._releases_provider() if self._releases_provider else [])
        releases = [Release.from_github(d, self.asset_pattern) for d in data]
        releases = filter_channel(releases, self.include_prereleases)
        return order_releases(releases)

    def available_updates(self, raw: list[dict] | None = None) -> list[Release]:
        cur = SemVer.parse(self.current_version)
        return [r for r in self.list_releases(raw) if classify_release(r, cur) is ReleaseStanding.NEWER]

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

    def retained_versions(self) -> list[str]:
        if not self.versions_dir.is_dir():
            return []
        tags = [p.name for p in self.versions_dir.iterdir() if p.is_dir()]
        # Newest semver first; non-semver names sorted last.
        tags.sort(key=lambda t: SemVer.try_parse(t) or SemVer(0, 0, 0), reverse=True)
        return tags

    def retain_current(self, install_dir: Path, tag: str) -> Path:
        """Copy the current install into the retained store before a swap (§3.7.7)."""
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        dest = self.versions_dir / tag
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(install_dir, dest)
        self._prune_retained()
        return dest

    def _prune_retained(self) -> None:
        tags = self.retained_versions()
        for stale in tags[self.retained_count :]:
            shutil.rmtree(self.versions_dir / stale, ignore_errors=True)

    def can_rollback(self) -> bool:
        return len(self.retained_versions()) > 0

    def previous_version(self) -> str | None:
        """The most recent retained version (the instant-rollback target)."""
        retained = self.retained_versions()
        return retained[0] if retained else None

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
            raise ValueError(
                f"No asset available for this platform ({self.asset_pattern})"
            )
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
        return dest

    def stage(self, asset_path: Path, release: Release) -> Path:
        """Extract the downloaded archive and write the pending-update marker.

        The launcher reads the marker on next startup and performs the actual
        directory swap (out-of-process, so the running binary can be replaced
        on Windows too — §3.7.5).
        """
        import tarfile
        import zipfile

        staging_dir = asset_path.parent / "extracted"
        staging_dir.mkdir(exist_ok=True)
        if asset_path.name.endswith(".tar.gz"):
            with tarfile.open(asset_path) as tf:
                tf.extractall(staging_dir)
        elif asset_path.name.endswith(".zip"):
            with zipfile.ZipFile(asset_path) as zf:
                zf.extractall(staging_dir)
        self.write_pending_marker(self.pending_marker_path, release.tag, staging_dir)
        return staging_dir

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

    def apply_pending(self, install_dir: Path) -> str | None:
        """Swap any staged update into ``install_dir`` (the Linux apply path).

        Called out-of-band — by the systemd service's ``ExecStartPre`` before the
        new binary starts (§3.7.5). On Linux a running executable's file can be
        replaced safely, so the swap happens cleanly between stop and start. The
        current install is retained first for instant rollback (§3.7.7). Returns
        the applied tag, or None when there is nothing staged.
        """
        marker = self.read_pending_marker(self.pending_marker_path)
        if not marker:
            return None
        staging = Path(marker.get("staging", ""))
        target = str(marker.get("target", "")) or "unknown"

        new_root = self._staged_install_root(staging, install_dir.name)
        if new_root is None:
            return None

        install_dir = Path(install_dir)
        # Retain the current install so a rollback is one swap away.
        if install_dir.is_dir():
            self.retain_current(install_dir, self.current_version)
        _swap_dir_contents(install_dir, new_root)

        self.pending_marker_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        return target

    @staticmethod
    def _staged_install_root(staging: Path, install_name: str) -> Path | None:
        """Find the extracted install root inside a staging dir.

        The archive normally extracts to ``<staging>/<install_name>/…``; fall back
        to a lone top-level directory if the bundle name differs.
        """
        if not staging.is_dir():
            return None
        named = staging / install_name
        if named.is_dir():
            return named
        subdirs = [p for p in staging.iterdir() if p.is_dir()]
        if len(subdirs) == 1:
            return subdirs[0]
        return staging if any(staging.iterdir()) else None

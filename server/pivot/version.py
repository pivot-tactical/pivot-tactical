"""Version awareness (spec §3.7.2).

The current version is embedded at build time as a semantic version plus the git
commit SHA and build date. During development these fall back to runtime
discovery (reading ``git`` / a generated ``_buildinfo.py``) so the About tab and
window title always show something meaningful.

Semantic-version comparison here is used by the update manager (spec §3.7.3) to
order GitHub release tags relative to the running build.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

# Canonical semantic version. Bumped per release; the packaged build overwrites
# the buildinfo fields below via packaging/_buildinfo.py generated in CI.
__version__ = "1.0.4"

_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@dataclass(frozen=True, order=True)
class SemVer:
    """A comparable semantic version.

    Ordering follows SemVer precedence: numeric core compared first, then a
    *present* pre-release sorts *before* the equivalent release (1.0.0-rc < 1.0.0).
    Build metadata is ignored for precedence, per the SemVer spec.
    """

    # NOTE: field order matters for dataclass(order=True). The numeric core then
    # ``pre_key`` form the comparison key; ``pre_key`` encodes pre-release
    # precedence. ``pre`` is redundant for ordering and ``build`` metadata must
    # be ignored for precedence (SemVer §10), so both are compare=False.
    major: int
    minor: int
    patch: int
    pre_key: tuple = ()
    pre: str | None = field(default=None, compare=False)
    build: str | None = field(default=None, compare=False)

    @classmethod
    def parse(cls, text: str) -> Self:
        m = _SEMVER_RE.match(text.strip())
        if not m:
            raise ValueError(f"not a semantic version: {text!r}")
        pre = m.group("pre")
        return cls(
            major=int(m.group("major")),
            minor=int(m.group("minor")),
            patch=int(m.group("patch")),
            pre_key=_pre_release_key(pre),
            pre=pre,
            build=m.group("build"),
        )

    @classmethod
    def try_parse(cls, text: str) -> Self | None:
        try:
            return cls.parse(text)
        except ValueError:
            return None

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            s += f"-{self.pre}"
        if self.build:
            s += f"+{self.build}"
        return s

    @property
    def is_prerelease(self) -> bool:
        return self.pre is not None


def _pre_release_key(pre: str | None) -> tuple:
    """Build a comparison key so that a release outranks its pre-releases.

    A release (no pre) must sort *after* any pre-release of the same core
    version. We model that with a leading flag: ``(1,)`` for releases, and
    ``(0, <identifiers>)`` for pre-releases. Numeric identifiers compare as
    numbers and below alphanumerics, per SemVer §11.
    """
    if pre is None:
        return (1,)
    parts: list = [0]
    for ident in pre.split("."):
        if ident.isdigit():
            parts.append((0, int(ident)))
        else:
            parts.append((1, ident))
    return tuple(parts)


@dataclass(frozen=True)
class VersionInfo:
    """Full build identity shown in About / title bar (spec §3.7.2)."""

    version: str
    git_sha: str
    build_date: str

    @property
    def semver(self) -> SemVer:
        return SemVer.parse(self.version)

    @property
    def title(self) -> str:
        return f"PIVOT {self.version} ({self.git_sha[:7]})"


def _read_buildinfo() -> tuple[str, str, str | None] | None:
    """Read CI-generated build metadata if present (packaged builds).

    Returns ``(git_sha, build_date, version_override)``. The version override is
    set for prerelease builds so the running app reports e.g. ``1.0.0-dev.42``
    and the update manager can order it against published releases (§3.7.3).
    """
    try:
        from pivot import _buildinfo  # type: ignore

        return (
            _buildinfo.GIT_SHA,
            _buildinfo.BUILD_DATE,
            getattr(_buildinfo, "VERSION", None),
        )
    except Exception:
        return None


def _discover_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_version_info() -> VersionInfo:
    bi = _read_buildinfo()
    if bi is not None:
        git_sha, build_date, version_override = bi
        return VersionInfo(version_override or __version__, git_sha, build_date)
    return VersionInfo(__version__, _discover_git_sha(), "dev")


version_info = get_version_info()

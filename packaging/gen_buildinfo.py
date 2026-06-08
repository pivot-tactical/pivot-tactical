"""Generate ``pivot/_buildinfo.py`` at build time (spec §3.7.2).

Embeds the git commit SHA and build date so the running executable can report
its exact build identity in About / the title bar. For **prerelease** builds the
release workflow also passes ``PIVOT_BUILD_VERSION`` (e.g. ``1.0.0-dev.42``),
which is embedded as ``VERSION`` so the app reports the prerelease version and
the update manager can order it against published releases (§3.7.3).

    python packaging/gen_buildinfo.py                       # sha + date
    PIVOT_BUILD_VERSION=1.0.0-dev.42 python packaging/gen_buildinfo.py
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "server" / "pivot" / "_buildinfo.py"


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except OSError:
        pass
    return "unknown"


def render(sha: str, build_date: str, version: str | None = None) -> str:
    """Render the _buildinfo.py module text (pure; unit-tested)."""
    lines = [
        '"""Generated at build time — do not edit (spec §3.7.2)."""',
        f'GIT_SHA = "{sha}"',
        f'BUILD_DATE = "{build_date}"',
    ]
    if version:
        lines.append(f'VERSION = "{version}"')
    return "\n".join(lines) + "\n"


def patch_version(version: str) -> bool:
    """Rewrite ``__version__`` in version.py to the build version.

    This is the bulletproof path: version.py is always imported (never lazily),
    so the frozen app reports the right version even if PyInstaller misses the
    lazily-imported _buildinfo module. Returns True if a substitution was made.
    """
    import re

    vp = TARGET.parent / "version.py"
    text = vp.read_text()
    new, n = re.subn(r'__version__ = "[^"]*"', f'__version__ = "{version}"', text, count=1)
    if n:
        vp.write_text(new)
    return bool(n)


def main() -> None:
    sha = _git_sha()
    build_date = _dt.date.today().isoformat()
    version = os.environ.get("PIVOT_BUILD_VERSION", "").strip() or None
    TARGET.write_text(render(sha, build_date, version))
    patched = patch_version(version) if version else False
    print(
        f"wrote {TARGET} (sha={sha[:10]}, date={build_date}, "
        f"version={version or '(base)'}, patched_version_py={patched})"
    )


if __name__ == "__main__":
    main()

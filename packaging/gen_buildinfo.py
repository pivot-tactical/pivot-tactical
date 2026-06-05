"""Generate ``pivot/_buildinfo.py`` at build time (spec §3.7.2).

Embeds the git commit SHA and build date so the running executable can report
its exact build identity in the About tab and window title. Run in CI right
before PyInstaller:

    python packaging/gen_buildinfo.py
"""

from __future__ import annotations

import datetime as _dt
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


def main() -> None:
    sha = _git_sha()
    build_date = _dt.date.today().isoformat()
    TARGET.write_text(
        '"""Generated at build time — do not edit (spec §3.7.2)."""\n'
        f'GIT_SHA = "{sha}"\n'
        f'BUILD_DATE = "{build_date}"\n'
    )
    print(f"wrote {TARGET} (sha={sha[:10]}, date={build_date})")


if __name__ == "__main__":
    main()

"""Reveal a folder in the host's native file manager (spec §3.5.1 UX).

PIVOT runs as a local instructor-station server and the browser console
usually lives on the same machine, so "Open recordings folder" asks the
*server host* to launch its file manager pointed at the recordings directory —
letting the instructor find WAVs without hunting for the data dir.

Best-effort by design: on a headless host (e.g. a systemd service with no
display) there is nothing to open, so :func:`open_in_file_manager` raises and
the caller falls back to simply reporting the path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_in_file_manager(path: Path) -> None:
    """Open ``path`` in the OS file manager. Raise on failure.

    ``path`` is a server-owned directory (the configured recordings dir), never
    client input, so there is no command-injection surface — and each launcher
    is invoked with an argv list rather than a shell string regardless.
    """
    target = str(path)
    if sys.platform.startswith("win"):
        # The reliable "open this folder in Explorer" on Windows. Fixed,
        # server-owned path — no injection surface.
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=True)
    else:
        subprocess.run(["xdg-open", target], check=True)

"""Fetch releases from the GitHub REST API (spec §3.7.3).

The only outbound internet call in the system, made explicitly by the instructor
(or an opt-in startup check) and out-of-band — never during a session (§3.7.1).
Uses the stdlib ``urllib`` so the runtime needs no extra HTTP dependency; an
optional token raises the rate limit / allows private repos (§3.7.8). Failures
are surfaced to the caller so air-gapped sites degrade gracefully to offline
import.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"


def fetch_releases(repo: str, token: str | None = None, timeout: float = 10.0) -> list[dict]:
    """Return the raw GitHub releases JSON for ``owner/repo`` (newest first).

    Raises on network/HTTP errors; the caller decides how to present them.
    """
    url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PIVOT-UpdateCheck",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []

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
import threading
import time
import urllib.error
import urllib.request
from functools import wraps

GITHUB_API = "https://api.github.com"


def ttl_cache(maxsize: int = 128, ttl_seconds: float = 300.0):
    """Simple thread-safe TTL cache decorator."""

    def decorator(func):
        cache: dict[str, tuple[list[dict], float]] = {}
        lock = threading.Lock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(kwargs)
            now = time.time()

            with lock:
                if key in cache:
                    result, timestamp = cache[key]
                    if now - timestamp < ttl_seconds:
                        return result

            result = func(*args, **kwargs)

            with lock:
                cache[key] = (result, now)

                if len(cache) > maxsize:
                    # Create a list of items to avoid "dictionary changed size during iteration"
                    oldest_key = min(list(cache.items()), key=lambda item: item[1][1])[0]
                    del cache[oldest_key]

            return result

        return wrapper

    return decorator


@ttl_cache(ttl_seconds=300.0)
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

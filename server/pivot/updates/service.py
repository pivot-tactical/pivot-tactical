"""Background update service: always-on async checks + policy (spec §3.7).

The instructor never waits on the network. A daemon thread polls GitHub on an
interval (and on startup), caches the result, and the API just returns that
cache. Independently of whether *auto-update* is enabled, the check always runs
so the Settings panel can show the live list of available releases.

Policy when auto-update **is** enabled:

* apply the newest release on the selected channel,
* but **only when no training session is running** (§3.7.1 — out-of-band). If a
  session is live, the apply is deferred; it happens at the next tick after the
  session ends (the runtime nudges the service via :meth:`trigger`).

All the moving parts are injected (config, session-active flag, release fetch,
apply) so the policy is unit-tested without a network or a real updater.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from pivot.core.timebase import to_iso_utc, utc_now
from pivot.updates.manager import Release, UpdateConfig, UpdateManager, classify_release
from pivot.version import SemVer

log = logging.getLogger("pivot.updates")

# How often the background thread re-checks, when left to its own devices.
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


def _release_to_dict(r: Release, current: SemVer) -> dict:
    return {
        "tag": r.tag,
        "name": r.name,
        "published_at": r.published_at,
        "prerelease": r.prerelease,
        "asset_name": r.asset_name,
        "asset_url": r.asset_url,
        "sha256_url": r.sha256_url,
        "sig_url": r.sig_url,
        "has_asset": bool(r.asset_url),
        "standing": classify_release(r, current).value,
    }


class UpdateService:
    """Owns the cached update status and the background poll/apply loop."""

    def __init__(
        self,
        *,
        version: str,
        versions_dir: Path,
        config_provider: Callable[[], dict],
        session_active: Callable[[], bool],
        releases_provider: Callable[[str, str | None], list[dict]],
        updater_kind: Callable[[], str],
        apply_fn: Callable[[Release, dict], dict] | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        on_change: Callable[[dict], None] | None = None,
    ) -> None:
        self._version = version
        self._versions_dir = Path(versions_dir)
        self._config = config_provider
        self._session_active = session_active
        self._fetch = releases_provider
        self._updater_kind = updater_kind
        self._apply = apply_fn or self._default_apply
        self._interval = interval_seconds
        self._on_change = on_change

        self._lock = threading.Lock()
        self._status: dict = {
            "current_version": version,
            "updater": "staged",
            "reachable": False,
            "error": None,
            "checking": False,
            "last_checked": None,
            "channel": "stable",
            "auto_update": False,
            "releases": [],
            "available": [],
            "retained": [],
            "previous": None,
            "auto_state": "idle",
            "auto_message": "",
        }
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- public API -------------------------------------------------------- #

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="update-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Ask the loop to re-check now (e.g. a session just ended)."""
        self._wake.set()

    # -- loop -------------------------------------------------------------- #

    def _run(self) -> None:
        # First check shortly after boot, then on the interval or when nudged.
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:  # never let the daemon die
                self._merge({"reachable": False, "error": str(exc), "checking": False})
            self._wake.wait(self._interval)
            self._wake.clear()

    # -- the actual check + policy ---------------------------------------- #

    def refresh(self) -> dict:
        """Fetch releases, refresh the cache, and apply auto-update if due."""
        cfg = self._config()
        channel = str(cfg.get("update_channel", "stable"))
        include_pre = channel == "include_prereleases"
        auto = bool(cfg.get("auto_update", False))
        repo = str(cfg.get("github_repo") or "")
        token = str(cfg.get("github_token") or "") or None

        self._merge(
            {
                "checking": True,
                "channel": channel,
                "auto_update": auto,
                "updater": self._updater_kind(),
            }
        )

        try:
            raw = self._fetch(repo, token)
        except Exception as exc:
            # Surfaced verbatim in Settings ("GitHub unreachable — <reason>") and
            # logged here so the instructor can diagnose proxy/DNS/TLS/rate-limit
            # failures that don't affect ordinary browsing (the browser uses the
            # OS proxy/cert store; this stdlib urllib request does not) (§3.7.3).
            log.warning("update check for %r failed: %s", repo, exc)
            self._merge(
                {
                    "reachable": False,
                    "error": str(exc),
                    "checking": False,
                    "last_checked": to_iso_utc(utc_now()),
                }
            )
            return self.snapshot()

        mgr = UpdateManager(
            UpdateConfig(self._version, self._versions_dir, include_prereleases=include_pre)
        )
        cur = SemVer.parse(self._version)
        releases = mgr.list_releases(raw)
        available = mgr.available_updates(raw)

        update = {
            "reachable": True,
            "error": None,
            "checking": False,
            "last_checked": to_iso_utc(utc_now()),
            "channel": channel,
            "auto_update": auto,
            "updater": self._updater_kind(),
            "releases": [_release_to_dict(r, cur) for r in releases],
            "available": [_release_to_dict(r, cur) for r in available],
            # Retained versions for instant offline rollback (§3.7.7).
            "retained": mgr.retained_versions(),
            "previous": mgr.previous_version(),
        }

        # Auto-update policy: newest available, only out-of-band.
        if auto and available:
            if self._session_active():
                update["auto_state"] = "deferred_session_active"
                update["auto_message"] = "Update deferred — a session is running."
            else:
                target = available[0]
                # Broadcast a downloading state so the UI shows progress before
                # the potentially-slow download completes.
                self._merge(
                    {
                        **update,
                        "auto_state": "downloading",
                        "auto_message": f"Downloading {target.tag}…",
                    }
                )
                try:
                    result = self._apply(target, cfg)
                    update["auto_state"] = "applied" if result.get("applied") else "error"
                    update["auto_message"] = result.get("message", "") or (
                        f"Updating to {target.tag}."
                        if result.get("applied")
                        else "Auto-update failed."
                    )
                except Exception as exc:
                    update["auto_state"] = "error"
                    update["auto_message"] = str(exc)
        else:
            update["auto_state"] = "idle"
            update["auto_message"] = ""

        self._merge(update)
        return self.snapshot()

    # -- default apply (real updater) ------------------------------------- #

    def _default_apply(self, release: Release, cfg: dict) -> dict:
        # Single channel-aware path: verified download (SHA-256 + Ed25519) + a
        # staged swap applied on the next restart (§3.7.5). Identical on every
        # platform and channel, so on-the-fly channel switching is honoured.
        mgr = UpdateManager(UpdateConfig(self._version, self._versions_dir))
        # Already staged — don't re-download/re-extract on the next poll tick.
        if mgr.staged_tag() == release.tag:
            return {
                "applied": True,
                "via": "staged",
                "message": f"{release.tag} already staged — restart to apply.",
            }

        token = str(cfg.get("github_token") or "") or None
        mgr.download_and_stage(release, token)
        return {
            "applied": True,
            "via": "staged",
            "message": f"{release.tag} staged — restart to apply.",
        }

    # -- internal ---------------------------------------------------------- #

    def _merge(self, update: dict) -> None:
        with self._lock:
            self._status.update(update)
            snap = dict(self._status)
        if self._on_change is not None:
            try:
                self._on_change(snap)
            except Exception:
                pass

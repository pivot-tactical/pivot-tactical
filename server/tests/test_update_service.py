"""Tests for the background update service (spec §3.7).

Everything the service touches is injected — config, the session-active flag,
the release fetch, and the apply function — so the polling/session-gating policy
is exercised without a network or a real updater.
"""

from __future__ import annotations

from pivot.updates.service import UpdateService

_RELEASES = [
    {"tag_name": "1.1.0", "name": "1.1.0", "prerelease": False, "assets": [
        {"name": "PIVOT-Tactical-v1.1.0-win64.zip", "browser_download_url": "http://x/w"}]},
    {"tag_name": "1.2.0-rc.1", "name": "rc", "prerelease": True, "assets": [
        {"name": "PIVOT-Tactical-v1.2.0-rc.1-win64.zip", "browser_download_url": "http://x/r"}]},
]


def _service(tmp_path, *, config, session_active=lambda: False, apply_fn=None,
             fetch=None, on_change=None):
    return UpdateService(
        version="1.0.0",
        versions_dir=tmp_path,
        config_provider=lambda: dict(config),
        session_active=session_active,
        releases_provider=fetch or (lambda repo, token=None: list(_RELEASES)),
        updater_kind=lambda: "staged",
        apply_fn=apply_fn,
        on_change=on_change,
    )


def test_snapshot_defaults_before_any_check(tmp_path):
    svc = _service(tmp_path, config={})
    snap = svc.snapshot()
    assert snap["current_version"] == "1.0.0"
    assert snap["reachable"] is False
    assert snap["available"] == []
    assert snap["auto_state"] == "idle"


def test_refresh_lists_available_on_stable_channel(tmp_path):
    svc = _service(tmp_path, config={"github_repo": "o/r"})
    snap = svc.refresh()
    assert snap["reachable"] is True
    assert [a["tag"] for a in snap["available"]] == ["1.1.0"]  # rc excluded on stable


def test_refresh_includes_prereleases_when_selected(tmp_path):
    svc = _service(tmp_path, config={"github_repo": "o/r",
                                     "update_channel": "include_prereleases"})
    snap = svc.refresh()
    assert [a["tag"] for a in snap["available"]] == ["1.2.0-rc.1", "1.1.0"]


def test_refresh_graceful_when_fetch_raises(tmp_path):
    def boom(repo, token=None):
        raise OSError("no network")

    svc = _service(tmp_path, config={"github_repo": "o/r"}, fetch=boom)
    snap = svc.refresh()
    assert snap["reachable"] is False
    assert "no network" in (snap["error"] or "")
    assert snap["available"] == []


def test_auto_update_applies_when_no_session(tmp_path):
    applied = []

    def apply_fn(release, cfg):
        applied.append(release.tag)
        return {"applied": True, "message": f"installing {release.tag}"}

    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   session_active=lambda: False, apply_fn=apply_fn)
    snap = svc.refresh()
    assert applied == ["1.1.0"]
    assert snap["auto_state"] == "applied"


def test_auto_update_deferred_during_session(tmp_path):
    applied = []

    def apply_fn(release, cfg):
        applied.append(release.tag)
        return {"applied": True}

    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   session_active=lambda: True, apply_fn=apply_fn)
    snap = svc.refresh()
    assert applied == []  # gated out while a session is live
    assert snap["auto_state"] == "deferred_session_active"


def test_auto_update_error_is_surfaced(tmp_path):
    def apply_fn(release, cfg):
        raise RuntimeError("disk full")

    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   apply_fn=apply_fn)
    snap = svc.refresh()
    assert snap["auto_state"] == "error"
    assert "disk full" in snap["auto_message"]


def test_no_auto_update_leaves_state_idle(tmp_path):
    applied = []
    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": False},
                   apply_fn=lambda r, c: applied.append(r.tag))
    snap = svc.refresh()
    assert applied == []
    assert snap["auto_state"] == "idle"


def test_on_change_called_with_snapshot(tmp_path):
    seen = []
    svc = _service(tmp_path, config={"github_repo": "o/r"},
                   on_change=lambda snap: seen.append(snap))
    svc.refresh()
    assert seen  # at least the "checking" merge and the final result
    assert seen[-1]["reachable"] is True

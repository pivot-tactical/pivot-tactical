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


def _stage(tmp_path, tag: str):
    """Write a pending update marker as a manual 'install this version' does."""
    from pivot.updates.manager import UpdateManager

    app_dir = tmp_path / f"app-{tag}"
    app_dir.mkdir(parents=True, exist_ok=True)
    mgr = UpdateManager("1.0.0", versions_dir=tmp_path)
    mgr.write_pending_marker(mgr.pending_marker_path, tag, app_dir)
    return mgr


def test_auto_update_never_replaces_a_staged_version(tmp_path):
    """A pending update awaiting restart — e.g. a *specific* version the
    instructor deliberately chose — must not be clobbered by auto-staging the
    newest release on the next routine check / 'Check now' (§3.7.4)."""
    mgr = _stage(tmp_path, "1.0.5")  # user picked 1.0.5; newest is 1.1.0
    applied = []
    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   apply_fn=lambda r, c: applied.append(r.tag) or {"applied": True})
    snap = svc.refresh()
    assert applied == []                      # no download of the newest
    assert mgr.staged_tag() == "1.0.5"        # the user's choice survives
    assert snap["staged_tag"] == "1.0.5"      # and the UI is told the truth
    assert snap["auto_state"] == "idle"
    assert "1.0.5" in snap["auto_message"]


def test_snapshot_reports_staged_tag_without_auto_update(tmp_path):
    """staged_tag surfaces the pending version even with auto-update off, so a
    reloaded console still shows what will apply on restart."""
    _stage(tmp_path, "1.0.5")
    svc = _service(tmp_path, config={"github_repo": "o/r"})
    snap = svc.refresh()
    assert snap["staged_tag"] == "1.0.5"


def test_auto_update_stages_newest_onto_clean_slate_and_reports_it(tmp_path):
    """With nothing staged, the routine check stages the newest and staged_tag
    reflects the release that was actually applied."""
    applied = []
    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   apply_fn=lambda r, c: applied.append(r.tag) or {"applied": True})
    snap = svc.refresh()
    assert applied == ["1.1.0"]
    assert snap["staged_tag"] == "1.1.0"
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


def test_downloading_state_broadcast_before_apply(tmp_path):
    """The service must emit auto_state='downloading' before calling apply so the
    UI can show progress during a potentially-slow network download."""
    states_seen: list[str] = []

    def apply_fn(release, cfg):
        # Capture the state visible to on_change at the moment apply is called
        # — it should already be "downloading" from the pre-apply merge.
        return {"applied": True, "message": f"staged {release.tag}"}

    def on_change(snap):
        states_seen.append(snap.get("auto_state", ""))

    svc = _service(tmp_path, config={"github_repo": "o/r", "auto_update": True},
                   apply_fn=apply_fn, on_change=on_change)
    snap = svc.refresh()

    assert "downloading" in states_seen, "expected a 'downloading' broadcast before apply"
    assert snap["auto_state"] == "applied"


def test_default_apply_short_circuits_when_already_staged(tmp_path):
    """If the release is already staged, _default_apply must not re-download or
    re-extract — it reports 'already staged' and points at restart."""
    from pivot.updates.manager import Release, UpdateManager

    versions = tmp_path / "versions"
    # Side-by-side: a staged release is already installed as its own app-<tag>
    # folder (no separate staging copy survives extraction).
    app_dir = versions / "app-1.1.0"
    app_dir.mkdir(parents=True)
    mgr = UpdateManager("1.0.0", versions_dir=versions)
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0", app_dir)

    svc = _service(tmp_path, config={"github_repo": "o/r"})
    svc._versions_dir = versions  # apply uses the service's versions dir

    # A real download would raise (no network / bad url); the short-circuit must
    # return before any download is attempted.
    result = svc._default_apply(Release(tag="1.1.0", asset_url="http://x/none",
                                        asset_name="a.zip"), cfg={})
    assert result["applied"] is True
    assert "already staged" in result["message"]


def test_run_catches_refresh_exception_and_keeps_daemon_alive(tmp_path):
    svc = _service(tmp_path, config={"github_repo": "o/r"})

    def fake_refresh():
        svc.stop()
        raise RuntimeError("mocked error")

    svc.refresh = fake_refresh
    svc._run()

    snap = svc.snapshot()
    assert snap["reachable"] is False
    assert snap["error"] == "mocked error"
    assert snap["checking"] is False

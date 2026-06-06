"""Entry-point behaviour: arg parsing and the Windows-tray decision (§3.1)."""

from __future__ import annotations

import pivot.__main__ as entry


def test_tray_flags_parse():
    assert entry._parse_args([]).tray is None          # unset -> platform default
    assert entry._parse_args(["--tray"]).tray is True
    assert entry._parse_args(["--no-tray"]).tray is False


def test_use_tray_never_on_non_windows(monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "linux")
    assert entry._use_tray(None) is False
    assert entry._use_tray(True) is False   # explicit request still can't on linux
    assert entry._use_tray(False) is False


def test_use_tray_explicit_wins_on_windows(monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "win32")
    assert entry._use_tray(True) is True
    assert entry._use_tray(False) is False


def test_use_tray_default_follows_frozen_on_windows(monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "win32")
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    assert entry._use_tray(None) is True      # packaged exe -> tray
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    assert entry._use_tray(None) is False     # dev run -> console


def test_relaunch_after_flag_parses():
    assert entry._parse_args([]).relaunch_after is None
    assert entry._parse_args(["--relaunch-after", "1234"]).relaunch_after == 1234


def test_restart_mode_detection(monkeypatch):
    from pivot.runtime import lifecycle

    # Explicit override always wins.
    monkeypatch.setenv("PIVOT_RESTART_MODE", "systemd")
    assert lifecycle.restart_mode() == "systemd"
    monkeypatch.delenv("PIVOT_RESTART_MODE", raising=False)

    # systemd sets INVOCATION_ID for the unit.
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert lifecycle.restart_mode() == "systemd"
    monkeypatch.delenv("INVOCATION_ID", raising=False)

    # Frozen exe with no supervisor -> spawn a relauncher.
    monkeypatch.setattr(lifecycle.sys, "frozen", True, raising=False)
    assert lifecycle.restart_mode() == "relaunch"
    # Plain source/dev run -> re-exec in place.
    monkeypatch.setattr(lifecycle.sys, "frozen", False, raising=False)
    assert lifecycle.restart_mode() == "exec"

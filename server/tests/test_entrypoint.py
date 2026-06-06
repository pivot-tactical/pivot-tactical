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

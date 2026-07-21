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


def test_rollback_flag_parses():
    # Absent -> None; bare flag -> "" (roll back to most recent); with a tag.
    assert entry._parse_args([]).rollback is None
    assert entry._parse_args(["--rollback"]).rollback == ""
    assert entry._parse_args(["--rollback", "1.1.0"]).rollback == "1.1.0"


def test_settings_from_args_frozen_uses_absolute_paths(monkeypatch, tmp_path):
    """Frozen exe must use exe-relative absolute paths regardless of cwd."""
    fake_exe = tmp_path / "PIVOT-Tactical.exe"
    fake_exe.touch()
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(fake_exe))

    args = entry._parse_args([])
    settings = entry._settings_from_args(args)

    assert settings.data_dir == tmp_path / "data"
    assert settings.versions_dir == tmp_path / "versions"


def test_settings_from_args_frozen_explicit_data_dir_wins(monkeypatch, tmp_path):
    """--data-dir still overrides the frozen default."""
    fake_exe = tmp_path / "PIVOT-Tactical.exe"
    fake_exe.touch()
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(fake_exe))

    custom = tmp_path / "custom_data"
    args = entry._parse_args(["--data-dir", str(custom)])
    settings = entry._settings_from_args(args)

    assert settings.data_dir == custom


def test_relaunch_after_brings_app_back_even_if_apply_fails(monkeypatch, tmp_path):
    """The detached relauncher must wait, then start the app — and still start it
    if applying a staged update raises — while logging to a file rather than a
    dead console (which previously killed it before it relaunched)."""
    from pivot.config import Settings
    from pivot.runtime import lifecycle

    settings = Settings(data_dir=tmp_path / "data", versions_dir=tmp_path / "versions")
    calls = {"waited": None, "spawned": 0}
    monkeypatch.setattr(lifecycle, "wait_for_exit",
                        lambda pid, **k: calls.__setitem__("waited", pid))
    monkeypatch.setattr(lifecycle, "spawn_app",
                        lambda exe=None: calls.__setitem__("spawned", calls["spawned"] + 1))

    # A staged update is pending, but applying it blows up.
    from pivot.updates.manager import UpdateManager
    mgr = UpdateManager("1.0.0", versions_dir=settings.versions_dir)
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0",
                             settings.versions_dir / "app-1.1.0")

    def boom(_s):
        raise RuntimeError("bad swap")

    monkeypatch.setattr(entry, "_apply_staged", boom)

    rc = entry._relaunch_after(4321, settings)

    assert rc == 0
    assert calls["waited"] == 4321
    assert calls["spawned"] == 1                      # app brought back despite the failure
    assert (tmp_path / "data" / "relaunch.log").exists()  # work is captured, not lost


def test_apply_staged_for_relaunch_noop_without_marker(monkeypatch, tmp_path):
    """A plain restart (nothing staged) must not apply — and must never elevate."""
    from pivot.config import Settings

    settings = Settings(data_dir=tmp_path / "data", versions_dir=tmp_path / "versions")
    called = {"apply": 0}
    monkeypatch.setattr(entry, "_apply_staged", lambda _s: called.__setitem__("apply", 1))

    entry._apply_staged_for_relaunch(settings)  # no pending marker

    assert called["apply"] == 0


def test_apply_staged_for_relaunch_applies_in_process_when_not_frozen(monkeypatch, tmp_path):
    """On a dev/Linux run a pending update applies directly, with no UAC dance."""
    from pivot.config import Settings
    from pivot.updates.manager import UpdateManager

    settings = Settings(data_dir=tmp_path / "data", versions_dir=tmp_path / "versions")
    mgr = UpdateManager("1.0.0", versions_dir=settings.versions_dir)
    mgr.write_pending_marker(mgr.pending_marker_path, "1.1.0",
                             settings.versions_dir / "app-1.1.0")
    called = {"apply": 0}
    monkeypatch.setattr(entry, "_apply_staged", lambda _s: called.__setitem__("apply", 1))
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)

    entry._apply_staged_for_relaunch(settings)

    assert called["apply"] == 1


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

def test_relaunch_after_brings_app_back_even_if_apply_for_relaunch_fails(monkeypatch, tmp_path):
    """The detached relauncher must still start the app if applying a staged update via _apply_staged_for_relaunch raises an Exception."""
    from pivot.config import Settings
    from pivot.runtime import lifecycle

    settings = Settings(data_dir=tmp_path / "data", versions_dir=tmp_path / "versions")
    calls = {"waited": None, "spawned": 0}
    monkeypatch.setattr(lifecycle, "wait_for_exit", lambda pid, **k: calls.__setitem__("waited", pid))
    monkeypatch.setattr(lifecycle, "spawn_app", lambda exe=None: calls.__setitem__("spawned", calls["spawned"] + 1))

    def boom(_s):
        raise RuntimeError("apply for relaunch failed")

    monkeypatch.setattr(entry, "_apply_staged_for_relaunch", boom)

    rc = entry._relaunch_after(4321, settings)

    assert rc == 0
    assert calls["waited"] == 4321
    assert calls["spawned"] == 1

def test_lan_ip_success(monkeypatch):
    from unittest.mock import MagicMock
    mock_socket_instance = MagicMock()
    mock_socket_instance.getsockname.return_value = ("192.168.1.50", 12345)
    mock_socket = MagicMock(return_value=mock_socket_instance)
    monkeypatch.setattr(entry.socket, "socket", mock_socket)

    ip = entry._lan_ip()

    assert ip == "192.168.1.50"
    mock_socket_instance.connect.assert_called_once_with(("8.8.8.8", 80))
    mock_socket_instance.getsockname.assert_called_once()
    mock_socket_instance.close.assert_called_once()

def test_lan_ip_fallback_on_oserror(monkeypatch):
    from unittest.mock import MagicMock
    mock_socket_instance = MagicMock()
    mock_socket_instance.connect.side_effect = OSError("Network is unreachable")
    mock_socket = MagicMock(return_value=mock_socket_instance)
    monkeypatch.setattr(entry.socket, "socket", mock_socket)

    ip = entry._lan_ip()

    assert ip == "127.0.0.1"
    mock_socket_instance.connect.assert_called_once_with(("8.8.8.8", 80))

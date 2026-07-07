import ctypes
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def win_tray_module(monkeypatch):
    """Fixture to mock ctypes and Windows APIs and cleanly import pivot.win_tray."""
    # Preserve original sys.modules
    original_modules = sys.modules.copy()

    try:
        monkeypatch.setattr(sys, "platform", "win32")

        class MockWintypes:
            UINT = ctypes.c_uint
            HINSTANCE = ctypes.c_void_p
            HICON = ctypes.c_void_p
            HANDLE = ctypes.c_void_p
            HBRUSH = ctypes.c_void_p
            LPCWSTR = ctypes.c_wchar_p
            DWORD = ctypes.c_uint
            HWND = ctypes.c_void_p
            WCHAR = ctypes.c_wchar
            HMENU = ctypes.c_void_p
            LPVOID = ctypes.c_void_p
            BOOL = ctypes.c_int
            HMODULE = ctypes.c_void_p
            WPARAM = ctypes.c_void_p
            LPARAM = ctypes.c_void_p
            MSG = ctypes.c_void_p
            POINT = ctypes.c_void_p

        monkeypatch.setattr(ctypes, "wintypes", MockWintypes(), raising=False)
        monkeypatch.setitem(sys.modules, "ctypes.wintypes", MockWintypes())
        monkeypatch.setattr(ctypes, "windll", MagicMock(), raising=False)
        monkeypatch.setattr(
            ctypes, "WINFUNCTYPE", MagicMock(return_value=ctypes.c_void_p), raising=False
        )

        if "pivot.win_tray" in sys.modules:
            del sys.modules["pivot.win_tray"]

        import pivot.win_tray

        yield pivot.win_tray

    finally:
        sys.modules.clear()
        sys.modules.update(original_modules)


def test_hide_console(win_tray_module):
    # Test 1: hwnd exists
    win_tray_module.kernel32.GetConsoleWindow.return_value = 12345
    win_tray_module.hide_console()
    win_tray_module.user32.ShowWindow.assert_called_with(12345, win_tray_module.SW_HIDE)


def test_hide_console_no_hwnd(win_tray_module):
    # Test 2: hwnd does not exist
    win_tray_module.user32.ShowWindow.reset_mock()
    win_tray_module.kernel32.GetConsoleWindow.return_value = 0
    win_tray_module.hide_console()
    win_tray_module.user32.ShowWindow.assert_not_called()


def test_show_console(win_tray_module):
    # Test 3: _show_console logic
    win_tray_module.kernel32.GetConsoleWindow.return_value = 12345
    win_tray_module.user32.ShowWindow.reset_mock()
    win_tray_module._show_console(True)
    win_tray_module.user32.ShowWindow.assert_called_with(12345, win_tray_module.SW_SHOW)

    win_tray_module.user32.ShowWindow.reset_mock()
    win_tray_module._show_console(False)
    win_tray_module.user32.ShowWindow.assert_called_with(12345, win_tray_module.SW_HIDE)


def test_show_console_no_hwnd(win_tray_module):
    # Test 4: _show_console without hwnd
    win_tray_module.kernel32.GetConsoleWindow.return_value = 0
    win_tray_module.user32.ShowWindow.reset_mock()
    win_tray_module._show_console(True)
    win_tray_module.user32.ShowWindow.assert_not_called()

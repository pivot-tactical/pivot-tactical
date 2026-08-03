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
        # WINFUNCTYPE builds a real callback type on Windows: stand in the
        # cdecl equivalent so WNDPROC works both as a _WNDCLASS field type and
        # as a factory wrapping the bound window procedure.
        monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)

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


def test_create_window_arguments_match_win32_prototype(win_tray_module):
    """Every CreateWindowExW argument must be convertible to its declared type.

    Regression test: hMenu was passed as ``None`` while its ctypes type is an
    integer (HMENU), which ctypes rejects — the packaged app died at startup
    with "argument 10: TypeError: wrong type" before the tray ever appeared.
    """
    win_tray_module.kernel32.GetModuleHandleW.return_value = 1
    win_tray_module.user32.CreateWindowExW.return_value = 4242

    app = win_tray_module.TrayApp("https://192.168.0.2:8080")
    app._create_window()

    assert app._hwnd == 4242
    args = win_tray_module.user32.CreateWindowExW.call_args.args
    argtypes = win_tray_module.CREATE_WINDOW_EX_ARGTYPES
    assert len(args) == len(argtypes)
    for position, (argtype, value) in enumerate(zip(argtypes, args, strict=True), start=1):
        try:
            argtype.from_param(value)
        except TypeError as exc:  # pragma: no cover - only on regression
            pytest.fail(f"argument {position} ({value!r}) is not a valid {argtype}: {exc}")


def test_create_window_raises_when_window_creation_fails(win_tray_module):
    """A null HWND must surface as an error, not a silently broken tray."""
    win_tray_module.kernel32.GetModuleHandleW.return_value = 1
    win_tray_module.user32.CreateWindowExW.return_value = None

    app = win_tray_module.TrayApp("https://192.168.0.2:8080")
    with pytest.raises(OSError):
        app._create_window()

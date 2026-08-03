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
        # win_tray loads its DLLs with WinDLL(..., use_last_error=True) so that
        # ctypes.get_last_error() reports the real Win32 error. Hand out one
        # stable mock per library name, so user32/kernel32/shell32 stay distinct
        # module attributes that tests can drive independently.
        _dlls: dict[str, MagicMock] = {}
        monkeypatch.setattr(
            ctypes,
            "WinDLL",
            lambda name, **kwargs: _dlls.setdefault(name, MagicMock()),
            raising=False,
        )
        monkeypatch.setattr(ctypes, "get_last_error", MagicMock(return_value=0), raising=False)
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


def test_quit_exception_logging(win_tray_module, monkeypatch):
    # Mock logger to verify exception is called
    mock_logger = MagicMock()
    monkeypatch.setattr(win_tray_module, "logger", mock_logger)

    def raising_on_quit():
        raise Exception("Mock error")

    tray = win_tray_module.TrayApp.__new__(win_tray_module.TrayApp)
    tray._on_quit = raising_on_quit
    tray._nid = None
    tray._hwnd = None
    tray._quit()
    mock_logger.exception.assert_called_with("Error in on_quit callback")


def test_add_icon_raises_when_the_shell_rejects_it(win_tray_module):
    """A failed Shell_NotifyIcon must raise, not leave a hidden, iconless PIVOT.

    run_with_tray hides the console before this runs, so a silent failure would
    drop into the message loop with the server running, no tray icon, and no way
    to quit it. Raising lets the caller restore the console and keep serving.
    """
    win_tray_module.user32.LoadIconW.return_value = 7  # HICON is a pointer field
    win_tray_module.shell32.Shell_NotifyIconW.return_value = 0
    win_tray_module.ctypes.get_last_error.return_value = 5

    app = win_tray_module.TrayApp("https://192.168.0.2:8080")
    app._hwnd = 4242
    with pytest.raises(OSError):
        app._add_icon()


def test_register_class_tolerates_an_already_registered_class(win_tray_module):
    """Re-registering the window class is expected, not fatal."""
    win_tray_module.kernel32.GetModuleHandleW.return_value = 1
    win_tray_module.user32.RegisterClassW.return_value = 0
    win_tray_module.user32.CreateWindowExW.return_value = 4242
    win_tray_module.ctypes.get_last_error.return_value = (
        win_tray_module.ERROR_CLASS_ALREADY_EXISTS
    )

    app = win_tray_module.TrayApp("https://192.168.0.2:8080")
    app._create_window()

    assert app._hwnd == 4242


def test_register_class_raises_on_a_real_failure(win_tray_module):
    """Any other RegisterClassW error leaves no class for CreateWindowExW."""
    win_tray_module.kernel32.GetModuleHandleW.return_value = 1
    win_tray_module.user32.RegisterClassW.return_value = 0
    win_tray_module.ctypes.get_last_error.return_value = 8  # ERROR_NOT_ENOUGH_MEMORY

    app = win_tray_module.TrayApp("https://192.168.0.2:8080")
    with pytest.raises(OSError):
        app._create_window()

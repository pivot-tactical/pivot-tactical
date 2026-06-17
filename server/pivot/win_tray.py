"""Windows system-tray host for the headless server (spec §3.1, §9.1).

PIVOT has no desktop GUI — the instructor and trainees use a browser over the
LAN. On Windows we still don't want a console window cluttering the taskbar, so
the packaged app hides its console and drops a **notification-area (tray) icon**
instead. Left-click opens the UI in a browser; double-click surfaces the console
so the log can be read; right-click opens a menu to open the UI, copy the LAN
address, show/hide the log window, or quit.

This is implemented with pure ``ctypes`` against the Win32 API — no third-party
dependency, so it adds nothing to the licence surface. It is Windows-only and
imported lazily; every other platform ignores it. The server itself runs on a
background thread while the Win32 message loop owns the main thread (required for
``Shell_NotifyIcon`` and the popup menu).
"""

import sys
import threading
import webbrowser
from collections.abc import Callable

# This module must never be imported on non-Windows platforms.
if sys.platform != "win32":  # pragma: no cover - guarded by callers
    raise ImportError("win_tray is Windows-only")

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# LRESULT / handles are pointer-sized (64-bit on a 64-bit Python). ctypes
# defaults every function's restype/argtypes to C ``int`` (32-bit), which on x64
# truncates handles and corrupts pointer arguments — a HMENU passed to
# AppendMenuW then arrives with garbage high bits, so the menu shows up empty (a
# blank white rectangle). Declaring prototypes makes ctypes pass/return the full
# 64-bit values. We only need this for calls that take or return handles.
LRESULT = ctypes.c_ssize_t
HMENU = wintypes.HMENU
HWND = wintypes.HWND


def _configure_win32_prototypes() -> None:
    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CreateWindowExW.restype = HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        HWND, HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.DestroyWindow.argtypes = [HWND]
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    # Menu calls — the heart of the empty-menu bug: handles must be 64-bit.
    user32.CreatePopupMenu.restype = HMENU
    user32.CreatePopupMenu.argtypes = []
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.AppendMenuW.argtypes = [HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.TrackPopupMenu.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [
        HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, ctypes.c_void_p,
    ]
    user32.DestroyMenu.argtypes = [HMENU]
    # Clipboard: GlobalAlloc/GlobalLock/SetClipboardData return pointers/handles
    # that would be truncated (and then dereferenced) without these.
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = [HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetConsoleWindow.restype = HWND
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]


_configure_win32_prototypes()

# --- Win32 constants -------------------------------------------------------- #
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

# Window-class style: deliver double-click messages to the tray callback. Without
# CS_DBLCLKS Windows only ever sends single-click ups, so a double-click on the
# icon would never reach _on_message as WM_LBUTTONDBLCLK.
CS_DBLCLKS = 0x0008

NIM_ADD = 0x0000
NIM_DELETE = 0x0002
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004

IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x0040
LR_SHARED = 0x8000
IDI_APPLICATION = 32512

TPM_RIGHTALIGN = 0x0008
TPM_BOTTOMALIGN = 0x0020
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

SW_HIDE = 0
SW_SHOW = 5

# Menu command ids.
ID_OPEN = 1001
ID_COPY = 1002
ID_LOG = 1003
ID_QUIT = 1004


def hide_console() -> None:
    """Hide the attached console window (the app lives in the tray instead)."""
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32.ShowWindow(hwnd, SW_HIDE)


def _show_console(show: bool) -> None:
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        user32.ShowWindow(hwnd, SW_SHOW if show else SW_HIDE)


def _copy_to_clipboard(text: str) -> None:
    """Put ``text`` on the clipboard (best-effort; ignored on failure)."""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    try:
        if not user32.OpenClipboard(0):
            return
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
    finally:
        user32.CloseClipboard()


WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
    ]


class TrayApp:
    """A minimal tray icon driving the headless server's lifecycle."""

    def __init__(self, url: str, on_quit: Callable[[], None] | None = None,
                 tooltip: str = "PIVOT — running") -> None:
        self.url = url
        self.tooltip = tooltip
        self._on_quit = on_quit
        self._console_visible = False
        self._hwnd = None
        self._nid = None
        # Keep a reference so the WNDPROC callback isn't garbage-collected.
        self._wndproc = WNDPROC(self._on_message)

    # -- message handling -------------------------------------------------- #

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            if lparam == WM_LBUTTONDBLCLK:
                # Double-click: surface the console so the log can be read.
                self._set_console(True)
            elif lparam == WM_LBUTTONUP:
                webbrowser.open(self.url)
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == ID_OPEN:
                webbrowser.open(self.url)
            elif cmd == ID_COPY:
                _copy_to_clipboard(self.url)
            elif cmd == ID_LOG:
                self._set_console(not self._console_visible)
            elif cmd == ID_QUIT:
                self._quit()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _set_console(self, visible: bool) -> None:
        """Show or hide the log console window and remember the state."""
        self._console_visible = visible
        _show_console(visible)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Open PIVOT in browser")
        user32.AppendMenuW(menu, MF_STRING, ID_COPY, "Copy LAN address")
        user32.AppendMenuW(menu, MF_STRING, ID_LOG,
                           "Hide log window" if self._console_visible else "Show log window")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit PIVOT")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Required so the menu dismisses correctly when clicking elsewhere.
        user32.SetForegroundWindow(self._hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTALIGN | TPM_BOTTOMALIGN,
                              pt.x, pt.y, 0, self._hwnd, None)
        user32.DestroyMenu(menu)

    def _quit(self) -> None:
        if self._nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        if self._on_quit is not None:
            try:
                self._on_quit()
            except Exception:
                pass
        user32.DestroyWindow(self._hwnd)

    # -- setup + loop ------------------------------------------------------ #

    def _create_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = _WNDCLASS()
        wc.style = CS_DBLCLKS  # so the tray icon reports double-clicks
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "PIVOTTrayWindow"
        user32.RegisterClassW(ctypes.byref(wc))
        self._hwnd = user32.CreateWindowExW(
            0, wc.lpszClassName, "PIVOT", 0, 0, 0, 0, 0, None, None, hinst, None
        )

    def _add_icon(self) -> None:
        hicon = user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))
        nid = _NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid

    def run(self) -> None:
        """Create the tray icon and pump the Win32 message loop (blocking)."""
        self._create_window()
        self._add_icon()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def run_with_tray(serve: Callable[[], None], url: str,
                  tooltip: str = "PIVOT — running") -> None:
    """Hide the console, run ``serve`` on a daemon thread, own the tray loop.

    ``serve`` should block (it runs uvicorn). Quitting from the tray menu tears
    the icon down and exits the process — uvicorn dies with the daemon thread.
    """
    hide_console()
    threading.Thread(target=serve, name="pivot-server", daemon=True).start()
    app = TrayApp(url, on_quit=lambda: None, tooltip=tooltip)
    app.run()
    # Tray loop returned (Quit chosen): drop out so the process exits.

"""Is a game or a fullscreen video on screen right now?

Two cheap Windows checks, used together because neither covers everything:

``SHQueryUserNotificationState``
    The API the shell itself uses to decide whether to hold back toast
    notifications. It reports presentation mode (fullscreen video, including a
    browser's fullscreen player) and fullscreen Direct3D games.

a foreground-window rectangle test
    Catches borderless-windowed games and media players that the state API does
    not classify, but which really do cover the whole monitor.

Both return False off Windows, so the widget simply never auto-hides elsewhere.
Nothing here raises: a failure just means "not fullscreen".
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

# --- SHQueryUserNotificationState -------------------------------------------
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

#: states in which the shell considers the screen occupied by something else
_BUSY_STATES = frozenset(
    {QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE})

#: window classes belonging to the shell itself: they cover the screen, but
#: they are the desktop, not something sitting on top of the widget
_SHELL_CLASSES = frozenset(
    {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})

#: GetWindowLongW index and the style bits that tell a maximized window apart
#: from a borderless fullscreen one
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_MAXIMIZE = 0x01000000

MONITOR_DEFAULTTONEAREST = 2

S_OK = 0


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


if sys.platform == "win32":
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    except OSError:      # pragma: no cover - only on odd Windows setups
        _user32 = _shell32 = None
    if _user32 is not None:
        # Explicit prototypes matter: without them ctypes assumes c_int, which
        # truncates a 64-bit HWND on the way back.
        _user32.GetForegroundWindow.restype = wintypes.HWND
        _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _user32.GetClassNameW.restype = ctypes.c_int
        _user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        _user32.IsIconic.argtypes = [wintypes.HWND]
        _user32.IsIconic.restype = wintypes.BOOL
        _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _user32.GetWindowRect.restype = wintypes.BOOL
        _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        _user32.GetWindowLongW.restype = wintypes.LONG
        _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        _user32.MonitorFromWindow.restype = wintypes.HANDLE
        _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
        _user32.GetMonitorInfoW.restype = wintypes.BOOL
        _shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        _shell32.SHQueryUserNotificationState.restype = ctypes.c_long
else:                    # pragma: no cover - the app targets Windows
    _user32 = _shell32 = None


def available() -> bool:
    return _user32 is not None and _shell32 is not None


def notification_state() -> int | None:
    """Raw SHQueryUserNotificationState, or None if it could not be read."""
    if _shell32 is None:
        return None
    state = ctypes.c_int()
    try:
        if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != S_OK:
            return None
    except Exception:  # noqa: BLE001 - purely a hint, never fatal
        return None
    return state.value


def foreground_window() -> int | None:
    if _user32 is None:
        return None
    try:
        hwnd = _user32.GetForegroundWindow()
    except Exception:  # noqa: BLE001
        return None
    return int(hwnd) if hwnd else None


def window_class(hwnd: int) -> str:
    if _user32 is None:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    try:
        _user32.GetClassNameW(wintypes.HWND(hwnd), buffer, len(buffer))
    except Exception:  # noqa: BLE001
        return ""
    return buffer.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    try:
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    except Exception:  # noqa: BLE001
        return 0
    return pid.value


def _window_style(hwnd: int) -> int:
    if _user32 is None:
        return 0
    try:
        return int(_user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE))
    except Exception:  # noqa: BLE001
        return 0


def desktop_is_foreground() -> bool:
    """True when nothing but the desktop / taskbar has the focus."""
    if _user32 is None:
        return True          # not Windows: behave as if the desktop is showing
    hwnd = foreground_window()
    if hwnd is None:
        return True
    if _window_pid(hwnd) == os.getpid():
        # Our own window. Clicking or dragging the card must not count as
        # "something else is in front", or the card would hide itself the
        # moment it is touched.
        return True
    return window_class(hwnd) in _SHELL_CLASSES


def fullscreen_app_is_foreground() -> bool:
    """True when a game or a fullscreen video currently owns the screen."""
    if _user32 is None:
        return False

    if notification_state() in _BUSY_STATES:
        return True

    hwnd = foreground_window()
    if hwnd is None:
        return False
    try:
        if _window_pid(hwnd) == os.getpid():
            return False                    # our own window never counts
        if window_class(hwnd) in _SHELL_CLASSES:
            return False
        if _user32.IsIconic(wintypes.HWND(hwnd)):
            return False
        style = _window_style(hwnd)
        if style & WS_MAXIMIZE:
            # A maximized window is not fullscreen: it still leaves the taskbar
            # and its own title bar alone, and the widget is meant to float
            # above exactly that. (Fullscreen windows never carry this bit.)
            return False
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return False
        monitor = _user32.MonitorFromWindow(wintypes.HWND(hwnd),
                                            MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
    except Exception:  # noqa: BLE001
        return False

    full = info.rcMonitor
    covered = (rect.left <= full.left and rect.top <= full.top
               and rect.right >= full.right and rect.bottom >= full.bottom)
    if covered:
        log.debug("fullscreen window: class=%s rect=%s", window_class(hwnd),
                  (rect.left, rect.top, rect.right, rect.bottom))
    return covered


def foreground_is_maximized() -> bool:
    """True when the foreground window is a plain maximized window.

    Worth its own check because "看视频" very often means a maximized player or
    browser window rather than a fullscreen one.
    """
    if _user32 is None:
        return False
    hwnd = foreground_window()
    if hwnd is None:
        return False
    try:
        if _window_pid(hwnd) == os.getpid():
            return False
        if window_class(hwnd) in _SHELL_CLASSES:
            return False
        if _user32.IsIconic(wintypes.HWND(hwnd)):
            return False
    except Exception:  # noqa: BLE001
        return False
    return bool(_window_style(hwnd) & WS_MAXIMIZE)


def describe() -> str:
    """One-line summary, handy when debugging why the widget is hidden."""
    if not available():
        return "foreground checks unavailable (not Windows)"
    hwnd = foreground_window()
    style = _window_style(hwnd) if hwnd else 0
    flags = []
    if style & WS_MAXIMIZE:
        flags.append("MAXIMIZE")
    if style & WS_CAPTION:
        flags.append("CAPTION")
    return (f"state={notification_state()} hwnd={hwnd} "
            f"class={window_class(hwnd) if hwnd else '-'} "
            f"style={style:#010x} {'|'.join(flags) or '-'} "
            f"fullscreen={fullscreen_app_is_foreground()} "
            f"desktop={desktop_is_foreground()}")

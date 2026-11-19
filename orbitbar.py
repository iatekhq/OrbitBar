"""
OrbitBar - a Lumabar-style taskbar replacement for Windows 10/11.

Features
  * Pinned apps + open windows (grouped per app, click to focus/minimize/cycle)
  * Virtual desktops (workspaces) - shows count/current, click to switch, + to add
  * System stats - CPU (with sparkline), RAM, network speed, battery
  * Media - current track title/artist, prev / play-pause / next
  * Volume - scroll to change, click for slider + mute
  * Clock/date - click for a month calendar + world clocks (right-click: notifications)
  * Weather (Open-Meteo, no account), Timer/Pomodoro, Recycle Bin, quick launcher
  * Live window previews on hover, themes (Glass, Midnight, Black, White),
    floating pill or full width, auto-hide - all from the right-click menu
  * Hides the native taskbar while running and restores it on exit

Run:     pythonw orbitbar.py
Repair:  python orbitbar.py --restore     (brings the Windows taskbar back if OrbitBar crashed)
Config:  %APPDATA%\\OrbitBar\\config.json  (right-click empty bar space -> Edit config)
"""

import sys

if sys.platform != "win32":
    sys.exit("OrbitBar runs on Windows 10/11 only.")


def _guard(pid):
    """Watchdog: a tiny second process (no Qt, ~6 MB) that waits for OrbitBar to end. If OrbitBar ended
    without tidying up (a crash, "End task" in Task Manager, a power-user kill) the Windows taskbar would
    stay hidden - so the watchdog brings it back. A normal quit or a reload hand-over leaves it alone."""
    import ctypes
    import ctypes.wintypes as w
    import json
    import os
    import time
    k32, u32, sh32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32"), ctypes.WinDLL("shell32")
    k32.OpenProcess.restype, k32.OpenProcess.argtypes = w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]
    k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k32.OpenMutexW.restype, k32.OpenMutexW.argtypes = w.HANDLE, [w.DWORD, w.BOOL, w.LPCWSTR]
    k32.CloseHandle.argtypes = [w.HANDLE]
    u32.FindWindowExW.restype, u32.FindWindowExW.argtypes = w.HWND, [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]
    u32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    h = k32.OpenProcess(0x00100000, False, pid)                       # SYNCHRONIZE
    if not h:
        return
    k32.WaitForSingleObject(h, 0xFFFFFFFF)                            # sleeps; uses no CPU
    k32.CloseHandle(h)
    state = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OrbitBar", "taskbar_state.json")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3:
        if not os.path.exists(state):
            return                                                    # quit normally: taskbar already back
        m = k32.OpenMutexW(0x00100000, False, "Local\\OrbitBar.SingleInstance")
        if m:
            k32.CloseHandle(m)
            return                                                    # reload: the new copy took over
        time.sleep(0.2)
    try:
        with open(state) as f:
            orig = int(json.load(f).get("orig_state", 2))
    except (OSError, ValueError, TypeError):
        orig = 2

    class ABD(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uCallbackMessage", w.UINT), ("uEdge", w.UINT),
                    ("rc", w.RECT), ("lParam", w.LPARAM)]
    sh32.SHAppBarMessage.argtypes = [w.DWORD, ctypes.POINTER(ABD)]
    sh32.SHAppBarMessage.restype = ctypes.c_size_t
    abd = ABD()
    abd.cbSize = ctypes.sizeof(ABD)
    abd.hWnd = u32.FindWindowExW(None, None, "Shell_TrayWnd", None)
    abd.lParam = orig
    sh32.SHAppBarMessage(10, ctypes.byref(abd))                       # ABM_SETSTATE: original auto-hide setting
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        hw = None
        while True:
            hw = u32.FindWindowExW(None, hw, cls, None)
            if not hw:
                break
            u32.ShowWindow(hw, 5)                                     # SW_SHOW
    try:
        os.remove(state)
    except OSError:
        pass
    try:
        log = os.path.join(os.path.dirname(state), "orbitbar.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} watchdog: OrbitBar (pid {pid}) ended unexpectedly - "
                    "Windows taskbar restored\n")
    except OSError:
        pass


if __name__ == "__main__" and any(a.startswith("--guard=") for a in sys.argv):
    _guard(int(next(a for a in sys.argv if a.startswith("--guard=")).split("=", 1)[1]))
    sys.exit(0)

import atexit
import calendar
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg
from collections import OrderedDict, deque
from datetime import datetime, timedelta

import psutil
from PySide6.QtCore import (QEvent, QFileInfo, QMimeData, QObject, QPoint, QPropertyAnimation, QRectF, QSize, Qt,
                            QThread, QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QDrag, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QRegion)
from PySide6.QtWidgets import (
    QApplication, QLayout, QSizePolicy, QCheckBox, QComboBox, QFileIconProvider, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSlider, QSpinBox, QStyle, QVBoxLayout, QWidget,
)

APP_NAME = "OrbitBar"
APP_PUBLISHER = "IATEK"
# Where releases are published, e.g. "yourname/OrbitBar". Empty = the update check is off.
GITHUB_REPO = "iatekhq/OrbitBar"
APP_VERSION = "5.5.7"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_PATH = os.path.join(CONFIG_DIR, "taskbar_state.json")

DEFAULT_CONFIG = {
    "theme": "midnight",           # "glass", "midnight", "black", "white" or "custom" (uses "background")
    "position": "bottom",          # "bottom" or "top"
    "height": 56,                  # bar height in logical pixels (incl. margin)
    "scale": 1.0,                  # size of everything: 0.9 compact … 1.5 huge
    "icon_size": 24,               # app icons in the middle (px, before scale)
    "widget_size": 1.15,           # weather, timer, stats, volume, clock... on the bar (live, no restart)
    "card_size": 1.15,             # cards, calendar and notifications (live, no restart)
    "margin": 5,                   # gap around the floating dock (0 = edge to edge)
    "radius": 16,                  # corner rounding of the bar (0 = square, like the Windows taskbar)
    "accent": "#7c9cff",
    "background": [18, 18, 26, 228],
    "clock_24h": True,
    "hide_windows_taskbar": True,
    "hide_on_fullscreen": True,
    "autohide": False,             # slide away; show when the mouse touches the screen edge
    "previews": True,              # live window thumbnails when hovering an app
    "weather": {"city": "Toronto", "units": "celsius", "provider": "auto"},   # city "auto" = locate by IP
    "world_clocks": [["UTC", "UTC"], ["London", "Europe/London"], ["Addis Ababa", "Africa/Addis_Ababa"]],
    "week_starts_sunday": True,
    "timer_presets": [["Focus", 25], ["Break", 5], ["Long break", 15]],
    "battery": {"style": "icon", "low_alert": 20, "full_alert": True},   # style: "icon" or "ring"; shown only on PCs with a battery
    "show": {"workspaces": True, "weather": True, "timer": True, "recycle_bin": True,
             "privacy": True, "media": True, "stats": True, "gpu": True, "network": True, "security": True,
             "battery": True, "volume": True, "clock": True, "tray": True, "study": True},
    "alerts": {"security": True, "privacy": True, "network": True},
    "check_updates": True,         # look at GitHub Releases twice a day
    "auto_update": True,           # downloaded build (.exe) only: fetch new versions, install at the next start
    "pinned": [
        r"C:\Windows\explorer.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Windows\System32\notepad.exe",
        r"C:\Windows\System32\Taskmgr.exe",
    ],
}

GLYPH_FONT = "Segoe Fluent Icons, Segoe MDL2 Assets"
G = {  # Segoe Fluent / MDL2 glyphs
    "search": "\uE721", "add": "\uE710", "prev": "\uE892", "next": "\uE893",
    "play": "\uE768", "pause": "\uE769", "vol": "\uE767", "mute": "\uE74F",
    "vol1": "\uE993", "vol2": "\uE994", "settings": "\uE713",
    "timer": "\uE916", "bin": "\uE74D", "close": "\uE8BB", "back": "\uE76B", "fwd": "\uE76C",
    "folder": "\uE8B7", "app": "\uE8FC",
}

THEMES = {
    #            bar background       text       dim        pill            hover            card                  border
    "midnight": dict(bg=(18, 18, 26, 228), text="#e9e9f2", dim="#9a9aae", pill=(255, 255, 255, 13),
                     hover=(255, 255, 255, 26), card=(28, 28, 38, 248), border=(255, 255, 255, 24), blur=False),
    "black":    dict(bg=(0, 0, 0, 246), text="#f2f2f2", dim="#8c8c8c", pill=(255, 255, 255, 14),
                     hover=(255, 255, 255, 28), card=(16, 16, 16, 250), border=(255, 255, 255, 22), blur=False),
    "glass":    dict(bg=(24, 24, 34, 70), text="#f4f4fa", dim="#b4b4c6", pill=(255, 255, 255, 20),
                     hover=(255, 255, 255, 34), card=(32, 32, 44, 242), border=(255, 255, 255, 40), blur=True),
    "white":    dict(bg=(246, 246, 250, 238), text="#1c1c26", dim="#6c6c7c", pill=(0, 0, 0, 10),
                     hover=(0, 0, 0, 20), card=(252, 252, 254, 252), border=(0, 0, 0, 26), blur=False),
}
T = dict(THEMES["midnight"])

# --------------------------------------------------------------------------------------
# Win32 plumbing
# --------------------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
kernel32 = ctypes.WinDLL("kernel32")
dwmapi = ctypes.WinDLL("dwmapi")
ole32 = ctypes.WinDLL("ole32")

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _sig(fn, res, *args):
    fn.restype = res
    fn.argtypes = list(args)


_sig(user32.FindWindowW, wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
_sig(user32.FindWindowExW, wt.HWND, wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
_sig(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.IsWindowVisible, wt.BOOL, wt.HWND)
_sig(user32.IsIconic, wt.BOOL, wt.HWND)
_sig(user32.GetWindow, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.GetWindowLongW, ctypes.c_long, wt.HWND, ctypes.c_int)
_sig(user32.GetWindowTextLengthW, ctypes.c_int, wt.HWND)
_sig(user32.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
_sig(user32.EnumWindows, wt.BOOL, WNDENUMPROC, wt.LPARAM)
_sig(user32.EnumChildWindows, wt.BOOL, wt.HWND, WNDENUMPROC, wt.LPARAM)
_sig(user32.GetForegroundWindow, wt.HWND)
_sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
_sig(user32.SwitchToThisWindow, None, wt.HWND, wt.BOOL)
_sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.SendMessageTimeoutW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
     wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t))
_sig(user32.GetClassLongPtrW, ctypes.c_size_t, wt.HWND, ctypes.c_int)
_sig(user32.GetWindowRect, wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
_sig(user32.SetWindowPos, wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, wt.UINT)
_sig(user32.MonitorFromWindow, wt.HMONITOR, wt.HWND, wt.DWORD)
_sig(user32.MonitorFromPoint, wt.HMONITOR, wt.POINT, wt.DWORD)
_sig(user32.RegisterWindowMessageW, wt.UINT, wt.LPCWSTR)
_sig(user32.keybd_event, None, wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t)
_sig(kernel32.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_sig(kernel32.CloseHandle, wt.BOOL, wt.HANDLE)
_sig(kernel32.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR,
     ctypes.POINTER(wt.DWORD))
_sig(kernel32.ProcessIdToSessionId, wt.BOOL, wt.DWORD, ctypes.POINTER(wt.DWORD))
_sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
_sig(user32.GetAncestor, wt.HWND, wt.HWND, wt.UINT)
_sig(user32.RegisterWindowMessageW, wt.UINT, wt.LPCWSTR)
_sig(user32.RegisterShellHookWindow, wt.BOOL, wt.HWND)
_sig(user32.DeregisterShellHookWindow, wt.BOOL, wt.HWND)
_sig(ole32.CoInitializeEx, ctypes.c_long, ctypes.c_void_p, wt.DWORD)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


_sig(user32.GetMonitorInfoW, wt.BOOL, wt.HMONITOR, ctypes.POINTER(MONITORINFO))


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT),
                ("uEdge", wt.UINT), ("rc", wt.RECT), ("lParam", wt.LPARAM)]


_sig(shell32.SHAppBarMessage, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(APPBARDATA))


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("rcDestination", wt.RECT), ("rcSource", wt.RECT),
                ("opacity", wt.BYTE), ("fVisible", wt.BOOL), ("fSourceClientAreaOnly", wt.BOOL)]


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", wt.BYTE), ("BatteryFlag", wt.BYTE), ("BatteryLifePercent", wt.BYTE),
                ("SystemStatusFlag", wt.BYTE), ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


_sig(dwmapi.DwmRegisterThumbnail, ctypes.c_long, wt.HWND, wt.HWND, ctypes.POINTER(wt.HANDLE))
_sig(dwmapi.DwmUnregisterThumbnail, ctypes.c_long, wt.HANDLE)
_sig(dwmapi.DwmUpdateThumbnailProperties, ctypes.c_long, wt.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES))
_sig(dwmapi.DwmQueryThumbnailSourceSize, ctypes.c_long, wt.HANDLE, ctypes.POINTER(wt.SIZE))
_sig(dwmapi.DwmSetWindowAttribute, ctypes.c_long, wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD)
_sig(user32.SetWindowCompositionAttribute, wt.BOOL, wt.HWND, ctypes.POINTER(WINCOMPATTRDATA))
_sig(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
_sig(kernel32.GetSystemPowerStatus, wt.BOOL, ctypes.POINTER(SYSTEM_POWER_STATUS))
_sig(shell32.SHQueryRecycleBinW, ctypes.c_long, wt.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO))
_sig(shell32.SHEmptyRecycleBinW, ctypes.c_long, wt.HWND, wt.LPCWSTR, wt.DWORD)

DWM_TNP_RECTDESTINATION, DWM_TNP_OPACITY, DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x1, 0x4, 0x8, 0x10
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND, DWMWCP_DONOTROUND = 33, 2, 1

SW_HIDE, SW_SHOW, SW_MINIMIZE, SW_RESTORE = 0, 5, 6, 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
WM_CLOSE, WM_GETICON = 0x0010, 0x007F
GCLP_HICON, GCLP_HICONSM = -14, -34
DWMWA_CLOAKED = 14
ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS, ABM_GETSTATE, ABM_SETSTATE = 0, 1, 2, 3, 4, 10
ABE_TOP, ABE_BOTTOM = 1, 3
ABS_AUTOHIDE, ABS_ALWAYSONTOP = 1, 2
HWND_TOPMOST = wt.HWND(-1)
SWP_NOACTIVATE = 0x0010
HWND_NOTOPMOST = wt.HWND(-2)
HWND_BOTTOM = wt.HWND(1)
TRAY_HOST_HWND = [None]          # our stand-in Shell_TrayWnd (System tray widget), if running


def appbar_message(code, abd):
    """SHAppBarMessage, but always delivered to Explorer itself. Windows sends it to the first window
    called "Shell_TrayWnd" - with the System tray widget on, that was *our* tray stand-in, and Explorer
    then ignored the bar's screen-space request (maximized windows covered the bar, Windows kept
    resetting the space, everything flickered). So step our stand-in aside for the duration of the call."""
    h = TRAY_HOST_HWND[0]
    if h:
        user32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)
        user32.SetWindowPos(h, HWND_BOTTOM, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)
    try:
        return shell32.SHAppBarMessage(code, ctypes.byref(abd))
    finally:
        if h:
            user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002)
MONITOR_DEFAULTTOPRIMARY, MONITOR_DEFAULTTONEAREST = 1, 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2

VK = {"win": 0x5B, "ctrl": 0x11, "alt": 0x12, "left": 0x25, "right": 0x27,
      "d": 0x44, "s": 0x53, "x": 0x58, "n": 0x4E, "a": 0x41, "f4": 0x73,
      "next": 0xB0, "prev": 0xB1, "playpause": 0xB3,
      "mute": 0xAD, "voldown": 0xAE, "volup": 0xAF}
EXTENDED = {VK["win"], VK["left"], VK["right"]}
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def send_keys(*names):
    """Press a key combo, e.g. send_keys('ctrl', 'win', 'right')."""
    codes = [VK[n] for n in names]
    for c in codes:
        user32.keybd_event(c, 0, KEYEVENTF_EXTENDEDKEY if c in EXTENDED else 0, 0)
    for c in reversed(codes):
        user32.keybd_event(c, 0, (KEYEVENTF_EXTENDEDKEY if c in EXTENDED else 0) | KEYEVENTF_KEYUP, 0)


def window_text(h):
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def class_name(h):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, buf, 256)
    return buf.value


def window_pid(h):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def exe_of_pid(pid):
    hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hp:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        ok = kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(hp)


def is_cloaked(h):
    val = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return val.value != 0


_exe_cache = {}


def window_exe(h):
    """Executable behind a window; resolves UWP apps hosted in ApplicationFrameHost."""
    pid = window_pid(h)
    hit = _exe_cache.get(h)
    if hit and hit[0] == pid and hit[1] and not hit[1].lower().endswith("applicationframehost.exe"):
        return hit[1]
    exe = _window_exe(h, pid)
    if len(_exe_cache) > 2000:
        _exe_cache.clear()
    _exe_cache[h] = (pid, exe)
    return exe


def _window_exe(h, pid):
    exe = exe_of_pid(pid)
    if exe.lower().endswith("applicationframehost.exe"):
        found = []

        def cb(child, _):
            cpid = window_pid(child)
            if cpid != pid:
                found.append(cpid)
                return False
            return True

        user32.EnumChildWindows(h, WNDENUMPROC(cb), 0)
        if found:
            exe = exe_of_pid(found[0]) or exe
    return exe


def list_app_windows():
    """Top-level, user-facing windows on the current virtual desktop, in z-order."""
    own = os.getpid()
    out = []

    def cb(h, _):
        if not user32.IsWindowVisible(h) or user32.GetWindow(h, GW_OWNER):
            return True
        ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
        if (ex & WS_EX_TOOLWINDOW or ex & WS_EX_NOACTIVATE) and not ex & WS_EX_APPWINDOW:
            return True
        if user32.GetWindowTextLengthW(h) == 0 or is_cloaked(h):
            return True
        if class_name(h) in SKIP_CLASSES or window_pid(h) == own:
            return True
        out.append(h)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def activate_window(h):
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
    user32.SwitchToThisWindow(h, True)
    user32.SetForegroundWindow(h)


def minimize_window(h):
    user32.ShowWindow(h, SW_MINIMIZE)


def close_window(h):
    user32.PostMessageW(h, WM_CLOSE, 0, 0)


def window_icon(h):
    """HICON of a window as QPixmap (fallback when the exe icon is unavailable)."""
    res = ctypes.c_size_t(0)
    for kind in (1, 2, 0):  # ICON_BIG, ICON_SMALL2, ICON_SMALL
        user32.SendMessageTimeoutW(h, WM_GETICON, kind, 0, 0x2, 25, ctypes.byref(res))
        if res.value:
            break
    hicon = res.value or user32.GetClassLongPtrW(h, GCLP_HICON) or user32.GetClassLongPtrW(h, GCLP_HICONSM)
    if not hicon:
        return None
    try:
        img = QImage.fromHICON(hicon)
        return QPixmap.fromImage(img) if not img.isNull() else None
    except Exception:
        return None


def monitor_info(hmon):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


def primary_monitor_rect():
    hmon = user32.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
    return monitor_info(hmon).rcMonitor


_sig(user32.SystemParametersInfoW, wt.BOOL, wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT)


def is_overlay(h):
    """Screen-drawing overlays (Zoom/Teams annotation, Windows Ink, screen recorders, snipping) cover the
    whole screen with a see-through layer - they aren't fullscreen videos, so the bar should stay."""
    ex = user32.GetWindowLongW(h, -20) & 0xFFFFFFFF                     # GWL_EXSTYLE
    layered, transparent, tool, topmost = ex & 0x80000, ex & 0x20, ex & 0x80, ex & 0x8
    if layered and (transparent or tool or topmost):
        return True
    cls = class_name(h).lower()
    return any(k in cls for k in ("anno", "ink", "screenclipping", "overlay", "whiteboard"))


SHELL_CLASSES = {"SHELLDLL_DefView", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
                 "MultitaskingViewFrame", "ForegroundStaging", "TopLevelWindowForOverflowXamlIsland",
                 "NotifyIconOverflowWindow"}


def is_shell_surface(h):
    """The desktop (after Win+D), Start, search, Task View and other parts of Windows itself.
    They can cover the whole screen without a title bar, but they're never a fullscreen app."""
    cls = class_name(h)
    if cls in SKIP_CLASSES or cls in SHELL_CLASSES:
        return True
    return cls != "CabinetWClass" and proc_name(window_pid(h)).lower() == "explorer"


def foreground_is_fullscreen():
    h = user32.GetForegroundWindow()
    if not h or window_pid(h) == os.getpid() or is_shell_surface(h):
        return False
    if is_overlay(h):
        return False
    # Real fullscreen (YouTube/F11/games) removes the title bar and sizing border; a maximized window
    # keeps them - even a browser that was maximized *before* its video went fullscreen.
    style = user32.GetWindowLongW(h, -16) & 0xFFFFFFFF                  # GWL_STYLE
    if (style & 0x00C00000) == 0x00C00000 or style & 0x00040000:      # WS_CAPTION / WS_THICKFRAME
        return False
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    m = monitor_info(user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST)).rcMonitor
    prim = primary_monitor_rect()
    on_primary = (m.left, m.top, m.right, m.bottom) == (prim.left, prim.top, prim.right, prim.bottom)
    return on_primary and r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def foreground_looks_fullscreen():
    """Looser test: the active window has no title bar or sizing border (a fullscreen video/game), even if
    Windows has just squeezed it to the work area for a moment."""
    h = user32.GetForegroundWindow()
    if not h or window_pid(h) == os.getpid() or is_shell_surface(h) or is_overlay(h):
        return False
    style = user32.GetWindowLongW(h, -16) & 0xFFFFFFFF
    if (style & 0x00C00000) == 0x00C00000 or style & 0x00040000:
        return False
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    prim = primary_monitor_rect()
    return (r.right - r.left) >= (prim.right - prim.left) and (r.bottom - r.top) >= (prim.bottom - prim.top) * 0.85


def window_still_fullscreen(h):
    """Is window `h` (the one the bar hid for) still a fullscreen video/game on the primary monitor?"""
    if not h or not user32.IsWindow(h) or not user32.IsWindowVisible(h) or user32.IsIconic(h):
        return False
    style = user32.GetWindowLongW(h, -16) & 0xFFFFFFFF
    if (style & 0x00C00000) == 0x00C00000 or style & 0x00040000:      # got its title bar / border back
        return False
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    m = primary_monitor_rect()
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_acrylic(hwnd, rgba):
    """Windows 10/11 acrylic blur behind the window (used by the Glass theme)."""
    r, g, b, a = rgba
    accent = ACCENT_POLICY(4, 2, (a << 24) | (b << 16) | (g << 8) | r, 0)   # ACCENT_ENABLE_ACRYLICBLURBEHIND
    data = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))


def clear_acrylic(hwnd):
    """Switch the Glass blur off again (live style change away from Glass)."""
    accent = ACCENT_POLICY(0, 0, 0, 0)
    data = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))


def set_corner_round(hwnd, rounded):
    pref = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), 4)


def set_corner_default(hwnd):
    pref = ctypes.c_int(0)                                   # DWMWCP_DEFAULT
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), 4)


def recycle_bin_info():
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64NumItems), int(info.i64Size)


def recycle_bin_info_bg():
    """Same, for a background thread (the shell wants COM set up on the calling thread)."""
    ole32.CoInitializeEx(None, 2)                            # COINIT_APARTMENTTHREADED
    return recycle_bin_info()


def could_be_app_window(h):
    """Cheap pre-check for window events: could this window ever get a button on the bar?
    (Tooltips, menus, child controls and owned dialogs never do, and they fire most of the events.)"""
    if not h or user32.GetAncestor(h, 2) != h or not user32.IsWindowVisible(h) or user32.GetWindow(h, GW_OWNER):
        return False
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    return not ((ex & WS_EX_TOOLWINDOW or ex & WS_EX_NOACTIVATE) and not ex & WS_EX_APPWINDOW)


class BgCall(QThread):
    """Run one slow call (disk, shell, network) off the UI thread and hand the result back on it,
    so the bar never freezes while Windows is slow to answer."""
    done = Signal(object)
    _live = set()

    def __init__(self, fn, on_done=None):
        super().__init__()
        self.fn = fn
        if on_done is not None:
            self.done.connect(on_done)
        BgCall._live.add(self)
        self.finished.connect(lambda: BgCall._live.discard(self))
        self.start()

    def run(self):
        try:
            res = self.fn()
        except Exception as ex:
            print("background call failed:", ex)
            res = None
        self.done.emit(res)


def set_sheet(w, css):
    """setStyleSheet only when it really changes: every call re-styles the widget from scratch
    (slow, and it can make text jump for a frame)."""
    if getattr(w, "_sheet", None) != css:
        w._sheet = css
        w.setStyleSheet(css)


# --------------------------------------------------------------------------------------
# Native taskbar + AppBar (reserve screen space)
# --------------------------------------------------------------------------------------
class NativeTaskbar:
    def __init__(self):
        self.orig_state = None
        self.hidden = False

    @staticmethod
    def _hwnds():
        hs = []
        h = explorer_tray()
        if h:
            hs.append(h)
        h2 = None
        while True:
            h2 = user32.FindWindowExW(None, h2, "Shell_SecondaryTrayWnd", None)
            if not h2:
                break
            hs.append(h2)
        return hs

    @staticmethod
    def _abd():
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        abd.hWnd = explorer_tray()
        return abd

    def get_state(self):
        abd = self._abd()
        return int(appbar_message(ABM_GETSTATE, abd))

    def set_state(self, state):
        abd = self._abd()
        abd.lParam = state
        appbar_message(ABM_SETSTATE, abd)

    def hide(self):
        if self.orig_state is None:
            try:                                # a reload/handover (or a crash) left the original state here
                with open(STATE_PATH) as f:
                    self.orig_state = int(json.load(f)["orig_state"])
            except (OSError, ValueError, KeyError, TypeError):
                self.orig_state = self.get_state()
                try:
                    os.makedirs(CONFIG_DIR, exist_ok=True)
                    with open(STATE_PATH, "w") as f:
                        json.dump({"orig_state": self.orig_state}, f)
                except OSError:
                    pass
        self.set_state(ABS_AUTOHIDE)  # releases the work area the taskbar reserved
        for h in self._hwnds():
            user32.ShowWindow(h, SW_HIDE)
        self.hidden = True

    def enforce(self):
        if self.hidden:
            for h in self._hwnds():
                if user32.IsWindowVisible(h):
                    user32.ShowWindow(h, SW_HIDE)

    def show(self):
        state = self.orig_state
        if state is None:
            try:
                with open(STATE_PATH) as f:
                    state = json.load(f).get("orig_state", ABS_ALWAYSONTOP)
            except (OSError, ValueError):
                state = ABS_ALWAYSONTOP
        self.set_state(state)
        for h in self._hwnds():
            user32.ShowWindow(h, SW_SHOW)
        self.hidden = False
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass


class AppBar:
    """Registers the bar with the shell so maximized windows don't cover it."""

    def __init__(self, hwnd):
        self.abd = APPBARDATA()
        self.abd.cbSize = ctypes.sizeof(APPBARDATA)
        self.abd.hWnd = hwnd
        self.abd.uCallbackMessage = user32.RegisterWindowMessageW("OrbitBarAppBarMsg")
        self.registered = bool(appbar_message(ABM_NEW, self.abd))

    def place(self, edge_top, height_px):
        mon = primary_monitor_rect()
        a = self.abd
        a.uEdge = ABE_TOP if edge_top else ABE_BOTTOM
        a.rc.left, a.rc.right = mon.left, mon.right
        if edge_top:
            a.rc.top, a.rc.bottom = mon.top, mon.top + height_px
        else:
            a.rc.top, a.rc.bottom = mon.bottom - height_px, mon.bottom
        appbar_message(ABM_QUERYPOS, a)
        if edge_top:
            a.rc.bottom = a.rc.top + height_px
        else:
            a.rc.top = a.rc.bottom - height_px
        appbar_message(ABM_SETPOS, a)
        return a.rc

    def remove(self):
        if self.registered:
            appbar_message(ABM_REMOVE, self.abd)
            self.registered = False


# --------------------------------------------------------------------------------------
# Virtual desktops (read from registry, switch with Ctrl+Win+Arrows)
# --------------------------------------------------------------------------------------
VD_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"


def virtual_desktops():
    """Returns (names, current_index)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, VD_KEY) as k:
            ids, _ = winreg.QueryValueEx(k, "VirtualDesktopIDs")
            try:
                cur, _ = winreg.QueryValueEx(k, "CurrentVirtualDesktop")
            except FileNotFoundError:
                cur = None
    except OSError:
        return ["1"], 0
    if cur is None:  # Windows 10 keeps it per session
        try:
            sid = wt.DWORD()
            kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
            path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\SessionInfo\{sid.value}\VirtualDesktops"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                cur, _ = winreg.QueryValueEx(k, "CurrentVirtualDesktop")
        except OSError:
            cur = None
    guids = [bytes(ids[i:i + 16]) for i in range(0, len(ids or b""), 16)]
    if not guids:
        return ["1"], 0
    names = []
    for i, g in enumerate(guids):
        name = str(i + 1)
        try:
            import uuid
            gid = "{" + str(uuid.UUID(bytes_le=g)).upper() + "}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, VD_KEY + r"\Desktops" + "\\" + gid) as k:
                custom, _ = winreg.QueryValueEx(k, "Name")
                if custom:
                    name = custom
        except OSError:
            pass
        names.append(name)
    idx = guids.index(bytes(cur)) if cur and bytes(cur) in guids else 0
    return names, idx


def switch_desktop(target, current):
    """Ctrl+Win+Arrow once per step, spaced out by timers: the bar never sleeps while switching."""
    delta = target - current
    key = "right" if delta > 0 else "left"
    for n in range(abs(delta)):
        QTimer.singleShot(n * 70, lambda: send_keys("ctrl", "win", key))
    return abs(delta)


# --------------------------------------------------------------------------------------
# Volume (pycaw) and media (WinRT) back-ends
# --------------------------------------------------------------------------------------
# --- GPU: the same Windows performance counters Task Manager uses (works for NVIDIA, AMD, Intel)
pdh = ctypes.WinDLL("pdh")


class _PDH_VALUE_U(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong), ("AnsiStringValue", ctypes.c_char_p)]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("u", _PDH_VALUE_U)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", wt.LPWSTR), ("FmtValue", PDH_FMT_COUNTERVALUE)]


_sig(pdh.PdhOpenQueryW, ctypes.c_long, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wt.HANDLE))
_sig(pdh.PdhAddEnglishCounterW, ctypes.c_long, wt.HANDLE, wt.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wt.HANDLE))
_sig(pdh.PdhCollectQueryData, ctypes.c_long, wt.HANDLE)
_sig(pdh.PdhGetFormattedCounterArrayW, ctypes.c_long, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.DWORD),
     ctypes.POINTER(wt.DWORD), ctypes.c_void_p)
_sig(pdh.PdhCloseQuery, ctypes.c_long, wt.HANDLE)
PDH_FMT_DOUBLE, PDH_FMT_NOCAP100 = 0x200, 0x8000
PDH_MORE_DATA = 0x800007D2


def pdh_array(counter):
    """[(instance_name, value)] for a wildcard counter; skips instances without valid data yet."""
    for _ in range(3):   # instances can appear between the size query and the read
        size, count = wt.DWORD(0), wt.DWORD(0)
        r = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100,
                                             ctypes.byref(size), ctypes.byref(count), None)
        if (r & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
            return []
        buf = (ctypes.c_byte * size.value)()
        r = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100,
                                             ctypes.byref(size), ctypes.byref(count), buf)
        if (r & 0xFFFFFFFF) == PDH_MORE_DATA:
            continue
        if r != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W * count.value)).contents
        return [(it.szName or "", it.FmtValue.u.doubleValue) for it in items if it.FmtValue.CStatus in (0, 1)]
    return []


def gpu_adapter_info():
    """(name, dedicated VRAM bytes) of the GPU with the most memory, read from the display-driver registry."""
    best = ("GPU", 0)
    try:
        cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls) as root:
            for i in range(32):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = winreg.QueryValueEx(k, "DriverDesc")[0]
                        mem = 0
                        for val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                mem = winreg.QueryValueEx(k, val)[0]
                                break
                            except OSError:
                                continue
                        if isinstance(mem, (bytes, bytearray)):
                            mem = int.from_bytes(bytes(mem[:8]), "little")
                        if int(mem) > best[1]:
                            best = (name, int(mem))
                except OSError:
                    continue
    except Exception:
        pass
    return best


class GpuWorker(QThread):
    """Samples GPU load every 2 s off the UI thread.

    GPU % is computed like Task Manager: per engine type (3D, Copy, VideoDecode, Compute...)
    sum the utilisation of every process, then take the busiest engine type.
    """
    updated = Signal(object)

    def run(self):
        import re
        q = wt.HANDLE()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
            self.updated.emit(None)
            return
        eng, mem = wt.HANDLE(), wt.HANDLE()
        ok = pdh.PdhAddEnglishCounterW(q, r"\GPU Engine(*)\Utilization Percentage", 0, ctypes.byref(eng)) == 0
        has_mem = pdh.PdhAddEnglishCounterW(q, r"\GPU Adapter Memory(*)\Dedicated Usage", 0,
                                            ctypes.byref(mem)) == 0
        if not ok:
            pdh.PdhCloseQuery(q)
            self.updated.emit(None)      # no GPU counters (some VMs / RDP sessions)
            return
        name, total = gpu_adapter_info()
        pat = re.compile(r"pid_(\d+).*engtype_(.+)$")
        parsed = {}                 # counter instance name -> (pid, engine) - names repeat every sample
        pdh.PdhCollectQueryData(q)
        try:
            while not self.isInterruptionRequested():
                for _ in range(20):                       # sleep 2 s, but stop quickly on quit
                    if self.isInterruptionRequested():
                        return
                    self.msleep(100)
                if pdh.PdhCollectQueryData(q) != 0:
                    continue
                per_type, per_pid = {}, {}
                for inst, v in pdh_array(eng):
                    if v <= 0:
                        continue
                    hit = parsed.get(inst)
                    if hit is None:
                        m = pat.search(inst)
                        hit = (int(m.group(1)), m.group(2)) if m else False
                        if len(parsed) > 20000:
                            parsed.clear()
                        parsed[inst] = hit
                    if not hit:
                        continue
                    pid, et = hit
                    per_type[et] = per_type.get(et, 0.0) + v
                    d = per_pid.setdefault(pid, {})
                    d[et] = d.get(et, 0.0) + v
                util = min(100.0, max(per_type.values(), default=0.0))
                used = max((v for _, v in pdh_array(mem)), default=0.0) if has_mem else 0.0
                top = sorted(((pid, min(100.0, max(d.values()))) for pid, d in per_pid.items()),
                             key=lambda t: -t[1])[:8]
                busiest = max(per_type, key=per_type.get) if per_type else ""
                self.updated.emit({"util": util, "used": used, "total": total, "name": name,
                                   "top": top, "engine": busiest})
        finally:
            pdh.PdhCloseQuery(q)


class Volume:
    def __init__(self):
        self.ep = None
        self.loaded = False           # the audio library loads on first use, after the bar is on screen

    def ensure(self):
        if not self.loaded:
            self.loaded = True
            self.refresh_device()

    def refresh_device(self):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            dev = AudioUtilities.GetSpeakers()
            if hasattr(dev, "EndpointVolume"):        # pycaw >= 2024
                self.ep = dev.EndpointVolume
            else:                                     # older pycaw
                from comtypes import CLSCTX_ALL, POINTER, cast
                iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self.ep = cast(iface, POINTER(IAudioEndpointVolume))
        except Exception:
            self.ep = None

    def get(self):
        self.ensure()
        if not self.ep:
            return None, False
        try:
            return round(self.ep.GetMasterVolumeLevelScalar() * 100), bool(self.ep.GetMute())
        except Exception:
            return None, False

    def set(self, pct):
        self.ensure()
        if self.ep:
            self.ep.SetMasterVolumeLevelScalar(max(0, min(100, pct)) / 100.0, None)
            if pct > 0:
                self.ep.SetMute(0, None)
        else:
            send_keys("volup" if pct >= 50 else "voldown")

    def toggle_mute(self):
        self.ensure()
        if self.ep:
            self.ep.SetMute(0 if self.ep.GetMute() else 1, None)
        else:
            send_keys("mute")


PLAYING = 4          # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


def pretty_source(aumid):
    """'Brave.XYZ123' / 'Spotify.exe' / 'MSEdge' / 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> nice name."""
    a = (aumid or "").split("!")[0]
    a = a[:-4] if a.lower().endswith(".exe") else a
    known = {"msedge": "Edge", "chrome": "Chrome", "brave": "Brave", "firefox": "Firefox", "spotify": "Spotify",
             "opera": "Opera", "vivaldi": "Vivaldi", "zunemusic": "Media Player", "vlc": "VLC", "librewolf": "LibreWolf"}
    low = a.lower()
    for k, v in known.items():
        if k in low:
            return v
    return a.split(".")[0].split("_")[0] or "Media"


def choose_session(sessions, chosen, pinned=None, was_playing=None):
    """Pick which media session the bar shows/controls.

    sessions: list of dicts with 'key' and 'playing'. chosen: key used last time.
    pinned: key the user picked in the card (kept while it exists).
    was_playing: keys that were playing at the previous check.
    Rules: user's pick > whatever *just started* playing (newest wins) > the one already shown
    (playing or paused) > anything playing > first."""
    keys = [s["key"] for s in sessions]
    if not sessions:
        return None
    if pinned in keys:
        return pinned
    playing = [s["key"] for s in sessions if s["playing"]]
    if was_playing is not None:
        started = [k for k in playing if k not in was_playing and k != chosen]
        if started:
            return started[-1]
    if chosen in keys:                 # keep what's shown - even paused (browsers leave stale "playing" tabs)
        return chosen
    if playing:
        return playing[0]
    return keys[0]


class MediaWorker(QThread):
    """Reads every media session (each YouTube tab is its own session), picks the one that is
    really playing, and sends play/pause/next/previous straight to that session."""
    updated = Signal(object)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.queue = None
        self.chosen = None
        self.pinned = None
        self.was_playing = None
        self.thumb_key, self.thumb = None, None

    def send(self, cmd, arg=None):
        """Thread-safe: queue a command ('toggle', 'next', 'prev', 'select') for the worker."""
        if self.loop and self.queue:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (cmd, arg))
            return True
        return False

    @staticmethod
    def _seconds(td):
        try:
            return td.total_seconds()
        except Exception:
            try:
                return int(td) / 1e7
            except Exception:
                return 0.0

    async def _snapshot(self, s):
        p = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        ctl = info.controls
        status = int(info.playback_status)
        pos = dur = start = 0.0
        stamp = time.time()
        try:
            tl = s.get_timeline_properties()
            start = self._seconds(tl.start_time)
            pos = self._seconds(tl.position) - start
            dur = self._seconds(tl.end_time) - start
            upd = tl.last_updated_time
            if upd is not None and hasattr(upd, "timestamp"):
                stamp = upd.timestamp()
        except Exception:
            pass
        aumid = s.source_app_user_model_id or ""
        title = p.title or ""
        return {"session": s, "key": f"{aumid}|{title}", "aumid": aumid, "app": pretty_source(aumid),
                "title": title, "artist": p.artist or "", "album": p.album_title or "",
                "playing": status == PLAYING, "can_next": bool(ctl.is_next_enabled),
                "can_prev": bool(ctl.is_previous_enabled), "position": pos, "duration": max(0.0, dur),
                "can_seek": bool(getattr(ctl, "is_playback_position_enabled", False)), "start": start,
                "stamp": stamp, "props": p}

    async def _thumbnail(self, props):
        try:
            ref = props.thumbnail
            if ref is None:
                return None
            stream = await ref.open_read_async()
            size = int(stream.size)
            if not size or size > 8 * 1024 * 1024:
                return None
            Streams = None
            for mod in ("winrt.windows.storage.streams", "winsdk.windows.storage.streams"):
                try:
                    Streams = __import__(mod, fromlist=["Buffer"])
                    break
                except Exception:
                    continue
            if Streams is None:
                return None
            buf = Streams.Buffer(size)
            buf = await stream.read_async(buf, size, Streams.InputStreamOptions.READ_AHEAD)
            try:
                return bytes(memoryview(buf))
            except TypeError:
                reader = Streams.DataReader.from_buffer(buf)
                data = bytearray(buf.length)
                reader.read_bytes(data)
                return bytes(data)
        except Exception:
            return None

    def run(self):
        manager_cls = None
        for mod in ("winrt.windows.media.control", "winsdk.windows.media.control"):
            try:
                m = __import__(mod, fromlist=["GlobalSystemMediaTransportControlsSessionManager"])
                manager_cls = m.GlobalSystemMediaTransportControlsSessionManager
                break
            except Exception:
                continue
        if manager_cls is None:
            return
        import asyncio

        async def main():
            self.loop = asyncio.get_running_loop()
            self.queue = asyncio.Queue()
            mgr = await manager_cls.request_async()
            while not self.isInterruptionRequested():
                snaps = []
                try:
                    for s in list(mgr.get_sessions()):
                        try:
                            snaps.append(await self._snapshot(s))
                        except Exception:
                            continue
                except Exception:
                    pass
                self.chosen = choose_session(snaps, self.chosen, self.pinned, self.was_playing)
                self.was_playing = {x["key"] for x in snaps if x["playing"]}
                if self.pinned and self.pinned not in [x["key"] for x in snaps]:
                    self.pinned = None
                cur = next((x for x in snaps if x["key"] == self.chosen), None)
                if cur and cur["key"] != self.thumb_key:
                    self.thumb_key, self.thumb = cur["key"], await self._thumbnail(cur["props"])
                out = None
                if cur:
                    out = {k: v for k, v in cur.items() if k not in ("session", "props")}
                    out["stamp_local"] = time.time()
                    out["thumb"] = self.thumb
                    out["others"] = [{"key": x["key"], "app": x["app"], "title": x["title"], "playing": x["playing"]}
                                     for x in snaps]
                self.updated.emit(out)
                # wait up to 1 s, but wake up immediately for a button press
                try:
                    cmd, arg = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                target = cur["session"] if cur else None
                try:
                    if cmd == "select":
                        self.pinned = self.chosen = arg
                    elif cmd == "seek":
                        if target is not None:          # arg = seconds from the start of the track
                            ticks = int((cur.get("start", 0.0) + max(0.0, float(arg))) * 10_000_000)
                            if not await target.try_change_playback_position_async(ticks):
                                print("media seek: this player doesn't accept seeking")
                    elif cmd == "toggle" and target is not None:
                        self.pinned = None          # pressing play/pause on something un-pins a card pick
                        ok = await target.try_toggle_play_pause_async()
                        if not ok:
                            send_keys("playpause")
                    elif target is not None:
                        ok = await {"toggle": target.try_toggle_play_pause_async,
                                    "next": target.try_skip_next_async,
                                    "prev": target.try_skip_previous_async}[cmd]()
                        if not ok and cmd == "toggle":
                            send_keys("playpause")
                    else:
                        send_keys({"toggle": "playpause", "next": "next", "prev": "prev"}[cmd])
                except Exception as ex:
                    print("media command failed:", ex)
                await asyncio.sleep(0.15)              # let the app update its state before re-reading

        try:
            asyncio.run(main())
        except Exception as e:
            print("media worker stopped:", e)
        finally:
            self.loop = None


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        save_config(cfg)
    except (OSError, ValueError) as e:
        print("config error, using defaults:", e)
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def norm(path):
    return os.path.normcase(os.path.abspath(os.path.expandvars(path))) if path else ""


# --------------------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------------------
TEXT = T["text"]
DIM = T["dim"]


SIZES = {"w": 1.15, "c": 1.15}
SIZE_CHOICES = [("Normal", 1.0), ("Large (115%)", 1.15), ("Extra large (130%)", 1.3), ("Huge (150%)", 1.5)]


def apply_sizes(cfg):
    for key, name in (("w", "widget_size"), ("c", "card_size")):
        try:
            SIZES[key] = min(2.0, max(0.8, float(cfg.get(name, 1.15))))
        except (TypeError, ValueError):
            SIZES[key] = 1.15


def uw(n):
    """A size on the bar, scaled by the Bar widgets setting."""
    return int(round(n * SIZES["w"]))


def uc(n):
    """A size in cards and notifications, scaled by the Cards & notifications setting."""
    return int(round(n * SIZES["c"]))


def apply_theme(cfg):
    global TEXT, DIM
    T.clear()
    T.update(THEMES.get(cfg.get("theme", "midnight"), THEMES["midnight"]))
    if cfg.get("theme") == "custom" and cfg.get("background"):
        T["bg"] = tuple(cfg["background"])
    TEXT, DIM = T["text"], T["dim"]


def rgba(c):
    return f"rgba({c[0]},{c[1]},{c[2]},{c[3]})"


def lbl(text="", cls=None, parent=None):
    w = QLabel(text, parent)
    if cls:
        w.setProperty("class", cls)
    return w
_icon_provider = None
_icon_cache = {}


def exe_icon(path, hwnd=None):
    global _icon_provider
    key = norm(path)
    if key in _icon_cache:
        return _icon_cache[key]
    pm = None
    if path and os.path.exists(os.path.expandvars(path)):
        if _icon_provider is None:
            _icon_provider = QFileIconProvider()
        icon = _icon_provider.icon(QFileInfo(os.path.expandvars(path)))
        if not icon.isNull():
            pm = icon.pixmap(QSize(64, 64))          # crisp even when icons are shown large
    if (pm is None or pm.isNull()) and hwnd:
        pm = window_icon(hwnd)
    if key and pm is not None and not pm.isNull():
        _icon_cache[key] = pm
    return pm


def glyph_button(glyph, tip="", size=14):
    b = QPushButton(glyph)
    b.setProperty("class", "glyph")
    b.setFont(QFont(GLYPH_FONT.split(",")[0], size - 4))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    return b


class Pill(QFrame):
    """Rounded group container."""

    def __init__(self, spacing=2):
        super().__init__()
        self.setObjectName("pill")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 1, 6, 1)
        self.lay.setSpacing(spacing)


class StartButton(QWidget):
    def __init__(self, accent, bar=None):
        super().__init__()
        self.bar = bar
        self.accent = QColor(accent)
        self.hover = False
        self.setFixedSize(uw(36), 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Start  (right-click: power menu)")

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            send_keys("win")

    def contextMenuEvent(self, e):
        e.accept()
        send_keys("win", "x")

    def rescale(self):
        self.setFixedSize(uw(36), 36)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.bar is not None:
            self.accent = QColor(self.bar.cfg["accent"])
        if self.hover:
            p.setBrush(QColor(*T["hover"]))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(self.rect(), 10, 10)
        c = self.rect().center()
        k = SIZES["w"]
        p.setPen(QPen(self.accent, 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(c.x() - 10 * k, c.y() - 6 * k, 20 * k, 12 * k))      # orbit
        p.setPen(Qt.NoPen)
        p.setBrush(self.accent)
        p.drawEllipse(QRectF(c.x() - 4.5 * k, c.y() - 4.5 * k, 9 * k, 9 * k))      # planet
        p.setBrush(QColor(TEXT))
        p.drawEllipse(QRectF(c.x() + 7, c.y() - 7, 4, 4))          # moon


class AppButton(QWidget):
    def __init__(self, bar, key, path, pinned):
        super().__init__()
        self.bar, self.key, self.path, self.pinned = bar, key, path, pinned
        self.hwnds, self.active, self.hover = [], False, False
        self.pix = None
        self.press_pos = None
        self.dragged = False
        self.flash_t0 = None          # set while the app asks for attention (new message, finished download…)
        self._tip = None
        self._icon_retry = 0.0
        self.icon_px = int(bar.cfg.get("icon_size", 24))
        self.setFixedSize(self.icon_px + 16, self.icon_px + 12)
        self.setCursor(Qt.PointingHandCursor)

    def set_icon_size(self, px):
        self.icon_px = int(px)
        self.setFixedSize(self.icon_px + 16, self.icon_px + 12)
        self.update()

    # drag an icon to reorder pinned apps (dropping a running app among them pins it)
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press_pos = e.position().toPoint()
            self.dragged = False

    def mouseMoveEvent(self, e):
        if self.press_pos is None or not (e.buttons() & Qt.LeftButton) or not self.path:
            return
        if (e.position().toPoint() - self.press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self.press_pos = None
        self.dragged = True
        if self.bar.previews:
            self.bar.previews.close_now()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(APP_MIME, self.key.encode("utf-8"))
        drag.setMimeData(mime)
        if self.pix:
            drag.setPixmap(self.pix.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            drag.setHotSpot(QPoint(14, 14))
        drag.exec(Qt.MoveAction)

    def set_state(self, hwnds, active):
        changed = (hwnds != self.hwnds) or (active != self.active)
        self.hwnds, self.active = hwnds, active
        if self.pix is None and time.monotonic() >= self._icon_retry:
            self.pix = exe_icon(self.path, hwnds[0] if hwnds else None)
            if self.pix is None:          # no icon yet (some apps set it late): ask again in 5 s, not every refresh
                self._icon_retry = time.monotonic() + 5
            else:
                changed = True
        self.update_tip()
        if changed:
            self.update()

    def update_tip(self):
        name = os.path.splitext(os.path.basename(self.path))[0] if self.path else "App"
        titles = [window_text(h) for h in self.hwnds[:6]]
        tip = "\n".join(titles) if titles else name
        if tip != self._tip:
            self._tip = tip
            self.setToolTip(tip)

    def enterEvent(self, e):
        self.hover = True
        self.update()
        if self.hwnds and self.bar.previews:
            self.bar.previews.hover(self)

    def leaveEvent(self, e):
        self.hover = False
        self.update()
        if self.bar.previews:
            self.bar.previews.leave()

    def launch(self):
        if self.path:
            try:
                os.startfile(os.path.expandvars(self.path))
            except OSError as ex:
                print("launch failed:", ex)

    def mouseReleaseEvent(self, e):
        if self.bar.previews:
            self.bar.previews.close_now()
        if self.dragged:                 # the press turned into a drag, not a click
            self.dragged = False
            return
        if e.button() == Qt.LeftButton:
            if not self.hwnds:
                self.launch()
            else:
                fg = user32.GetForegroundWindow()
                target = None
                if len(self.hwnds) == 1:
                    h = self.hwnds[0]
                    if fg == h and not user32.IsIconic(h):
                        minimize_window(h)
                    else:
                        target = h
                elif fg in self.hwnds:   # cycle through this app's windows
                    i = self.hwnds.index(fg)
                    target = self.hwnds[(i + 1) % len(self.hwnds)]
                else:
                    target = self.hwnds[0]
                if target:
                    activate_window(target)
                self.bar.apps.set_foreground(target)       # highlight now; Windows confirms a moment later
            QTimer.singleShot(150, self.bar.apps.refresh)
        elif e.button() == Qt.MiddleButton:
            self.launch()

    def contextMenuEvent(self, e):
        e.accept()
        if self.bar.previews:
            self.bar.previews.close_now()
        self.context_menu(e.globalPos())

    def context_menu(self, pos):
        m = QMenu(self)
        for h in self.hwnds:
            t = window_text(h)
            a = m.addAction((t[:60] + "…") if len(t) > 60 else t)
            a.triggered.connect(lambda _=False, h=h: activate_window(h))
        if self.hwnds:
            m.addSeparator()
        if self.path:
            m.addAction("New window").triggered.connect(self.launch)
            if self.pinned:
                m.addAction("Unpin from bar").triggered.connect(lambda: self.bar.set_pinned(self.path, False))
            else:
                m.addAction("Pin to bar").triggered.connect(lambda: self.bar.set_pinned(self.path, True))
        if self.hwnds:
            label = "Close window" if len(self.hwnds) == 1 else f"Close all {len(self.hwnds)} windows"
            m.addAction(label).triggered.connect(lambda: [close_window(h) for h in self.hwnds])
        m.exec(self.bar.popup_pos(m.sizeHint(), pos))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.rect().adjusted(2, 2, -2, -2)
        if self.active or self.hover:
            p.setPen(Qt.NoPen)
            c = QColor(*T["hover"])
            c.setAlpha(min(255, c.alpha() + (14 if self.active else 0)))
            p.setBrush(c)
            p.drawRoundedRect(r, 10, 10)
        if self.flash_t0 is not None:
            # pulses for a few seconds, then stays lit until you open the app (like the Windows taskbar)
            import math
            t = time.monotonic() - self.flash_t0
            k = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 1.1)) if t < AppsArea.PULSE_SECS else 0.75
            glow = QColor(FLASH_COLOR)
            glow.setAlphaF(0.55 * k)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(r, 10, 10)
        if self.pix:
            s = self.icon_px
            p.drawPixmap(QRectF((self.width() - s) / 2, (self.height() - s) / 2 - 1, s, s), self.pix,
                         QRectF(self.pix.rect()))
        else:
            p.setPen(QColor(TEXT))
            p.drawText(self.rect(), Qt.AlignCenter, "?")
        n = len(self.hwnds)
        if n:   # running indicator: one pill when active, dots otherwise
            accent = QColor(FLASH_COLOR) if self.flash_t0 is not None else QColor(self.bar.cfg["accent"])
            p.setPen(Qt.NoPen)
            y = self.height() - 4
            if self.active or self.flash_t0 is not None:
                p.setBrush(accent)
                p.drawRoundedRect(QRectF(self.width() / 2 - 8, y, 16, 3), 1.5, 1.5)
            else:
                p.setBrush(QColor(DIM))
                dots = min(n, 3)
                x0 = self.width() / 2 - (dots * 6 - 2) / 2
                for i in range(dots):
                    p.drawEllipse(QRectF(x0 + i * 6, y, 3.5, 3.5))


APP_MIME = "application/x-orbitbar-app"
FLASH_COLOR = "#f59e0b"


class AppsArea(Pill):
    PULSE_SECS = 8

    def __init__(self, bar):
        super().__init__(spacing=0)
        self.bar = bar
        self.pulse = QTimer(self, interval=50, timeout=self.tick_flash)
        self._pending_flash = None
        self.buttons = OrderedDict()
        self.order = []
        self.drop_x = None
        self.opened = []          # unpinned running apps, in the order they first appeared
        self.known = set()        # every window that has a button (window events check this first)
        self._pin_cache = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(APP_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(APP_MIME):
            self.drop_x = self.slot_x(e.position().x())
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop_x = None
        self.update()

    def slot_x(self, x):
        """Snap the drop marker to the gap between icons."""
        edges = [b.geometry().left() for b in self.buttons.values()]
        if self.buttons:
            edges.append(list(self.buttons.values())[-1].geometry().right() + 1)
        return min(edges, key=lambda ex: abs(ex - x)) if edges else x

    def dropEvent(self, e):
        key = bytes(e.mimeData().data(APP_MIME)).decode("utf-8")
        x = e.position().x()
        self.drop_x = None
        self.update()
        src = self.buttons.get(key)
        if src is None:
            return
        others = [b for b in self.buttons.values() if b is not src]
        idx = sum(1 for b in others if b.geometry().center().x() < x)
        others.insert(idx, src)
        pins = [b.path for b in others if (b.pinned or b is src) and b.path]
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.bar.set_pins(pins))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.drop_x is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.bar.cfg["accent"]))
            p.drawRoundedRect(QRectF(self.drop_x - 1.5, 6, 3, self.height() - 12), 1.5, 1.5)

    def pinned_list(self):
        """Pinned apps that exist on disk. The disk check is cached: a pin on a network drive or a sleeping
        disk could otherwise stall every refresh."""
        pins = tuple(self.bar.cfg.get("pinned", []))
        now = time.monotonic()
        c = self._pin_cache
        if c is None or c[0] != pins or now - c[1] > 30:
            ok = [(norm(p), p) for p in pins if p.lower().startswith("shell:") or os.path.exists(os.path.expandvars(p))]
            self._pin_cache = c = (pins, now, ok)
        return c[2]

    def set_foreground(self, h):
        """Move the 'active' highlight without rescanning windows (instant feedback on focus changes)."""
        for b in self.buttons.values():
            on = bool(h) and h in b.hwnds
            if on and b.flash_t0 is not None:          # you opened it: stop asking for attention
                b.flash_t0 = None
                b.update()
            if on != b.active:
                b.active = on
                b.update()

    # ---- apps asking for attention (Windows' FlashWindow: a new message, a finished download…)
    def flash(self, h):
        if not h or h == user32.GetForegroundWindow():
            return
        for b in self.buttons.values():
            if h in b.hwnds:
                if b.flash_t0 is None:
                    b.flash_t0 = time.monotonic()
                    b.update()
                    if not self.pulse.isActive():
                        self.pulse.start()
                return
        self._pending_flash = h                     # a window the bar hasn't listed yet
        self.bar.schedule_refresh(30)

    def tick_flash(self):
        now, live = time.monotonic(), False
        for b in self.buttons.values():
            if b.flash_t0 is not None:
                b.update()
                live = live or now - b.flash_t0 < self.PULSE_SECS + 0.2
        if not live:
            self.pulse.stop()                       # steady glow from here: no more redraws needed

    def refresh_titles(self):
        for b in self.buttons.values():
            b.update_tip()

    def refresh(self):
        pinned = self.pinned_list()
        groups = OrderedDict((k, []) for k, _ in pinned)
        paths = dict(pinned)
        for h in list_app_windows():
            exe = window_exe(h)
            k = norm(exe) or f"hwnd:{h}"
            groups.setdefault(k, []).append(h)
            paths.setdefault(k, exe)
        pinned_keys = {k for k, _ in pinned}
        fg = user32.GetForegroundWindow()
        # Unpinned apps keep the order they were opened in (like the Windows taskbar). Windows lists
        # windows most-recently-used first, so without this, clicking an app moved it to the front.
        for k in groups:
            if k not in pinned_keys and k not in self.opened:
                self.opened.append(k)
        self.opened = [k for k in self.opened if k in groups and k not in pinned_keys]
        order = [k for k, _ in pinned] + self.opened
        groups = OrderedDict((k, groups[k]) for k in order)
        if order != self.order:
            # Only the apps that opened or closed get a button made or removed; the rest are kept and just
            # re-ordered. (Rebuilding every button made all icons blink whenever any app opened or closed.)
            self.setUpdatesEnabled(False)
            try:
                old, self.buttons = self.buttons, OrderedDict()
                for k in order:
                    b = old.pop(k, None)
                    if b is None:
                        b = AppButton(self.bar, k, paths.get(k, ""), k in pinned_keys)
                    self.buttons[k] = b
                prev = self.bar.previews
                for b in old.values():                    # apps that closed
                    if prev and prev.target is b:
                        prev.close_now()
                        prev.target = None
                    self.lay.removeWidget(b)
                    b.hide()
                    b.setParent(None)
                    b.deleteLater()
                for i, b in enumerate(self.buttons.values()):
                    if self.lay.indexOf(b) != i:
                        self.lay.removeWidget(b)
                        self.lay.insertWidget(i, b)
                    if b.isHidden():
                        b.show()
            finally:
                self.setUpdatesEnabled(True)
            self.order = order
        known = set()
        for k, hw in groups.items():
            b = self.buttons[k]
            b.pinned = k in pinned_keys
            b.set_state(hw, fg in hw)
            if b.flash_t0 is not None and (fg in hw or not hw):
                b.flash_t0 = None
                b.update()
            known.update(hw)
        self.known = known
        if self._pending_flash in known:
            h, self._pending_flash = self._pending_flash, None
            self.flash(h)


class DesktopButton(QPushButton):
    """A desktop pill: click = switch, middle-click = close, right-click = menu."""

    def __init__(self, ws, index, text):
        super().__init__(text)
        self.ws, self.index = ws, index

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.ws.close_desktop(self.index)
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        e.accept()
        self.ws.menu(self.index, e.globalPos())


class Workspaces(Pill):
    def __init__(self, bar):
        super().__init__(spacing=2)
        self.bar = bar
        self.state = None
        self.shown = None             # desktop drawn as active (can run ahead of Windows after a click)
        self.btns = []
        self.add = glyph_button(G["add"], "New desktop (Ctrl+Win+D)", 12)
        self.add.clicked.connect(lambda: send_keys("ctrl", "win", "d"))
        self.lay.addWidget(self.add)

    def refresh(self):
        names, cur = virtual_desktops()
        pend = getattr(self, "_pending", None)       # (target, old desktop, until) after a click
        if pend:
            if cur == pend[1] and time.monotonic() < pend[2] and names == (self.state or ([],))[0]:
                return                               # Windows hasn't recorded the switch yet: keep the new highlight
            self._pending = None
        if (names, cur) == self.state and self.shown == cur:
            return
        if not self.state or names != self.state[0]:
            self.rebuild(names)
        self.state = (names, cur)
        self.highlight(cur)

    def rebuild(self, names):
        """Desktops added, removed or renamed: reuse the existing pills, change only the difference."""
        self.setUpdatesEnabled(False)
        try:
            while len(self.btns) > len(names):
                b = self.btns.pop()
                self.lay.removeWidget(b)
                b.hide()
                b.setParent(None)
                b.deleteLater()
            for i, name in enumerate(names):
                text = name if len(name) <= 10 else name[:9] + "…"
                if i < len(self.btns):
                    b = self.btns[i]
                    b.setText(text)
                else:
                    b = DesktopButton(self, i, text)
                    b.setProperty("class", "ws")
                    b.setCursor(Qt.PointingHandCursor)
                    b.setFocusPolicy(Qt.NoFocus)
                    b.clicked.connect(lambda _=False, i=i: self.go(i))
                    self.lay.insertWidget(i, b)
                    self.btns.append(b)
                    b.show()
                b.setToolTip(f"Desktop {i + 1}: {name}\nClick: switch · Middle-click: close · Right-click: menu")
        finally:
            self.setUpdatesEnabled(True)
        self.shown = None

    def highlight(self, cur):
        self.shown = cur
        for i, b in enumerate(self.btns):
            cls = "ws-active" if i == cur else "ws"
            if b.property("class") != cls:
                b.setProperty("class", cls)
                b.style().unpolish(b)
                b.style().polish(b)
                b.update()

    def go(self, i):
        if self.state:
            old = self.shown if self.shown is not None else self.state[1]
            if i == old:
                return
            steps = switch_desktop(i, old)
            self._pending = (i, self.state[1], time.monotonic() + steps * 0.07 + 1.5)
            self.highlight(i)                         # light up the target right away
            QTimer.singleShot(steps * 70 + 200, self.refresh)
            QTimer.singleShot(steps * 70 + 700, self.refresh)    # Windows can be slow to record the switch

    def close_desktop(self, i):
        """Windows can only close the *current* desktop (Ctrl+Win+F4), so switch to it first.
        Its windows move to the neighbouring desktop - nothing gets closed."""
        if not self.state or len(self.state[0]) <= 1:
            return
        cur = self.state[1]
        steps = switch_desktop(i, cur) if i != cur else 0
        QTimer.singleShot(steps * 70 + 150, lambda: send_keys("ctrl", "win", "f4"))
        QTimer.singleShot(steps * 70 + 550, self.refresh)

    def menu(self, i, pos):
        m = QMenu(self)
        if self.state and i != self.state[1]:
            m.addAction(f"Switch to desktop {i + 1}").triggered.connect(lambda: self.go(i))
        close = m.addAction(f"Close desktop {i + 1}  (its windows move over)")
        close.setEnabled(bool(self.state and len(self.state[0]) > 1))
        close.triggered.connect(lambda: self.close_desktop(i))
        m.addSeparator()
        m.addAction("New desktop").triggered.connect(lambda: (send_keys("ctrl", "win", "d"),
                                                               QTimer.singleShot(300, self.refresh)))
        m.addAction("Task View (rename / rearrange)").triggered.connect(lambda: send_keys("win", "tab"))
        m.exec(self.bar.popup_pos(m.sizeHint(), pos))

    def wheelEvent(self, e):
        send_keys("ctrl", "win", "left" if e.angleDelta().y() > 0 else "right")
        QTimer.singleShot(250, self.refresh)


class Sparkline(QWidget):
    def __init__(self, accent, n=30):
        super().__init__()
        self.vals = deque([0.0] * n, maxlen=n)
        self.accent = QColor(accent)
        self.setFixedSize(38, 22)

    def push(self, v):
        self.vals.append(v)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, n = self.width(), self.height(), len(self.vals)
        path = QPainterPath()
        for i, v in enumerate(self.vals):
            x = i * (w - 1) / (n - 1)
            y = h - 2 - (h - 4) * min(v, 100) / 100
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        fill = QPainterPath(path)
        fill.lineTo(w - 1, h)
        fill.lineTo(0, h)
        fill.closeSubpath()
        c = QColor(self.accent)
        c.setAlpha(50)
        p.fillPath(fill, c)
        p.setPen(QPen(self.accent, 1.4))
        p.drawPath(path)


def fmt_rate(bps):
    for unit in ("B", "K", "M", "G"):
        if bps < 1024 or unit == "G":
            return f"{bps:.0f}{unit}" if unit in ("B", "K") else f"{bps:.1f}{unit}"
        bps /= 1024


def fmt_clock(secs):
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


class SeekSlider(QSlider):
    """The song's progress bar, but you can grab it: click anywhere to jump there, drag to scrub,
    scroll the mouse wheel to go 5 s back/forward. Values are tenths of a second."""
    seek = Signal(float)                 # seconds from the start of the track
    scrubbing = Signal(float)            # while dragging: where the handle is (for the time label)

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 0)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.sliderMoved.connect(lambda v: self.scrubbing.emit(v / 10))
        self.sliderReleased.connect(lambda: self.seek.emit(self.value() / 10))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.maximum() > 0:
            # jump straight to the click (a plain slider only steps towards it), then keep dragging from there
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(v)
            self.scrubbing.emit(v / 10)
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        if self.maximum() <= 0:
            return
        step = 50 if e.angleDelta().y() < 0 else -50      # wheel down = forward, like the calendar
        v = max(0, min(self.maximum(), self.value() + step))
        self.setValue(v)
        self.seek.emit(v / 10)
        e.accept()


class MediaCard(QWidget):
    """Hover card: cover art, title, artist, app, live progress, controls, other media sessions."""

    def __init__(self, bar, media):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.bar, self.media = bar, media
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(uc(340))
        self._card_box = box
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.art = QLabel()
        self.art.setFixedSize(uc(84), uc(84))
        self.art.setAlignment(Qt.AlignCenter)
        top.addWidget(self.art)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.app = lbl("", "dim")
        self.title = lbl("", "h2")
        self.title.setWordWrap(True)
        self.artist = lbl("", "dim")
        self.artist.setWordWrap(True)
        info.addWidget(self.app)
        info.addWidget(self.title)
        info.addWidget(self.artist)
        info.addStretch(1)
        top.addLayout(info, 1)
        v.addLayout(top)
        self.progress = SeekSlider()
        self.progress.seek.connect(self.seek_to)
        self.progress.scrubbing.connect(lambda secs: self.t_pos.setText(fmt_clock(secs)))
        v.addWidget(self.progress)
        times = QHBoxLayout()
        self.t_pos, self.t_dur = lbl("", "dim"), lbl("", "dim")
        times.addWidget(self.t_pos)
        times.addStretch(1)
        times.addWidget(self.t_dur)
        v.addLayout(times)
        ctl = QHBoxLayout()
        ctl.addStretch(1)
        self.b_restart = glyph_button("\uE72C", "Play from the beginning", 15)
        self.b_back = text_button("−10", "seek", "Back 10 seconds")
        self.b_prev = glyph_button(G["prev"], "Previous", 15)
        self.b_play = glyph_button(G["play"], "Play / pause", 20)
        self.b_next = glyph_button(G["next"], "Next", 15)
        self.b_fwd = text_button("+10", "seek", "Forward 10 seconds")
        self.b_restart.clicked.connect(lambda: self.seek_to(0))
        self.b_back.clicked.connect(lambda: self.seek_by(-10))
        self.b_fwd.clicked.connect(lambda: self.seek_by(10))
        self.b_prev.clicked.connect(lambda: media.command("prev"))
        self.b_play.clicked.connect(lambda: media.command("toggle"))
        self.b_next.clicked.connect(lambda: media.command("next"))
        self.seek_buttons = (self.b_restart, self.b_back, self.b_fwd)
        for b in (self.b_restart, self.b_back, self.b_prev, self.b_play, self.b_next, self.b_fwd):
            ctl.addWidget(b)
        ctl.addStretch(1)
        v.addLayout(ctl)
        self.others_head = section("Also playing")
        v.addWidget(self.others_head)
        self.others = QVBoxLayout()
        self.others.setSpacing(2)
        v.addLayout(self.others)
        bar.restyle(self)
        self.setStyleSheet(self.styleSheet() + """
            QPushButton[class="glyph"] { padding: 8px 12px; border-radius: 18px; }
            QPushButton[class="session"] { text-align: left; padding: 5px 8px; }
        """)
        self.tick = QTimer(self, interval=500, timeout=self.update_progress)
        self.show_timer = QTimer(self, singleShot=True, interval=350, timeout=self.open)
        self.hide_timer = QTimer(self, singleShot=True, interval=350, timeout=self.close_now)
        self._others_sig = None
        self._seek_hold = None           # (key, seconds, time) right after a seek, until the player catches up

    # hover plumbing (same pattern as window previews)
    def hover(self):
        self.hide_timer.stop()
        if not self.isVisible() and self.media.data:
            self.show_timer.start()

    def leave(self):
        self.show_timer.stop()
        self.hide_timer.start()

    def enterEvent(self, e):
        self.hide_timer.stop()

    def leaveEvent(self, e):
        self.hide_timer.start()

    def open(self):
        if not self.media.data:
            return
        self.refresh()
        self.adjustSize()
        anchor = self.media.mapToGlobal(QPoint(self.media.width() // 2, 0))
        animate_in(self, self.bar.popup_pos(self.size(), anchor, center=True), self.bar)
        self.tick.start()

    def close_now(self):
        self.show_timer.stop()
        self.hide_timer.stop()
        self.tick.stop()
        self.hide()

    def refresh(self):
        d = self.media.data
        if not d:
            self.close_now()
            return
        self.app.setText(("▶ " if d["playing"] else "❚❚ ") + d["app"])
        self.title.setText(d["title"] or "Unknown title")
        self.artist.setText(" · ".join(x for x in (d["artist"], d["album"]) if x))
        self.artist.setVisible(bool(d["artist"] or d["album"]))
        self.b_play.setText(G["pause"] if d["playing"] else G["play"])
        self.b_next.setEnabled(d["can_next"])
        self.b_prev.setEnabled(d["can_prev"])
        self.b_next.setToolTip("Next" if d["can_next"] else "This player has no next track")
        self.b_prev.setToolTip("Previous" if d["can_prev"] else "This player has no previous track")
        can_seek = d.get("can_seek", False) and d["duration"] > 0
        self.progress.setEnabled(can_seek)
        self.progress.setToolTip("Click or drag to jump · scroll for 5 s steps" if can_seek
                                 else "This player doesn't allow jumping around")
        for b in self.seek_buttons:
            b.setEnabled(can_seek)
        pm = self.media.art_pixmap(84)
        if pm is not None:
            self.art.setPixmap(pm)
            self.art.setStyleSheet("")
        else:
            self.art.setPixmap(QPixmap())
            self.art.setText("♪")
            self.art.setStyleSheet(f"background: {rgba(T['pill'])}; border-radius: 12px; font-size: 30px; color: {DIM};")
        others = [o for o in d["others"] if o["key"] != d["key"]]
        sig = tuple((o["key"], o["playing"]) for o in others)
        if sig != self._others_sig:
            self._others_sig = sig
            clear_layout(self.others)
            for o in others[:5]:
                b = text_button(("▶ " if o["playing"] else "❚❚ ") + f"{o['app']} · {o['title'][:40] or 'Untitled'}", "session",
                                "Control this one from the bar")
                b.setStyleSheet("text-align: left; padding: 5px 8px;")
                b.clicked.connect(lambda _=False, k=o["key"]: self.media.command("select", k))
                self.others.addWidget(b)
            self.others_head.setVisible(bool(others))
            if self.isVisible():
                self.adjustSize()
        self.update_progress()

    def current_pos(self):
        d = self.media.data
        if not d:
            return 0.0
        dur = d["duration"]
        pos = d["position"] + ((time.time() - d["stamp"]) if d["playing"] else 0)
        hold = self._seek_hold
        if hold:
            key, target, t0 = hold
            moved = target + ((time.time() - t0) if d["playing"] else 0)
            if key != d["key"] or time.time() - t0 > 4 or abs(pos - moved) < 2:
                self._seek_hold = None          # the player has caught up (or the song changed)
            else:
                pos = moved                     # players report the new spot a moment late: don't jump back
        return max(0.0, min(pos, dur) if dur else pos)

    def seek_to(self, secs):
        d = self.media.data
        if not d or not d.get("can_seek") or d["duration"] <= 0:
            return
        secs = max(0.0, min(float(secs), d["duration"] - 1))
        self._seek_hold = (d["key"], secs, time.time())
        self.media.command("seek", secs)
        self.update_progress()

    def seek_by(self, delta):
        self.seek_to(self.current_pos() + delta)

    def update_progress(self):
        d = self.media.data
        if not d:
            return
        dur = d["duration"]
        live = dur <= 0
        self.progress.setVisible(not live)
        self.t_dur.setText("" if live else fmt_clock(dur))
        if live:
            self.t_pos.setText("Live / no timeline")
            return
        if self.progress.isSliderDown():        # the user is dragging: leave the handle and label alone
            return
        pos = self.current_pos()
        self.t_pos.setText(fmt_clock(pos))
        if self.progress.maximum() != int(dur * 10):
            self.progress.setRange(0, int(dur * 10))
        self.progress.setValue(int(pos * 10))


class SystemSource(QObject):
    """One place that samples CPU / RAM / network (1 s) and GPU (2 s, background) for all the
    separate system widgets, so adding more widgets doesn't add more measuring."""
    updated = Signal()

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.cpu, self.ram, self.ram_used, self.ram_total = 0.0, 0.0, 0, 1
        self.down = self.up = 0.0
        self.cpu_hist = deque([0.0] * 30, maxlen=30)
        self.gpu = None
        self.gpu_worker = None
        self.last_net = psutil.net_io_counters()
        self.last_t = time.time()
        psutil.cpu_percent(None)

    def want_gpu(self):
        if self.gpu_worker is None:
            self.gpu_worker = GpuWorker()
            self.gpu_worker.updated.connect(self.on_gpu)
            self.gpu_worker.start()

    def on_gpu(self, d):
        self.gpu = d
        self.bar.gpu_data = d
        self.updated.emit()

    def refresh(self):
        self.cpu = psutil.cpu_percent(None)
        self.cpu_hist.append(self.cpu)
        vm = psutil.virtual_memory()
        self.ram, self.ram_used, self.ram_total = vm.percent, vm.used, vm.total
        now, n = time.time(), psutil.net_io_counters()
        dt = max(now - self.last_t, 0.001)
        self.down = (n.bytes_recv - self.last_net.bytes_recv) / dt
        self.up = (n.bytes_sent - self.last_net.bytes_sent) / dt
        self.last_net, self.last_t = n, now
        self.updated.emit()

    def stop(self):
        if self.gpu_worker:
            self.gpu_worker.requestInterruption()
            self.gpu_worker.wait(1500)


class MetricWidget(QWidget):
    """CPU / RAM / GPU / network as separate widgets. Styles: number, graph (sparkline), ring."""
    LABELS = {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "network": "NET"}

    def __init__(self, bar, kind, variant="number"):
        super().__init__()
        self.bar, self.kind = bar, kind
        self.src = bar.sys_source
        if kind == "gpu":
            self.src.want_gpu()
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.set_variant(variant)
        self.src.updated.connect(self.update_tip)

    def set_variant(self, v):
        if self.kind == "network":
            v = "number"
        allowed = ("graph", "number", "ring") if self.kind == "cpu" else ("number", "ring")
        self.variant = v if v in allowed else allowed[0]
        fm = QFontMetricsF(self.text_font())
        if self.kind == "network":
            w = fm.horizontalAdvance("↓999.9M ↑999.9M") + 4
        elif self.variant == "ring":
            w = uw(28) + fm.horizontalAdvance(" 100%") + 4
        else:
            w = fm.horizontalAdvance(f"{self.LABELS[self.kind]} 100%") + 6 + (uw(42) if self.variant == "graph" else 0)
        self.setFixedWidth(int(w))
        self.update()

    def text_font(self):
        f = QFont("Segoe UI Variable Text, Segoe UI")
        f.setPixelSize(uw(12))
        return f

    def rescale(self):
        self.set_variant(self.variant)

    def value(self):
        s = self.src
        if self.kind == "cpu":
            return s.cpu
        if self.kind == "ram":
            return s.ram
        if self.kind == "gpu":
            return s.gpu["util"] if s.gpu else None
        return None

    def update_tip(self):
        s = self.src
        tips = {"cpu": f"CPU {s.cpu:.0f}%",
                "ram": f"RAM {s.ram_used / 2**30:.1f} / {s.ram_total / 2**30:.1f} GB",
                "network": f"Download {fmt_rate(s.down)}/s · Upload {fmt_rate(s.up)}/s"}
        if self.kind == "gpu":
            g = s.gpu
            tip = (g["name"] + (f"\nVRAM {g['used'] / 2**30:.1f} / {g['total'] / 2**30:.1f} GB" if g["total"] else "")
                   if g else "GPU: measuring…")
        else:
            tip = tips[self.kind]
        self.setToolTip(tip + "\nClick: top apps, memory & disks")
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.bar.open_system_card(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.text_font())
        h = self.height()
        s = self.src
        if self.kind == "network":
            p.setPen(QColor(TEXT))
            down, up = f"↓{fmt_rate(s.down)}", f" ↑{fmt_rate(s.up)}"
            fm = p.fontMetrics()
            x = self.width() - 4 - fm.horizontalAdvance(down + up)
            p.drawText(QRectF(x, 0, self.width(), h), Qt.AlignVCenter | Qt.AlignLeft, down)
            p.setPen(QColor(DIM))
            p.drawText(QRectF(x + fm.horizontalAdvance(down), 0, self.width(), h), Qt.AlignVCenter | Qt.AlignLeft, up)
            return
        v = self.value()
        txt = "–" if v is None else f"{v:.0f}%"
        accent = QColor(self.bar.cfg["accent"])
        hot = v is not None and v >= 90
        if self.variant == "ring":
            d = uw(22)
            r = QRectF(4, h / 2 - d / 2, d, d)
            p.setPen(QPen(QColor(*T["hover"]), 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r)
            if v is not None:
                pen = QPen(QColor("#f87171") if hot else accent, 3)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawArc(r, 90 * 16, -int(360 * 16 * min(v, 100) / 100))
            f = self.text_font()
            f.setPixelSize(uw(7))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(DIM))
            p.drawText(r, Qt.AlignCenter, self.LABELS[self.kind])
            p.setFont(self.text_font())
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(d + 8, 0, self.width() - d - 8, h), Qt.AlignVCenter | Qt.AlignLeft, txt)
            return
        x = 4
        if self.variant == "graph":
            gw, gh = uw(38), min(uw(22), h - 6)
            path = QPainterPath()
            vals = list(s.cpu_hist)
            for i, val in enumerate(vals):
                px = x + i * (gw - 1) / (len(vals) - 1)
                py = h / 2 + gh / 2 - 2 - (gh - 4) * min(val, 100) / 100
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            fill = QPainterPath(path)
            fill.lineTo(x + gw - 1, h / 2 + gh / 2)
            fill.lineTo(x, h / 2 + gh / 2)
            fill.closeSubpath()
            c = QColor(accent)
            c.setAlpha(50)
            p.fillPath(fill, c)
            p.setPen(QPen(accent, 1.4))
            p.drawPath(path)
            x += gw + 4
        label = self.LABELS[self.kind]
        fm = p.fontMetrics()
        p.setPen(QColor(DIM))
        p.drawText(QRectF(x, 0, self.width(), h), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.setPen(QColor("#f87171") if hot else QColor(TEXT))
        p.drawText(QRectF(x, 0, self.width() - x - 4, h), Qt.AlignVCenter | Qt.AlignRight, txt)
        del fm


class WorldClockWidget(QWidget):
    """A second time zone on the bar (the first one in Settings > World clocks)."""

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.text = ""
        self.refresh()

    def zone(self):
        zones = self.bar.cfg.get("world_clocks") or [["UTC", "UTC"]]
        return zones[0]

    def refresh(self):
        name, tz = self.zone()
        try:
            from zoneinfo import ZoneInfo
            t = datetime.now(ZoneInfo(tz))
            self.text = f"{name} {t.strftime('%H:%M' if self.bar.cfg.get('clock_24h', True) else '%I:%M %p').lstrip('0')}"
            lines = []
            for n, z in self.bar.cfg.get("world_clocks", []):
                lines.append(f"{n}: {datetime.now(ZoneInfo(z)).strftime('%a %H:%M')}")
            self.setToolTip("\n".join(lines) + "\nClick: calendar & world clocks")
        except Exception:
            self.text = f"{name} ?"
        fm = QFontMetricsF(self.font())
        self.setFixedWidth(int(fm.horizontalAdvance("🌐 " + self.text) + 18))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.bar.open_calendar(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QColor(TEXT))
        p.drawText(self.rect().adjusted(8, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, "🌐 " + self.text)


class Media(Pill):
    def __init__(self, bar):
        super().__init__(spacing=0)
        self.bar = bar
        self.data = None
        self._art_key, self._art = None, None
        self.label = QLabel()
        self.label.setFixedWidth(uw(132))
        self.label.setProperty("class", "media")
        self.prev = glyph_button(G["prev"], "Previous", 12)
        self.play = glyph_button(G["play"], "Play / pause", 13)
        self.next = glyph_button(G["next"], "Next", 12)
        self.prev.clicked.connect(lambda: self.command("prev"))
        self.play.clicked.connect(lambda: self.command("toggle"))
        self.next.clicked.connect(lambda: self.command("next"))
        self.lay.addWidget(self.label)
        self.lay.addSpacing(4)
        for b in (self.prev, self.play, self.next):
            self.lay.addWidget(b)
        self.card = MediaCard(bar, self)
        self.on_update(None)
        self.worker = MediaWorker()
        self.worker.updated.connect(self.on_update)
        self.worker.start()

    def command(self, cmd, arg=None):
        if not self.worker.send(cmd, arg) and cmd not in ("select", "seek"):   # no media API: media keys
            send_keys({"toggle": "playpause", "next": "next", "prev": "prev"}[cmd])

    def enterEvent(self, e):
        self.card.hover()

    def leaveEvent(self, e):
        self.card.leave()

    def art_pixmap(self, size):
        d = self.data
        if not d or not d.get("thumb"):
            return None
        if self._art_key != (d["key"], size):
            img = QImage.fromData(d["thumb"])
            if img.isNull():
                return None
            dpr = self.devicePixelRatioF()
            px = int(size * dpr)
            img = img.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            img = img.copy((img.width() - px) // 2, (img.height() - px) // 2, px, px)
            rounded = QPixmap(px, px)
            rounded.fill(Qt.transparent)
            p = QPainter(rounded)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, px, px), 12 * dpr, 12 * dpr)
            p.setClipPath(path)
            p.drawImage(0, 0, img)
            p.end()
            rounded.setDevicePixelRatio(dpr)
            self._art_key, self._art = (d["key"], size), rounded
        return self._art

    def rescale(self):
        self.label.setFixedWidth(uw(132))
        self._sig = 0
        self.on_update(self.data)

    def on_update(self, d):
        self.data = d
        sig = (d["title"], d["artist"], d["playing"], d["can_next"], d["can_prev"]) if d else None
        if sig == getattr(self, "_sig", 0):      # the worker reports every second; most seconds nothing changed
            if self.card.isVisible():
                self.card.refresh()
            return
        self._sig = sig
        playing = bool(d and d["playing"])
        self.play.setText(G["pause"] if playing else G["play"])
        self.next.setEnabled(bool(d and d["can_next"]) or d is None)
        self.prev.setEnabled(bool(d and d["can_prev"]) or d is None)
        if d and d["title"]:
            full = f"{d['title']} — {d['artist']}" if d["artist"] else d["title"]
            fm = self.label.fontMetrics()
            self.label.setText(fm.elidedText(full, Qt.ElideRight, 124))
            self.label.setToolTip("")                    # the hover card shows everything
        else:   # keep the space reserved so nothing shifts when music starts/stops
            self.label.setText(f"<span style='color:{DIM}'>Nothing playing</span>")
        if self.card.isVisible():
            self.card.refresh()


class VolumePopup(QFrame):
    def __init__(self, bar, vol):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.bar, self.vol = bar, vol
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        row = QHBoxLayout()
        self.mute = glyph_button(G["vol"], "Mute", 14)
        self.mute.clicked.connect(self.on_mute)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(uc(200))
        self.slider.valueChanged.connect(self.on_slide)
        self.pct = QLabel("0")
        self.pct.setFixedWidth(uc(34))
        row.addWidget(self.mute)
        row.addWidget(self.slider)
        row.addWidget(self.pct)
        lay.addLayout(row)
        more = QPushButton("Sound && quick settings")
        more.setProperty("class", "link")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: (self.hide(), send_keys("win", "a")))
        lay.addWidget(more, alignment=Qt.AlignRight)
        bar.restyle(self)

    def sync(self):
        v, m = self.vol.get()
        if v is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(v)
            self.slider.blockSignals(False)
            self.pct.setText(str(v))
        self.mute.setText(G["mute"] if m else G["vol"])

    def on_slide(self, v):
        self.vol.set(v)
        self.pct.setText(str(v))
        self.bar.volume_btn.refresh()

    def on_mute(self):
        self.vol.toggle_mute()
        self.sync()
        self.bar.volume_btn.refresh()


class VolumeButton(QPushButton):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.vol = Volume()
        self.popup = None
        self.setProperty("class", "vol")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Scroll to change volume · click for slider · middle-click to mute")
        self.clicked.connect(self.open_popup)
        self.setFixedWidth(uw(70))
        self.ticks = 0
        self.setText(G["vol2"])
        QTimer.singleShot(0, self.refresh)         # after the first frame: loading the audio library takes a moment

    def refresh(self):
        self.ticks += 1
        if self.ticks % 15 == 0:      # pick up default-device changes (headphones etc.)
            self.vol.refresh_device()
        v, m = self.vol.get()
        if v is None:
            self.setText(G["vol"])
            return
        g = G["mute"] if m or v == 0 else (G["vol1"] if v < 34 else G["vol2"] if v < 67 else G["vol"])
        self.setText(f"{g}  {v}")

    def rescale(self):
        self.setFixedWidth(uw(70))

    def wheelEvent(self, e):
        v, _ = self.vol.get()
        step = 2 if e.angleDelta().y() > 0 else -2
        if v is None:
            send_keys("volup" if step > 0 else "voldown")
        else:
            self.vol.set(v + step)
        self.refresh()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.vol.toggle_mute()
            self.refresh()
        else:
            super().mouseReleaseEvent(e)

    def open_popup(self):
        self.vol.refresh_device()
        if self.popup is None:
            self.popup = VolumePopup(self.bar, self.vol)
        self.popup.sync()
        self.popup.adjustSize()
        anchor = self.mapToGlobal(QPoint(self.width() // 2, 0))
        self.popup.move(self.bar.popup_pos(self.popup.sizeHint(), anchor, center=True))
        self.popup.show()


class Clock(QWidget):
    """Clock styles: digital (time + date), analog (little clock face), flip (flip-card digits)."""
    WIDTHS = {"digital": 76, "analog": 34, "flip": 72}

    def __init__(self, bar, variant="digital"):
        super().__init__()
        self.bar = bar
        self.variant = variant
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(0)
        self.time = QLabel()
        self.time.setProperty("class", "time")
        self.date = QLabel()
        self.date.setProperty("class", "date")
        for l in (self.time, self.date):
            l.setAlignment(Qt.AlignRight)
            lay.addWidget(l)
        self.setCursor(Qt.PointingHandCursor)
        self.card = None
        self.set_variant(variant)

    def rescale(self):
        self.set_variant(self.variant)

    def set_variant(self, v):
        self.variant = v if v in self.WIDTHS else "digital"
        self.setFixedWidth(uw(self.WIDTHS[self.variant]))
        for l in (self.time, self.date):
            l.setVisible(self.variant == "digital")
        self.update()

    def paintEvent(self, e):
        if self.variant == "digital":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = datetime.now()
        accent = QColor(self.bar.cfg["accent"])
        if self.variant == "analog":
            import math
            c = QPoint(self.width() // 2, self.height() // 2)
            r = min(self.width(), self.height()) / 2 - 4
            p.setPen(QPen(QColor(*T["border"]), 1.2))
            p.setBrush(QColor(*T["pill"]))
            p.drawEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(DIM))
            for i in range(12):
                a = math.radians(i * 30)
                p.drawEllipse(QRectF(c.x() + (r - 2.5) * math.sin(a) - 0.8, c.y() - (r - 2.5) * math.cos(a) - 0.8, 1.6, 1.6))

            def hand(angle_deg, length, width, color):
                a = math.radians(angle_deg)
                pen = QPen(color, width)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawLine(c, QPoint(int(c.x() + length * math.sin(a)), int(c.y() - length * math.cos(a))))
            hand((now.hour % 12 + now.minute / 60) * 30, r * 0.52, 2.2, QColor(TEXT))
            hand((now.minute + now.second / 60) * 6, r * 0.78, 1.6, QColor(TEXT))
            hand(now.second * 6, r * 0.82, 1.0, accent)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QRectF(c.x() - 1.8, c.y() - 1.8, 3.6, 3.6))
        else:                                                # flip cards
            h24 = self.bar.cfg.get("clock_24h", True)
            hh = now.strftime("%H") if h24 else (now.strftime("%I").lstrip("0") or "12").rjust(2)
            mm = now.strftime("%M")
            f = QFont("Segoe UI Variable Display, Segoe UI")
            f.setPixelSize(16)
            f.setBold(True)
            p.setFont(f)
            cw, ch = 30, 26
            y = (self.height() - ch) / 2
            for i, txt in enumerate((hh, mm)):
                r = QRectF(2 + i * (cw + 6), y, cw, ch)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*T["hover"]))
                p.drawRoundedRect(r, 5, 5)
                p.setPen(QColor(TEXT))
                p.drawText(r, Qt.AlignCenter, txt)
                p.setPen(QPen(QColor(*T["bg"][:3]), 1))
                p.drawLine(QPoint(int(r.left()), int(r.center().y())), QPoint(int(r.right()), int(r.center().y())))
            p.setPen(Qt.NoPen)
            p.setBrush(accent if now.second % 2 == 0 else QColor(DIM))
            for dy in (-4, 4):
                p.drawEllipse(QRectF(2 + cw + 1.5, self.height() / 2 + dy - 1.5, 3, 3))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.card is None:
                self.card = CalendarCard(self.bar)
            self.card.open_from(self)

    def contextMenuEvent(self, e):
        e.accept()
        send_keys("win", "n")

    def refresh(self):
        if self.bar.cfg.get("clock_24h", True):
            self.time.setText(time.strftime("%H:%M"))
        else:
            self.time.setText(time.strftime("%I:%M %p").lstrip("0"))
        self.date.setText(time.strftime("%a %d %b"))
        self.setToolTip(time.strftime("%A, %d %B %Y · %H:%M") + "\nClick: calendar · Right-click: notifications")
        if self.variant != "digital":
            self.update()



# ======================================================================================
# Study suite: deadlines, next class, exam countdown, focus mode, study-time stats
# ======================================================================================
STUDY_PATH = os.path.join(CONFIG_DIR, "study.json")
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ICS_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
DUE_WORDS = ("due", "deadline", "submission", "submit", "closes", "assignment", "quiz", "homework", "lab report")
EXAM_WORDS = ("exam", "midterm", "mid-term", "final test", "examen")
COURSE_RE = None


def exe_base(path):
    return (path or "").replace("\\", "/").rsplit("/", 1)[-1]


def local_now():
    return datetime.now().astimezone()


def course_of(text):
    """Pull a course code like CYB404 / CYT 145 / COMP-1234 out of a title."""
    global COURSE_RE
    import re
    if COURSE_RE is None:
        COURSE_RE = re.compile(r"\b([A-Z]{2,5})[ -]?(\d{3,4}[A-Z]?)\b")
    m = COURSE_RE.search(text or "")
    return f"{m.group(1)}{m.group(2)}" if m else ""


START_WORDS = ("opens", "availability starts", "availability start", "start date", "starts", "available",
               "becomes available", "unlock")
_TAILS = (" is due", " closes", " due date", " due", " deadline", " availability ends", " availability end",
          " end date", " ends", " closes at")
_HEADS = ("due:", "due -", "deadline:", "submission:")


def clean_title(summary):
    """'Lab 4 is due' / 'Quiz 1 - Availability Ends' / 'Due: Essay' -> 'Lab 4' / 'Quiz 1' / 'Essay'."""
    s = (summary or "").strip().rstrip(".")
    low = s.lower()
    for h in _HEADS:
        if low.startswith(h):
            s, low = s[len(h):].strip(), low[len(h):].strip()
    changed = True
    while changed:
        changed = False
        for tail in _TAILS:
            if low.endswith(tail):
                s, low = s[: -len(tail)].rstrip(" -–:"), low[: -len(tail)].rstrip(" -–:")
                changed = True
    return s.strip() or "Untitled"


# ---- iCalendar (.ics) ----------------------------------------------------------------
def _ics_lines(text):
    """Unfold RFC 5545 continuation lines; yield (NAME, {params}, value)."""
    out = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    for line in out:
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        params = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v.strip('"')
        yield parts[0].upper(), params, value


def _ics_unescape(v):
    return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def ics_datetime(value, params):
    """-> (aware local datetime, all_day)"""
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and v.isdigit()):
        d = datetime.strptime(v[:8], "%Y%m%d")
        return d.astimezone(), True
    from datetime import timezone
    if v.endswith("Z"):
        return datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).astimezone(), False
    naive = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    tz = params.get("TZID")
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(), False
        except Exception:
            pass
    return naive.astimezone(), False


def _expand_rrule(start, end, rule, window_start, window_end, exdates=()):
    """Occurrences of a recurring event (FREQ=DAILY/WEEKLY with BYDAY, INTERVAL, COUNT, UNTIL)."""
    r = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq = r.get("FREQ", "").upper()
    interval = max(1, int(r.get("INTERVAL", "1") or 1))
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            until = ics_datetime(r["UNTIL"], {})[0]
        except ValueError:
            until = None
    dur = end - start
    out, n = [], 0
    if freq == "WEEKLY":
        days = sorted(ICS_DAYS[d[-2:]] for d in r.get("BYDAY", "").split(",") if d[-2:] in ICS_DAYS) \
            or [start.weekday()]
        week0 = start - timedelta(days=start.weekday())
        w = 0
        while n < 2000:
            base = week0 + timedelta(weeks=w * interval)
            for d in days:
                occ = base + timedelta(days=d)
                if occ < start:
                    continue
                if until and occ > until or (count is not None and n >= count):
                    return out
                n += 1
                if window_start <= occ + dur and occ <= window_end and occ.replace(tzinfo=None) not in exdates:
                    out.append((occ, occ + dur))
            if base > window_end:
                return out
            w += 1
    elif freq == "DAILY":
        occ = start
        while n < 2000:
            if until and occ > until or (count is not None and n >= count) or occ > window_end:
                return out
            n += 1
            if occ + dur >= window_start and occ.replace(tzinfo=None) not in exdates:
                out.append((occ, occ + dur))
            occ = occ + timedelta(days=interval)
    else:
        out.append((start, end))
    return out


def parse_ics(text, now=None, days_ahead=120):
    """Minimal, robust .ics reader -> list of events (recurrences expanded within the window)."""
    now = now or local_now()
    w0, w1 = now - timedelta(days=2), now + timedelta(days=days_ahead)
    events, cur = [], None
    for name, params, value in _ics_lines(text):
        if name == "BEGIN" and value.upper() == "VEVENT":
            cur = {"exdates": set()}
        elif name == "END" and value.upper() == "VEVENT" and cur is not None:
            if "start" in cur:
                start = cur["start"]
                end = cur.get("end") or start
                base = {"uid": cur.get("uid", ""), "summary": cur.get("summary", ""),
                        "location": cur.get("location", ""), "categories": cur.get("categories", ""),
                        "allday": cur.get("allday", False)}
                if cur.get("rrule"):
                    for i, (s, e) in enumerate(_expand_rrule(start, end, cur["rrule"], w0, w1, cur["exdates"])):
                        events.append(dict(base, uid=f"{base['uid']}#{s:%Y%m%dT%H%M}", start=s, end=e))
                elif w0 <= end and start <= w1:
                    events.append(dict(base, start=start, end=end))
            cur = None
        elif cur is not None:
            try:
                if name == "DTSTART":
                    cur["start"], cur["allday"] = ics_datetime(value, params)
                elif name in ("DTEND", "DUE"):
                    cur["end"] = ics_datetime(value, params)[0]
                elif name == "SUMMARY":
                    cur["summary"] = _ics_unescape(value)
                elif name == "LOCATION":
                    cur["location"] = _ics_unescape(value)
                elif name == "CATEGORIES":
                    cur["categories"] = _ics_unescape(value)
                elif name == "UID":
                    cur["uid"] = value
                elif name == "RRULE":
                    cur["rrule"] = value
                elif name == "EXDATE":
                    for v in value.split(","):
                        cur["exdates"].add(ics_datetime(v, params)[0].replace(tzinfo=None))
            except (ValueError, KeyError):
                continue
    return events


def classify_event(ev, feed_kind):
    """'deadline' | 'exam' | 'class' | None (ignored). Works for Moodle, Brightspace/D2L, Canvas,
    Blackboard, Schoology and Google Classroom (via Google Calendar) naming styles."""
    s = (ev.get("summary") or "").lower().strip().rstrip(".")
    if any(s.endswith(w) or f" - {w}" in s or f": {w}" in s for w in START_WORDS):
        return None                                      # "Quiz 3 opens", "Lab - Availability Starts"
    if any(w in s for w in EXAM_WORDS):
        return "exam"
    dur = (ev["end"] - ev["start"]).total_seconds()
    if any(w in s for w in DUE_WORDS) or s.endswith(" ends") or "availability end" in s or "end date" in s:
        return "deadline"
    if ev.get("allday"):                                 # Google Classroom / all-day items are never classes
        return "deadline" if feed_kind == "lms" else None
    if feed_kind == "lms" and dur <= 60:                 # Canvas/Blackboard: zero-length event at the due time
        return "deadline"
    if dur > 60:
        return "class"
    return None


def events_to_items(events, feed_kind):
    items = []
    for ev in events:
        kind = classify_event(ev, feed_kind)
        if not kind:
            continue
        title = clean_title(ev["summary"]) if kind == "deadline" else (ev["summary"] or "Untitled").strip()
        course = course_of(ev.get("categories", "")) or course_of(ev["summary"]) or (ev.get("categories") or "")[:12]
        when = ev["end"] if kind == "deadline" else ev["start"]
        if kind == "deadline" and ev.get("allday"):        # all-day: DTEND is the *next* day (exclusive)
            when = ev["start"].replace(hour=23, minute=59)
        items.append({"kind": kind, "id": ev.get("uid") or f"{title}|{when:%Y%m%d%H%M}", "title": title,
                      "course": course, "when": when, "end": ev["end"], "room": ev.get("location", ""),
                      "source": feed_kind})
    return items


# ---- manual entries (Settings text boxes) ---------------------------------------------
def parse_manual_dated(text, kind):
    """'2026-11-20 23:59 | Lab 4 | CYT145' per line (time and course optional)."""
    items = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        when = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                when = datetime.strptime(parts[0], fmt)
                if fmt == "%Y-%m-%d":
                    when = when.replace(hour=23 if kind == "deadline" else 9, minute=59 if kind == "deadline" else 0)
                break
            except ValueError:
                continue
        if when is None:
            continue
        when = when.astimezone()
        title = parts[1] or "Untitled"
        course = parts[2] if len(parts) > 2 else course_of(title)
        items.append({"kind": kind, "id": f"manual|{kind}|{title}|{when:%Y%m%d%H%M}", "title": title,
                      "course": course, "when": when, "end": when, "room": "", "source": "manual"})
    return items


def parse_timetable(text, now=None, days_ahead=14):
    """'Mon,Wed 10:00-11:50 | CYB404 | B2034' per line -> class occurrences for the next days."""
    now = now or local_now()
    items = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        head = parts[0].split()
        if len(head) < 2 or "-" not in head[-1]:
            continue
        days = [WEEKDAYS.index(d[:3].lower()) for d in " ".join(head[:-1]).replace(",", " ").split()
                if d[:3].lower() in WEEKDAYS]
        try:
            t0, t1 = [datetime.strptime(t, "%H:%M").time() for t in head[-1].split("-", 1)]
        except ValueError:
            continue
        course, room = parts[1], (parts[2] if len(parts) > 2 else "")
        for off in range(-1, days_ahead + 1):
            day = (now + timedelta(days=off)).date()
            if day.weekday() in days:
                s = datetime.combine(day, t0).astimezone()
                e = datetime.combine(day, t1).astimezone()
                items.append({"kind": "class", "id": f"tt|{course}|{s:%Y%m%d%H%M}", "title": course,
                              "course": course, "when": s, "end": e, "room": room, "source": "timetable"})
    return items


def dedupe_items(items):
    """Drop repeats: same id, or the same deadline reported twice (e.g. Brightspace 'Due' and
    'Availability Ends' for one quiz) - the earlier date wins, which is the safer one for students."""
    seen_ids, seen_dl, out = set(), set(), []
    for it in sorted(items, key=lambda i: i["when"]):
        if it["id"] in seen_ids:
            continue
        if it["kind"] == "deadline":
            k = (it["course"].lower(), it["title"].lower())
            if k in seen_dl:
                continue
            seen_dl.add(k)
        seen_ids.add(it["id"])
        out.append(it)
    return out


def fmt_left(delta):
    """timedelta -> '45m' / '6h 20m' / '1d 6h' / '12 days'."""
    secs = int(delta.total_seconds())
    if secs < 0:
        return "overdue"
    m = secs // 60
    if m < 60:
        return f"{max(m, 1)}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m:02d}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    if d >= 7:
        return f"{d} days"
    return f"{d}d {h}h" if h else f"{d}d"


def urgency(delta):
    secs = delta.total_seconds()
    if secs < 0 or secs < 24 * 3600:
        return "red"
    if secs < 48 * 3600:
        return "amber"
    return "normal"


# ---- study-time classification --------------------------------------------------------
BROWSERS = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi", "librewolf", "arc", "zen")
DEFAULT_STUDY = {
    "lms_links": "", "class_links": "", "timetable": "", "deadlines": "", "exams": "",
    "track_time": True, "focus_minutes": 25, "break_minutes": 5, "strict": False,
    "study_apps": "winword, excel, powerpnt, onenote, code, devenv, pycharm64, idea64, notepad, notepad++, "
                  "acrobat, acrord32, sumatrapdf, obsidian, notion, zotero, rstudio, matlab, wireshark, vmware, "
                  "virtualboxvm, putty, windowsterminal, powershell, cmd, packettracer, teams, zoom",
    "study_sites": "moodle, brightspace, canvas, blackboard, learn@, docs.google, google docs, scholar, overleaf, "
                   "wikipedia, jstor, stack overflow, github, tryhackme, hack the box, coursera, udemy, khan academy",
    "distraction_apps": "discord, steam, epicgameslauncher, battle.net, riotclientux, leagueclient, valorant",
    "distraction_sites": "youtube, netflix, tiktok, instagram, facebook, reddit, twitch, prime video, disney+, x.com",
}


SITE_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "x.com": "X", "disney+": "Disney+", "twitch": "Twitch",
              "github": "GitHub", "tryhackme": "TryHackMe", "hack the box": "Hack The Box", "jstor": "JSTOR",
              "stack overflow": "Stack Overflow", "docs.google": "Google Docs", "learn@": "Learn@",
              "brightspace": "Brightspace", "moodle": "Moodle", "canvas": "Canvas", "blackboard": "Blackboard"}


def site_name(site):
    return SITE_NAMES.get(site, site.title())


def _words(s):
    return [w.strip().lower() for w in (s or "").split(",") if w.strip()]


def classify_activity(exe, title, study_cfg):
    """-> ('study' | 'distract' | 'other', label for the stats)."""
    e = (exe or "").lower()
    e = e[:-4] if e.endswith(".exe") else e
    t = (title or "").lower()
    if e in BROWSERS:                      # websites only count inside a browser
        for site in _words(study_cfg.get("distraction_sites")):
            if site in t:
                return "distract", site_name(site)
        for site in _words(study_cfg.get("study_sites")):
            if site in t:
                return "study", site_name(site)
        return "other", exe
    if e in _words(study_cfg.get("distraction_apps")):
        return "distract", exe
    if e in _words(study_cfg.get("study_apps")):
        return "study", exe
    return "other", exe


# --------------------------------------------------------------------------------------
# Cards (click-to-open panels) and new widgets
# --------------------------------------------------------------------------------------
def animate_in(win, final_pos, bar, ms=170):
    """Fade in + glide out of the bar (up from a bottom bar, down from a top bar)."""
    from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup
    off = -10 if bar.cfg.get("position") == "top" else 10
    win.setWindowOpacity(0.0)
    win.move(final_pos + QPoint(0, off))
    win.show()
    grp = QParallelAnimationGroup(win)
    fade = QPropertyAnimation(win, b"windowOpacity", grp)
    fade.setDuration(ms)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    glide = QPropertyAnimation(win, b"pos", grp)
    glide.setDuration(ms + 30)
    glide.setStartValue(final_pos + QPoint(0, off))
    glide.setEndValue(final_pos)
    glide.setEasingCurve(QEasingCurve.OutCubic)
    grp.addAnimation(fade)
    grp.addAnimation(glide)
    win._anim_in = grp
    grp.start()


def animate_out(win, done, ms=140):
    from PySide6.QtCore import QEasingCurve
    fade = QPropertyAnimation(win, b"windowOpacity", win)
    fade.setDuration(ms)
    fade.setStartValue(win.windowOpacity())
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.InCubic)
    fade.finished.connect(done)
    win._anim_out = fade
    fade.start()


class Card(QFrame):
    """Popup panel that opens out of the bar, like Lumabar's widget cards."""

    def __init__(self, bar, width=300):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.bar = bar
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("card")
        self._card_box, self._card_w = box, width
        box.setFixedWidth(uc(width))
        outer.addWidget(box)
        self.body = QVBoxLayout(box)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(8)
        bar.restyle(self)
        inner = self.refresh

        def refresh(*a, **k):
            # Cards rebuild their rows from scratch; with painting paused the new rows appear in one frame
            # instead of flashing empty / half-built for a moment.
            self.setUpdatesEnabled(False)
            try:
                return inner(*a, **k)
            finally:
                self.setUpdatesEnabled(True)
        self.refresh = refresh

    def refresh(self):
        pass

    def open_from(self, widget):
        self.anchor_widget = widget
        self.refresh()
        self.adjustSize()
        anchor = widget.mapToGlobal(QPoint(widget.width() // 2, 0))
        animate_in(self, self.bar.popup_pos(self.size(), anchor, center=True), self.bar)

    def rescale(self):
        self._card_box.setFixedWidth(uc(self._card_w))
        if self.isVisible():
            self.refresh()
            self.reposition()

    def reposition(self):
        """Re-anchor after the content changed size (cards open away from the bar's edge)."""
        self.adjustSize()
        w = getattr(self, "anchor_widget", None)
        if w is not None:
            anchor = w.mapToGlobal(QPoint(w.width() // 2, 0))
            pos = self.bar.popup_pos(self.size(), anchor, center=True)
            if pos != self.pos():
                self.move(pos)


def clear_layout(lay):
    """Remove everything from a layout immediately (no ghost widgets until the next event loop pass)."""
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())
            it.layout().deleteLater()


def text_button(text, cls="chip", tip=""):
    b = QPushButton(text)
    b.setProperty("class", cls)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    if tip:
        b.setToolTip(tip)
    return b


def section(title):
    return lbl(title.upper(), "section")


# ---- Calendar + world clocks -----------------------------------------------------------
class CalendarCard(Card):
    def __init__(self, bar):
        super().__init__(bar, width=300)
        self.month = datetime.now().replace(day=1)
        head = QHBoxLayout()
        self.title = lbl("", "h2")
        prev = glyph_button(G["back"], "Previous month (or scroll up)", 11)
        nxt = glyph_button(G["fwd"], "Next month (or scroll down)", 11)
        self.title.setToolTip("Scroll anywhere on the calendar to change month")
        prev.clicked.connect(lambda: self.shift(-1))
        nxt.clicked.connect(lambda: self.shift(1))
        today = text_button("Today", "link")
        today.clicked.connect(self.go_today)
        head.addWidget(self.title)
        head.addStretch(1)
        for w in (today, prev, nxt):
            head.addWidget(w)
        self.body.addLayout(head)
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        self.body.addLayout(self.grid)
        self.clocks = QVBoxLayout()
        self.clocks.setSpacing(4)
        self.body.addWidget(section("World clocks"))
        self.body.addLayout(self.clocks)
        notif = text_button("Open notifications", "link")
        notif.clicked.connect(lambda: (self.hide(), send_keys("win", "n")))
        self.body.addWidget(notif, alignment=Qt.AlignRight)

    def go_today(self):
        self.month = datetime.now().replace(day=1)
        self.refresh()

    def shift(self, n):
        m = self.month.month - 1 + n
        self.month = self.month.replace(year=self.month.year + m // 12, month=m % 12 + 1)
        self.refresh()
        if self.isVisible():
            self.reposition()                     # 5- and 6-week months differ in height

    def wheelEvent(self, e):
        """Scroll down = next month, scroll up = previous. Touchpads send small steps: add them up."""
        self._wheel = getattr(self, "_wheel", 0) + e.angleDelta().y()
        steps = 0
        while abs(self._wheel) >= 120:
            steps += -1 if self._wheel > 0 else 1
            self._wheel += -120 if self._wheel > 0 else 120
        if steps:
            self.shift(steps)
        e.accept()

    def refresh(self):
        self.title.setText(self.month.strftime("%B %Y"))
        clear_layout(self.grid)
        sunday = self.bar.cfg.get("week_starts_sunday", True)
        cal = calendar.Calendar(firstweekday=6 if sunday else 0)
        names = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] if sunday else ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        for c, n in enumerate(names):
            self.grid.addWidget(lbl(n, "cal-head"), 0, c, alignment=Qt.AlignCenter)
        today = datetime.now().date()
        for r, week in enumerate(cal.monthdatescalendar(self.month.year, self.month.month), start=1):
            for c, d in enumerate(week):
                cls = "cal-today" if d == today else ("cal" if d.month == self.month.month else "cal-out")
                w = lbl(str(d.day), cls)
                w.setFixedSize(uc(38), uc(30))
                w.setAlignment(Qt.AlignCenter)
                self.grid.addWidget(w, r, c)
        clear_layout(self.clocks)
        try:
            from zoneinfo import ZoneInfo
            for name, tz in self.bar.cfg.get("world_clocks", []):
                now = datetime.now(ZoneInfo(tz))
                row = QHBoxLayout()
                row.addWidget(lbl(name))
                row.addStretch(1)
                row.addWidget(lbl(now.strftime("%a"), "dim"))
                row.addWidget(lbl(now.strftime("%H:%M" if self.bar.cfg.get("clock_24h", True) else "%I:%M %p")))
                self.clocks.addLayout(row)
        except Exception as ex:   # tzdata missing etc.
            self.clocks.addWidget(lbl(f"World clocks unavailable ({ex.__class__.__name__}) - pip install tzdata",
                                      "dim"))


# ---- System card: top processes, memory, disks ---------------------------------------
class SystemSampler(QThread):
    """Does all the slow work (walking every process, reading drives) off the UI thread,
    so the card opens and closes instantly."""
    sampled = Signal(dict)

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.procs = {}
        self.ncpu = psutil.cpu_count() or 1
        self.disks = []
        self.disk_age = 0

    def proc_rows(self):
        seen, rows = set(), []
        for p in psutil.process_iter(["name", "memory_info"]):
            seen.add(p.pid)
            cached = self.procs.setdefault(p.pid, p)
            try:
                cpu = cached.cpu_percent(None) / self.ncpu
            except (psutil.Error, OSError):
                continue
            name = p.info.get("name")
            if p.pid and name and name != "System Idle Process":
                mi = p.info.get("memory_info")
                rows.append((name, cpu, mi.rss if mi else 0))
        for pid in list(self.procs):
            if pid not in seen:
                del self.procs[pid]
        agg = {}
        for name, cpu, mem in rows:          # group like Task Manager (chrome.exe x30 -> one line)
            c, m, n = agg.get(name, (0, 0, 0))
            agg[name] = (c + cpu, m + mem, n + 1)
        return agg

    def read_disks(self):
        out = []
        for part in psutil.disk_partitions(all=False):
            # skip network/optical drives: a sleeping NAS or empty DVD drive can block for seconds
            if any(o in part.opts for o in ("cdrom", "remote")) or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            out.append((part.mountpoint.rstrip("\\"), u.percent, u.free))
        return out

    def gpu_rows(self, g):
        if not g or not g.get("top"):
            return []
        agg = {}
        for pid, pct in g["top"]:
            try:
                nm = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                nm = f"pid {pid}"
            c, m, n = agg.get(nm, (0, 0, 0))
            agg[nm] = (min(100.0, c + pct), m, n + 1)
        return sorted(agg.items(), key=lambda kv: -kv[1][0])[:5]

    def run(self):
        first = not self.procs
        if first:                      # prime per-process CPU counters, then measure 0.5 s later
            self.proc_rows()
            self.msleep(500)
        while not self.isInterruptionRequested():
            agg = self.proc_rows()
            if not self.disks or self.disk_age >= 10:     # drives change slowly: every ~15 s
                self.disks, self.disk_age = self.read_disks(), 0
            self.disk_age += 1
            vm = psutil.virtual_memory()
            g = self.bar.gpu_data
            self.sampled.emit({
                "cpu": psutil.cpu_percent(None), "ram_used": vm.used, "ram_total": vm.total,
                "uptime": datetime.now() - datetime.fromtimestamp(psutil.boot_time()),
                "top_cpu": sorted(agg.items(), key=lambda kv: -kv[1][0])[:5],
                "top_mem": sorted(agg.items(), key=lambda kv: -kv[1][1])[:5],
                "gpu": g, "top_gpu": self.gpu_rows(g), "disks": self.disks,
            })
            for _ in range(15):        # 1.5 s between samples, but stop within 100 ms
                if self.isInterruptionRequested():
                    return
                self.msleep(100)


class SystemCard(Card):
    def __init__(self, bar):
        super().__init__(bar, width=330)
        self.content = QVBoxLayout()
        self.content.setSpacing(4)
        self.body.addLayout(self.content)
        row = QHBoxLayout()
        row.addStretch(1)
        tm = text_button("Task Manager", "link")
        tm.clicked.connect(lambda: (self.hide(), os.startfile("taskmgr.exe")))
        row.addWidget(tm)
        self.body.addLayout(row)
        self.data = None
        self.sampler = SystemSampler(bar)
        self.sampler.sampled.connect(self.on_sample)

    def showEvent(self, e):
        super().showEvent(e)
        if not self.sampler.isRunning():
            self.sampler.start()

    def hideEvent(self, e):
        self.sampler.requestInterruption()     # thread exits on its own within ~0.1 s
        super().hideEvent(e)

    def stop(self):
        self.sampler.requestInterruption()
        self.sampler.wait(1500)

    def on_sample(self, d):
        self.data = d
        if self.isVisible():
            self.refresh()
            self.reposition()

    def refresh(self):
        """Draws the last sample instantly (or 'Loading…'); never does slow work itself."""
        clear_layout(self.content)
        d = self.data
        if d is None:
            self.content.addWidget(lbl("Loading…", "h2"))
            self.content.addWidget(lbl("Reading processes and drives", "dim"))
            return
        up = d["uptime"]
        head = QHBoxLayout()
        head.addWidget(lbl(f"CPU {d['cpu']:.0f}%   RAM {d['ram_used'] / 2**30:.1f}/{d['ram_total'] / 2**30:.0f} GB",
                           "h2"))
        head.addStretch(1)
        head.addWidget(lbl(f"up {up.days}d {up.seconds // 3600}h", "dim"))
        self.content.addLayout(head)
        g = d["gpu"]
        if g:
            vram = (f"   VRAM {g['used'] / 2**30:.1f}/{g['total'] / 2**30:.0f} GB" if g["total"]
                    else f"   VRAM {g['used'] / 2**30:.1f} GB")
            self.content.addWidget(lbl(f"GPU {g['util']:.0f}%{vram}", "h2"))
            self.content.addWidget(lbl(g["name"], "dim"))

        def table(title, items, fmt):
            self.content.addWidget(section(title))
            for name, (c, m, n) in items:
                r = QHBoxLayout()
                nm = name.removesuffix(".exe") + (f"  ×{n}" if n > 1 else "")
                r.addWidget(lbl(nm[:30]))
                r.addStretch(1)
                r.addWidget(lbl(fmt(c, m)))
                self.content.addLayout(r)

        table("Top CPU", d["top_cpu"], lambda c, m: f"{c:.1f}%")
        if d["top_gpu"]:
            table("Top GPU", d["top_gpu"], lambda c, m: f"{c:.1f}%")
        table("Top memory", d["top_mem"],
              lambda c, m: f"{m / 2**20:,.0f} MB" if m < 2**30 else f"{m / 2**30:.1f} GB")
        self.content.addWidget(section("Storage"))
        for mount, pct, free in d["disks"]:
            r = QHBoxLayout()
            r.addWidget(lbl(mount))
            pb = QProgressBar()
            pb.setRange(0, 100)
            pb.setValue(int(pct))
            pb.setTextVisible(False)
            pb.setFixedHeight(6)
            r.addWidget(pb, 1)
            r.addWidget(lbl(f"{free / 2**30:.0f} GB free", "dim"))
            self.content.addLayout(r)


# ---- Weather (Open-Meteo: free, no key) ----------------------------------------------
WMO = [((0,), "☀", "Clear"), ((1, 2), "🌤", "Partly cloudy"), ((3,), "☁", "Overcast"),
       ((45, 48), "🌫", "Fog"), (tuple(range(51, 58)), "🌦", "Drizzle"), (tuple(range(61, 68)), "🌧", "Rain"),
       (tuple(range(71, 78)), "🌨", "Snow"), ((80, 81, 82), "🌧", "Showers"), ((85, 86), "🌨", "Snow showers"),
       ((95, 96, 99), "⛈", "Thunderstorm")]


def wmo(code):
    for codes, icon, text in WMO:
        if code in codes:
            return icon, text
    return "·", "—"


# WWO condition codes (wttr.in) -> WMO codes used everywhere else
def wwo_to_wmo(code):
    c = int(code)
    if c == 113: return 0
    if c == 116: return 2
    if c in (119, 122): return 3
    if c in (143, 248, 260): return 45
    if c in (200, 386, 389, 392, 395): return 95
    if c in (263, 266, 281, 284, 185): return 51
    if c in (353, 356, 359): return 80
    if c in (176, 293, 296, 299, 302, 305, 308, 311, 314, 317, 320, 362, 365): return 61
    if c in (179, 182, 227, 230, 323, 326, 329, 332, 335, 338, 350, 368, 371, 374, 377): return 71
    return 3


def met_to_wmo(symbol):
    s = symbol or ""
    if "thunder" in s: return 95
    if s.startswith("clearsky"): return 0
    if s.startswith("fair"): return 1
    if s.startswith("partlycloudy"): return 2
    if s.startswith("cloudy"): return 3
    if s.startswith("fog"): return 45
    if "snowshowers" in s: return 85
    if "snow" in s or "sleet" in s: return 71
    if "rainshowers" in s: return 80
    if "drizzle" in s: return 51
    if "rain" in s: return 61
    return 3


def _to24(t):
    try:
        return datetime.strptime(t.strip(), "%I:%M %p").strftime("%H:%M")
    except (ValueError, AttributeError):
        return None


class WeatherWorker(QThread):
    """Fetches weather with automatic fallback between free, key-less providers:
    Open-Meteo -> wttr.in -> MET Norway (yr.no). The first one that answers wins."""
    done = Signal(dict)
    UA = "OrbitBar/1.0 (personal Windows taskbar)"

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.coords = None      # (lat, lon, place) once any source resolves it

    # ---- http with a certificate fallback (antivirus/proxy HTTPS inspection, old CA bundles)
    def get(self, url):
        import ssl
        req = urllib.request.Request(url, headers={"User-Agent": self.UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as ex:
            if not isinstance(getattr(ex, "reason", None), ssl.SSLError):
                raise
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                raise ex
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                return json.loads(r.read().decode("utf-8"))

    def imperial(self):
        return self.cfg.get("weather", {}).get("units") == "fahrenheit"

    def resolve(self):
        if self.coords:
            return self.coords
        w = self.cfg.get("weather", {})
        if w.get("latitude") is not None and w.get("longitude") is not None:
            self.coords = (w["latitude"], w["longitude"], w.get("city") or "Here")
            return self.coords
        city = (w.get("city") or "auto").strip()
        if city.lower() == "auto":
            raise RuntimeError("location 'auto' needs wttr.in first")
        q = urllib.parse.quote(city)
        try:                                   # 1st choice: Open-Meteo geocoding
            g = self.get("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + q)
            if g.get("results"):
                hit = g["results"][0]
                self.coords = (hit["latitude"], hit["longitude"], hit["name"])
                return self.coords
        except Exception:
            pass
        n = self.get(f"https://nominatim.openstreetmap.org/search?format=json&limit=1&q={q}")   # 2nd: OpenStreetMap
        if not n:
            raise RuntimeError(f"city '{city}' not found")
        self.coords = (float(n[0]["lat"]), float(n[0]["lon"]), city)
        return self.coords

    # ---- providers: each returns data in one common (Open-Meteo-like) shape
    def open_meteo(self):
        lat, lon, place = self.resolve()
        imp = self.imperial()
        d = self.get("https://api.open-meteo.com/v1/forecast?"
                     f"latitude={lat}&longitude={lon}&timezone=auto&forecast_days=6"
                     f"&temperature_unit={'fahrenheit' if imp else 'celsius'}&wind_speed_unit={'mph' if imp else 'kmh'}"
                     "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m"
                     "&hourly=temperature_2m,weather_code"
                     "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset")
        d["place"], d["wind_unit"] = place, "mph" if imp else "km/h"
        return d

    def wttr(self):
        w = self.cfg.get("weather", {})
        city = (w.get("city") or "auto").strip()
        loc = "" if city.lower() == "auto" else urllib.parse.quote(city)
        if w.get("latitude") is not None and w.get("longitude") is not None:
            loc = f"{w['latitude']},{w['longitude']}"
        j = self.get(f"https://wttr.in/{loc}?format=j1")
        imp = self.imperial()
        u = "F" if imp else "C"
        c = j["current_condition"][0]
        area = (j.get("nearest_area") or [{}])[0]
        place = ((area.get("areaName") or [{}])[0].get("value")) or city
        try:
            if not self.coords and area.get("latitude"):
                self.coords = (float(area["latitude"]), float(area["longitude"]), place)
        except (TypeError, ValueError):
            pass
        try:
            now = datetime.strptime(c["localObsDateTime"], "%Y-%m-%d %I:%M %p")
        except (KeyError, ValueError):
            now = datetime.now()
        ht, htemp, hcode = [], [], []
        daily = {k: [] for k in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min",
                                 "sunrise", "sunset")}
        for day in j.get("weather", []):
            for h in day.get("hourly", []):
                ht.append(f"{day['date']}T{int(h['time']) // 100:02d}:00")
                htemp.append(float(h["temp" + u]))
                hcode.append(wwo_to_wmo(h["weatherCode"]))
            hrs = day.get("hourly") or [{"weatherCode": 119}]
            daily["time"].append(day["date"])
            daily["weather_code"].append(wwo_to_wmo(hrs[len(hrs) // 2]["weatherCode"]))
            daily["temperature_2m_max"].append(float(day["maxtemp" + u]))
            daily["temperature_2m_min"].append(float(day["mintemp" + u]))
            ast = (day.get("astronomy") or [{}])[0]
            for key in ("sunrise", "sunset"):
                t = _to24(ast.get(key, ""))
                daily[key].append(f"{day['date']}T{t}" if t else None)
        return {"place": place, "wind_unit": "mph" if imp else "km/h",
                "current": {"time": now.strftime("%Y-%m-%dT%H:%M"),
                            "temperature_2m": float(c["temp_" + u]),
                            "apparent_temperature": float(c["FeelsLike" + u]),
                            "weather_code": wwo_to_wmo(c["weatherCode"]),
                            "wind_speed_10m": float(c["windspeedMiles" if imp else "windspeedKmph"]),
                            "relative_humidity_2m": float(c["humidity"])},
                "hourly": {"time": ht, "temperature_2m": htemp, "weather_code": hcode},
                "daily": daily}

    def met_norway(self):
        lat, lon, place = self.resolve()
        j = self.get(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat:.4f}&lon={lon:.4f}")
        imp = self.imperial()
        conv = (lambda t: t * 9 / 5 + 32) if imp else (lambda t: t)
        rows = []
        for e in j["properties"]["timeseries"]:
            t = datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone()
            det = e["data"]["instant"]["details"]
            nxt = e["data"].get("next_1_hours") or e["data"].get("next_6_hours") or {}
            rows.append((t, conv(det["air_temperature"]), met_to_wmo(nxt.get("summary", {}).get("symbol_code")),
                         det.get("wind_speed", 0.0), det.get("relative_humidity", 0.0)))
        if not rows:
            raise RuntimeError("empty forecast")
        t0, temp0, code0, wind0, hum0 = rows[0]
        days = OrderedDict()
        for t, temp, code, _, _ in rows:
            d = days.setdefault(t.strftime("%Y-%m-%d"), {"temps": [], "codes": {}})
            d["temps"].append(temp)
            d["codes"][t.hour] = code
        daily = {k: [] for k in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min",
                                 "sunrise", "sunset")}
        for date, d in list(days.items())[:6]:
            daily["time"].append(date)
            noon = min(d["codes"], key=lambda h: abs(h - 12))
            daily["weather_code"].append(d["codes"][noon])
            daily["temperature_2m_max"].append(max(d["temps"]))
            daily["temperature_2m_min"].append(min(d["temps"]))
            daily["sunrise"].append(None)
            daily["sunset"].append(None)
        hourly = rows[:48]
        return {"place": place, "wind_unit": "mph" if imp else "km/h",
                "current": {"time": t0.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": temp0,
                            "apparent_temperature": temp0, "weather_code": code0,
                            "wind_speed_10m": wind0 * (2.237 if imp else 3.6), "relative_humidity_2m": hum0},
                "hourly": {"time": [r[0].strftime("%Y-%m-%dT%H:00") for r in hourly],
                           "temperature_2m": [r[1] for r in hourly],
                           "weather_code": [r[2] for r in hourly]},
                "daily": daily, "no_feels": True}

    def run(self):
        w = self.cfg.get("weather", {})
        providers = OrderedDict([("Open-Meteo", self.open_meteo), ("wttr.in", self.wttr),
                                 ("MET Norway", self.met_norway)])
        pref = (w.get("provider") or "auto").lower()
        order = list(providers)
        if (w.get("city") or "").strip().lower() == "auto":
            order = ["wttr.in", "Open-Meteo", "MET Norway"]     # wttr.in can locate you by IP
        if pref != "auto":
            order.sort(key=lambda n: n.lower().replace(" ", "-") != pref.replace(" ", "-"))
        errors = []
        for name in order:
            try:
                data = providers[name]()
                data["source"] = name
                self.done.emit(data)
                return
            except Exception as ex:
                errors.append(f"{name}: {ex.__class__.__name__}: {str(ex)[:90]}")
        self.done.emit({"error": "\n".join(errors)})


class WeatherCard(Card):
    def __init__(self, bar):
        super().__init__(bar, width=320)
        self.content = QVBoxLayout()
        self.content.setSpacing(6)
        self.body.addLayout(self.content)
        self.data = None

    def refresh(self):
        clear_layout(self.content)
        d = self.data
        if not d or "error" in d:
            self.content.addWidget(lbl("Weather unavailable" if d else "Loading…", "h2"))
            if d:
                err = lbl(d["error"], "dim")
                err.setWordWrap(True)
                self.content.addWidget(err)
                hint = lbl("Check the city in config, or whether a firewall/antivirus is blocking Python.", "dim")
                hint.setWordWrap(True)
                self.content.addWidget(hint)
            retry = text_button("Retry", "link")
            retry.clicked.connect(self.bar.weather.fetch)
            self.content.addWidget(retry, alignment=Qt.AlignRight)
            return
        cur = d["current"]
        icon, desc = wmo(cur["weather_code"])
        top = QHBoxLayout()
        top.addWidget(lbl(f"{icon} {cur['temperature_2m']:.0f}°", "h1"))
        right = QVBoxLayout()
        right.addWidget(lbl(d["place"], "h2"))
        right.addWidget(lbl(desc, "dim"))
        top.addStretch(1)
        top.addLayout(right)
        self.content.addLayout(top)
        feels = "" if d.get("no_feels") else f"Feels {cur['apparent_temperature']:.0f}°  ·  "
        self.content.addWidget(lbl(f"{feels}Wind {cur['wind_speed_10m']:.0f} {d['wind_unit']}  ·  "
                                   f"Humidity {cur['relative_humidity_2m']:.0f}%", "dim"))
        # next 12 hours, every 2h
        self.content.addWidget(section("Hourly"))
        hours = QHBoxLayout()
        times = d["hourly"]["time"]
        start = next((i for i, t in enumerate(times) if t >= cur["time"][:13] + ":00"), 0)
        try:   # hourly data -> every 2nd hour; 3-hourly data (wttr.in) -> every slot
            gap = int(times[1][11:13]) - int(times[0][11:13]) if len(times) > 1 else 1
        except ValueError:
            gap = 1
        step = 2 if gap == 1 else 1
        for i in range(start, min(start + 6 * step, len(times)), step):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(lbl(times[i][11:13] + "h", "dim"), alignment=Qt.AlignCenter)
            col.addWidget(lbl(wmo(d["hourly"]["weather_code"][i])[0]), alignment=Qt.AlignCenter)
            col.addWidget(lbl(f"{d['hourly']['temperature_2m'][i]:.0f}°"), alignment=Qt.AlignCenter)
            hours.addLayout(col)
        self.content.addLayout(hours)
        self.content.addWidget(section("Next days"))
        dl = d["daily"]
        for i in range(1, len(dl["time"])):
            r = QHBoxLayout()
            day = datetime.strptime(dl["time"][i], "%Y-%m-%d").strftime("%a")
            r.addWidget(lbl(day))
            r.addWidget(lbl(wmo(dl["weather_code"][i])[0]))
            r.addStretch(1)
            r.addWidget(lbl(f"{dl['temperature_2m_min'][i]:.0f}°", "dim"))
            r.addWidget(lbl(f"{dl['temperature_2m_max'][i]:.0f}°"))
            self.content.addLayout(r)
        foot = QHBoxLayout()
        if dl.get("sunrise") and dl["sunrise"][0] and dl["sunset"][0]:
            foot.addWidget(lbl(f"☀↑ {dl['sunrise'][0][11:16]}    ☀↓ {dl['sunset'][0][11:16]}", "dim"))
        foot.addStretch(1)
        foot.addWidget(lbl(f"via {d.get('source', '')}", "dim"))
        self.content.addLayout(foot)


class WeatherWidget(QPushButton):
    def __init__(self, bar):
        super().__init__("…")
        self.bar = bar
        self.setProperty("class", "chip")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumWidth(58)
        self.card = WeatherCard(bar)
        self.clicked.connect(lambda: self.card.open_from(self))
        self.worker = None
        self.timer = QTimer(self, interval=15 * 60 * 1000, timeout=self.fetch)
        self.timer.start()
        QTimer.singleShot(1500, self.fetch)

    def refetch(self):
        """City/units changed in Settings: forget the old location and fetch again."""
        self._new_place = True
        if not (self.worker and self.worker.isRunning()):
            self.fetch()                       # (if one is running, on_data starts the fresh one)

    def fetch(self):
        if self.worker and self.worker.isRunning():
            return
        coords = None if getattr(self, "_new_place", False) else (self.worker.coords if self.worker else None)
        self._new_place = False
        self.worker = WeatherWorker(self.bar.cfg)
        self.worker.coords = coords
        self.worker.done.connect(self.on_data)
        self.worker.finished.connect(self._after_fetch)
        self.worker.start()

    def _after_fetch(self):
        if getattr(self, "_new_place", False):       # the city changed while this fetch was running
            self.worker.wait(1000)
            self.fetch()

    def on_data(self, d):
        self.card.data = d
        if "error" in d:
            self.setText("☁ —")
            self.setToolTip("Weather unavailable - click for details\n" + d["error"])
            QTimer.singleShot(5 * 60_000, self.fetch)   # retry in 5 minutes
            if self.card.isVisible():
                self.card.refresh()
                self.card.reposition()
            return
        icon, desc = wmo(d["current"]["weather_code"])
        self.setText(f"{icon} {d['current']['temperature_2m']:.0f}°")
        self.setToolTip(f"{d['place']}: {desc}  (via {d.get('source', '?')})")
        if self.card.isVisible():
            self.card.refresh()
            self.card.reposition()


# ---- Toast (non-activating notification) ---------------------------------------------
class ToastManager:
    """Stacks notifications above (or below) the bar's right end, newest closest to the bar."""
    MAX = 4

    def __init__(self, bar):
        self.bar = bar
        self.live = []
        self.last_beep = 0.0

    def notify(self, title, text, anchor=None, on_click=None, linger=False):
        """on_click: what clicking the notification does (it always closes it). linger: stays 30 s, not 8."""
        t = Toast(self.bar, self)
        t.on_click = on_click
        t.set(title, text)
        if linger:
            t.timer.start(30_000)
        if on_click:
            t.setCursor(Qt.PointingHandCursor)
        self.live.append(t)
        while len(self.live) > self.MAX:
            self.live[0].dismiss()
        self.restack(new=t)
        if time.monotonic() - self.last_beep > 2:           # one sound for a burst of notifications
            self.last_beep = time.monotonic()
            try:
                import winsound
                winsound.MessageBeep(0x40)
            except Exception:
                pass

    def remove(self, t):
        if t in self.live:
            self.live.remove(t)
            self.restack()

    def restack(self, new=None):
        g = self.bar.geometry()
        top = self.bar.cfg.get("position") == "top"
        y = g.bottom() + 8 if top else g.top() - 8
        for t in reversed(self.live):                        # newest nearest the bar
            t.adjustSize()
            x = g.right() - t.width() - 10
            pos = QPoint(x, y) if top else QPoint(x, y - t.height())
            y = (y + t.height() + 8) if top else (y - t.height() - 8)
            if t is new:
                animate_in(t, pos, self.bar)
            elif t.pos() != pos:
                glide = QPropertyAnimation(t, b"pos", t)
                glide.setDuration(160)
                glide.setEndValue(pos)
                t._glide = glide
                glide.start()


class Toast(QWidget):
    def __init__(self, bar, manager=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.bar = bar
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(uc(320))
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(uc(18), uc(14), uc(18), uc(14))
        v.setSpacing(uc(4))
        self.title, self.text = lbl("", "toast-title"), lbl("", "toast")
        self.text.setWordWrap(True)
        v.addWidget(self.title)
        v.addWidget(self.text)
        bar.restyle(self)
        self.manager = manager
        self.gone = False
        self.on_click = None
        self.timer = QTimer(self, singleShot=True, interval=8000, timeout=self.dismiss)

    def set(self, title, text):
        self.title.setText(title)
        self.text.setText(text)
        self.timer.start()

    def dismiss(self):
        if self.gone:
            return
        self.gone = True
        self.timer.stop()
        if self.manager:
            self.manager.remove(self)

        def done():
            self.hide()
            self.deleteLater()
        animate_out(self, done)

    def mouseReleaseEvent(self, e):
        action = self.on_click if e.button() == Qt.LeftButton else None
        self.dismiss()
        if action:
            QTimer.singleShot(0, action)


# ---- Timer / Pomodoro ------------------------------------------------------------------
class TimerCard(Card):
    """Focus mode (Pomodoro with distraction catching), quick timers, and today's study stats."""

    def __init__(self, bar, widget):
        super().__init__(bar, width=350)
        self.w = widget
        self.big = lbl("25:00", "h1")
        self.big.setAlignment(Qt.AlignCenter)
        self.state = lbl("", "dim")
        self.state.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.big)
        self.body.addWidget(self.state)
        st = bar.cfg.setdefault("study", dict(DEFAULT_STUDY))
        fm, bm = int(st.get("focus_minutes", 25)), int(st.get("break_minutes", 5))
        focus = QHBoxLayout()
        for label, f, b in ((f"🔒 Focus {fm}/{bm}", fm, bm), ("🔒 Deep 50/10", 50, 10)):
            btn = text_button(label, "chip", f"{f} min focus, {b} min break, repeating")
            btn.clicked.connect(lambda _=False, f=f, b=b: self.w.start_focus(f, b))
            focus.addWidget(btn)
        self.body.addLayout(focus)
        ctl = QHBoxLayout()
        self.pause = text_button("Pause")
        self.pause.clicked.connect(self.w.toggle_pause)
        stop = text_button("Stop")
        stop.clicked.connect(self.w.reset)
        ctl.addWidget(self.pause)
        ctl.addWidget(stop)
        self.body.addLayout(ctl)
        self.strict = QCheckBox("Strict: minimize distracting apps, ask about sites")
        self.strict.setChecked(bool(st.get("strict", False)))
        self.strict.toggled.connect(lambda on: (st.__setitem__("strict", on), save_config(bar.cfg)))
        self.body.addWidget(self.strict)
        self.body.addWidget(section("Quick timers"))
        presets = QHBoxLayout()
        for name, mins in bar.cfg.get("timer_presets", [])[:3]:
            b = text_button(f"{name}\n{mins} min")
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, n=name, m=mins: self.w.start(m, n))
            presets.addWidget(b)
        self.body.addLayout(presets)
        custom = QHBoxLayout()
        self.spin = QSpinBox()
        self.spin.setRange(1, 600)
        self.spin.setValue(10)
        self.spin.setSuffix(" min")
        go = text_button("Start timer")
        go.clicked.connect(lambda: self.w.start(self.spin.value(), "Timer"))
        custom.addWidget(self.spin, 1)
        custom.addWidget(go)
        self.body.addLayout(custom)
        self.stats_head = section("Today")
        self.body.addWidget(self.stats_head)
        self.stats = QVBoxLayout()
        self.stats.setSpacing(3)
        self.body.addLayout(self.stats)
        self.chart = WeekChart(bar.study.state, bar) if bar.study else None
        self._rows = None
        if self.chart:
            self.body.addWidget(self.chart)

    def stat(self, name, value, color=None):
        r = QHBoxLayout()
        r.addWidget(lbl(name, "dim"))
        r.addStretch(1)
        v = lbl(value)
        if color:
            v.setStyleSheet(f"color: {color}; font-weight: 600;")
        r.addWidget(v)
        self.stats.addLayout(r)

    def refresh(self):
        w = self.w
        self.big.setText(w.fmt(w.remaining() if w.mode else w.focus_len * 60))
        names = {None: "Ready when you are", "timer": w.name, "focus": f"Focus · round {w.round}",
                 "break": "Break · stretch, water, eyes off the screen"}
        self.state.setText(names[w.mode] + (" · paused" if w.paused else ""))
        self.pause.setText("Resume" if w.paused else "Pause")
        self.pause.setEnabled(bool(w.mode))
        study = self.bar.study
        if not study:
            self.stats_head.setText("TODAY · study tracking is off")
            if self._rows:
                clear_layout(self.stats)
                self._rows = None
            return
        day = study.state.day()
        rows = [("📚 Study time", fmt_hm(day.get("study", 0)), self.bar.cfg["accent"]),
                ("📱 Distractions", fmt_hm(day.get("distract", 0)), "#f87171" if day.get("distract", 0) > 1800 else None),
                ("🔒 Focus", f"{fmt_hm(day.get('focus', 0))} · {day.get('sessions', 0)} session"
                 + ("s" if day.get("sessions", 0) != 1 else ""), None)]
        streak = study.state.streak()
        if streak:
            rows.append(("🔥 Streak", f"{streak} day{'s' if streak != 1 else ''} (25+ min focus)", None))
        if day.get("caught"):
            rows.append(("🚫 Distractions caught", str(day["caught"]), None))
        apps = sorted(((k.split(":", 1)[1], v) for k, v in day.get("apps", {}).items() if k.startswith("study:")),
                      key=lambda kv: -kv[1])[:3]
        if apps:
            rows.append(("Top study apps", ", ".join(f"{n} {fmt_hm(v)}" for n, v in apps), None))
        if rows == self._rows:            # this runs every second while the card is open: usually nothing changed
            return
        self._rows = rows
        clear_layout(self.stats)
        for r in rows:
            self.stat(*r)
        if self.chart:
            self.chart.update()


class FocusPrompt(QWidget):
    """'Is this for study?' - shown in strict mode instead of minimizing a browser."""

    def __init__(self, bar, timer):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.bar, self.timer = bar, timer
        self.hwnd = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(uc(330))
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(16, 14, 16, 12)
        self.title = lbl("🔒 Focus check", "h2")
        self.text = lbl("", "dim")
        self.text.setWordWrap(True)
        v.addWidget(self.title)
        v.addWidget(self.text)
        row = QHBoxLayout()
        study = text_button("📚 It's for study", "chip", "Allow this window for 15 minutes")
        back = text_button("↩ Back to work", "chip", "Minimize it")
        study.clicked.connect(self.allow)
        back.clicked.connect(self.back)
        row.addWidget(study)
        row.addWidget(back)
        v.addLayout(row)
        bar.restyle(self)
        self.auto = QTimer(self, singleShot=True, interval=20000, timeout=self.hide)

    def ask(self, hwnd, name, left):
        self.hwnd = hwnd
        self.text.setText(f"{name} isn't on your study list. Is it for your course? ({left} of focus left)")
        self.adjustSize()
        anchor = self.timer.mapToGlobal(QPoint(self.timer.width() // 2, 0))
        self.move(self.bar.popup_pos(self.size(), anchor, center=True))
        self.show()
        self.auto.start()

    def allow(self):
        if self.bar.study and self.hwnd:
            self.bar.study.allow_window(self.hwnd)
        self.hide()

    def back(self):
        if self.hwnd:
            minimize_window(self.hwnd)
        self.hide()


class TimerWidget(QPushButton):
    """Bar button: idle ⏱ · focus 🔒 24:13 · break ☕ 04:12 · plain timer ⏱ 09:59."""

    def __init__(self, bar):
        super().__init__(G["timer"])
        self.bar = bar
        self.setProperty("class", "vol")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Focus mode, timers & study stats")
        self.mode, self.paused = None, False
        self.name, self.end, self.left = "", 0.0, 0.0
        st = bar.cfg.get("study", {})
        self.focus_len, self.break_len = int(st.get("focus_minutes", 25)), int(st.get("break_minutes", 5))
        self.round, self.focus_started, self.ticks = 0, 0.0, 0
        self.last_nudge = -1e9          # "never" (monotonic time can be small right after boot)
        self.card = TimerCard(bar, self)
        self.prompt = FocusPrompt(bar, self)
        self.clicked.connect(lambda: self.card.open_from(self))

    def sizeHint(self):
        """Width as if every digit were '0', so the countdown doesn't nudge its neighbours every second."""
        hint = super().sizeHint()
        fm = self.fontMetrics()
        txt = self.text()
        widest = max("0123456789", key=fm.horizontalAdvance)
        extra = fm.horizontalAdvance(re.sub(r"\d", widest, txt)) - fm.horizontalAdvance(txt)
        shape = re.sub(r"\d", "0", txt)
        w = hint.width() + max(0, extra) + 4
        if getattr(self, "_shape", None) == shape:          # same layout of text: never shrink by a pixel
            w = max(w, self._w)
        self._shape, self._w = shape, w
        return QSize(w, hint.height())

    @staticmethod
    def fmt(secs):
        secs = max(0, int(round(secs)))
        h, rem = divmod(secs, 3600)
        return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60:02d}:{rem % 60:02d}"

    def remaining(self):
        return self.left if self.paused else self.end - time.monotonic()

    # ---- controls
    def _begin(self, mode, minutes, name=""):
        self.mode, self.paused, self.name = mode, False, name
        self.end = time.monotonic() + minutes * 60
        if mode == "focus":
            self.focus_started = time.monotonic()
        self.refresh()

    def start(self, minutes, name):
        self._credit_focus()
        self._begin("timer", minutes, name)

    def start_focus(self, focus, brk):
        self._credit_focus()
        self.focus_len, self.break_len, self.round = focus, brk, 1
        self._begin("focus", focus)
        self.bar.toast.notify("🔒 Focus started", f"{focus} minutes. Distractions will be "
                              + ("minimized." if self.strict else "flagged."), self)

    def toggle_pause(self):
        if not self.mode:
            return
        if self.paused:
            self.end = time.monotonic() + self.left
        else:
            self.left = self.end - time.monotonic()
        self.paused = not self.paused
        self.refresh()

    def reset(self):
        self._credit_focus()
        self.mode, self.paused = None, False
        self.refresh()

    @property
    def strict(self):
        return bool(self.bar.cfg.get("study", {}).get("strict", False))

    def _credit_focus(self, full=False):
        """Count focus minutes into today's stats (a stopped session counts if it lasted 5+ minutes)."""
        if self.mode != "focus" or not self.bar.study:
            return
        secs = self.focus_len * 60 if full else min(self.focus_len * 60, time.monotonic() - self.focus_started)
        if secs >= 300:
            day = self.bar.study.state.day()
            day["focus"] = day.get("focus", 0) + int(secs)
            day["sessions"] = day.get("sessions", 0) + 1
            self.bar.study.state.dirty = True
            self.bar.study.state.save()

    # ---- every second (from the bar)
    def refresh(self):
        self.ticks += 1
        if self.mode and not self.paused and self.remaining() <= 0:
            if self.mode == "timer":
                self.mode = None
                self.bar.toast.notify("⏰ Time's up", f"{self.name} finished.", self)
            elif self.mode == "focus":
                self._credit_focus(full=True)
                long_break = self.round % 4 == 0
                mins = max(self.break_len, 15) if long_break else self.break_len
                self.bar.toast.notify("☕ Break time" + (" (long break)" if long_break else ""),
                                      f"Nice work - round {self.round} done. {mins} minute break.", self)
                self._begin("break", mins)
            elif self.mode == "break":
                self.round += 1
                self.bar.toast.notify("🔒 Back to focus", f"Round {self.round} · {self.focus_len} minutes.", self)
                self._begin("focus", self.focus_len)
        if self.mode == "focus" and not self.paused and self.ticks % 2 == 0:
            self.catch_distraction()
        if self.mode:
            icon = {"focus": "🔒", "break": "☕", "timer": G["timer"]}[self.mode]
            self.setText(f"{icon}  {self.fmt(self.remaining())}")
            color = {"focus": self.bar.cfg["accent"], "break": "#34d399", "timer": self.bar.cfg["accent"]}[self.mode]
            set_sheet(self, "" if self.paused else f"color: {color};")
        else:
            self.setText(G["timer"])
            set_sheet(self, "")
        if self.card.isVisible():
            self.card.refresh()

    def rebuild_card(self):
        """Settings changed (focus length, presets): new card, same running timer."""
        st = self.bar.cfg.get("study", {})
        if not self.mode:
            self.focus_len, self.break_len = int(st.get("focus_minutes", 25)), int(st.get("break_minutes", 5))
        was = self.card.isVisible()
        self.card.hide()
        self.card.deleteLater()
        self.card = TimerCard(self.bar, self)
        if was:
            self.card.open_from(self)

    def catch_distraction(self):
        h = user32.GetForegroundWindow()
        if not h or window_pid(h) == os.getpid():
            return
        exe = exe_base(window_exe(h))
        if self.bar.study:
            cat, label = self.bar.study.classify_window(h, exe, window_text(h))
        else:
            cat, label = classify_activity(exe, window_text(h), self.bar.cfg.get("study", DEFAULT_STUDY))
        if cat != "distract":
            return
        now = time.monotonic()
        name = label[:-4] if label.lower().endswith(".exe") else label
        browser = exe.lower().removesuffix(".exe") in BROWSERS
        if self.strict and browser:
            # a site might be study material (lecture video, docs): ask instead of yanking the browser away
            if not self.prompt.isVisible() and now - self.last_nudge > 45:
                self.last_nudge = now
                self.prompt.ask(h, name, self.fmt(self.remaining()))
            else:
                return
        elif self.strict:
            minimize_window(h)
            if now - self.last_nudge > 20:
                self.last_nudge = now
                self.bar.toast.notify("🔒 Focus mode", f"{name} minimized - {self.fmt(self.remaining())} to go.", self)
        elif now - self.last_nudge > 120:
            self.last_nudge = now
            self.bar.toast.notify("👀 Focus mode", f"{name} isn't study time - {self.fmt(self.remaining())} left.", self)
        else:
            return
        if self.bar.study:
            day = self.bar.study.state.day()
            day["caught"] = day.get("caught", 0) + 1
            self.bar.study.state.dirty = True


# ---- Recycle Bin -------------------------------------------------------------------------
class RecycleBinWidget(QPushButton):
    def __init__(self, bar):
        super().__init__(G["bin"])
        self.bar = bar
        self.setProperty("class", "vol")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(lambda: os.startfile("shell:RecycleBinFolder"))
        self._busy, self._last = False, None
        self.setToolTip("Recycle Bin")
        self.refresh()

    def refresh(self):
        """Windows checks the Recycle Bin on every drive (and can wake a sleeping disk to do it), which can
        take a while - so ask in the background and update when the answer comes."""
        if self._busy:
            return
        self._busy = True
        BgCall(recycle_bin_info_bg, self._on_info)

    def _on_info(self, res):
        self._busy = False
        n, size = res or (0, 0)
        if (n, size) == self._last:
            return
        self._last = (n, size)
        self.setText(f"{G['bin']}  {n}" if n else G["bin"])
        mb = size / 2**20
        self.setToolTip(f"Recycle Bin: {n} items, {mb:,.0f} MB\nClick: open · Right-click: empty"
                        if n else "Recycle Bin is empty")

    def contextMenuEvent(self, e):
        e.accept()
        m = QMenu(self)
        m.addAction("Open Recycle Bin").triggered.connect(lambda: os.startfile("shell:RecycleBinFolder"))
        empty = m.addAction("Empty Recycle Bin")
        empty.triggered.connect(lambda: (shell32.SHEmptyRecycleBinW(None, None, 0), self.refresh()))
        m.exec(self.bar.popup_pos(m.sizeHint(), e.globalPos()))


# ---- Quick launcher (apps, folders, paths) -----------------------------------------------
class SearchCard(QWidget):
    """Type an app name, a folder path or a file path and hit Enter."""

    def __init__(self, bar):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.bar = bar
        self.index = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("card")
        box.setFixedWidth(uc(420))
        self._card_box = box
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 12, 12, 10)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search apps, or type a path like C:\\Users or %APPDATA%")
        self.edit.textChanged.connect(self.update_results)
        self.edit.returnPressed.connect(self.launch_current)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.setIconSize(QSize(20, 20))
        self.list.setFixedHeight(uc(240))
        self.list.itemActivated.connect(self.launch_item)
        self.list.itemClicked.connect(self.launch_item)
        v.addWidget(self.edit)
        v.addWidget(self.list)
        hint = lbl("Enter: open · ↑↓: choose · Esc: close · Right-click 🔍: Windows Search", "dim")
        v.addWidget(hint)
        bar.restyle(self)
        self._icons, self._provider = {}, None
        self._indexing, self._indexed_at = False, 0.0
        QTimer.singleShot(6000, self.prebuild)          # ready before the first click

    @staticmethod
    def scan_start_menu():
        roots = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
                 os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")]
        idx = {}
        for root in roots:
            for dirpath, _, files in os.walk(root):
                for f in files:
                    name, ext = os.path.splitext(f)
                    if ext.lower() in (".lnk", ".url") and "uninstall" not in name.lower():
                        idx.setdefault(name, os.path.join(dirpath, f))
        return sorted(idx.items(), key=lambda kv: kv[0].lower())

    def build_index(self):
        self.index, self._indexed_at = self.scan_start_menu(), time.monotonic()

    def prebuild(self):
        """Scan the Start menu in the background (and again later, so newly installed apps show up)."""
        if not self._indexing:
            self._indexing = True
            BgCall(self.scan_start_menu, self._on_index)

    def _on_index(self, idx):
        self._indexing = False
        if idx is not None:
            self.index, self._indexed_at = idx, time.monotonic()
            if self.isVisible():
                self.update_results(self.edit.text())

    def icon_for(self, path):
        ic = self._icons.get(path)
        if ic is None:
            if self._provider is None:
                self._provider = QFileIconProvider()
            ic = self._provider.icon(QFileInfo(path))
            if len(self._icons) > 800:
                self._icons.clear()
            self._icons[path] = ic
        return ic

    def open(self, anchor):
        if self.index is None:
            self.build_index()
        elif time.monotonic() - self._indexed_at > 600:
            self.prebuild()
        self.edit.clear()
        self.update_results("")
        self.adjustSize()
        self.move(self.bar.popup_pos(self.size(), anchor.mapToGlobal(QPoint(0, 0))))
        self.show()
        self.raise_()
        self.activateWindow()
        user32.SwitchToThisWindow(int(self.winId()), True)
        self.edit.setFocus()

    @staticmethod
    def as_path(text):
        t = os.path.expanduser(os.path.expandvars(text.strip().strip('"')))
        if t.lower().startswith("shell:"):
            return t
        if (len(t) > 1 and t[1] == ":") or t.startswith("\\\\"):
            return t if os.path.exists(t) else None
        return None

    def update_results(self, text):
        if self.index is None:
            return
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        text = text.strip()
        path = self.as_path(text) if text else None
        if path:
            it = QListWidgetItem(f"Open  {path}")
            it.setData(Qt.UserRole, path)
            if not path.lower().startswith("shell:"):
                it.setIcon(self.icon_for(path))
            self.list.addItem(it)
        q = text.lower()
        hits = [kv for kv in self.index if q in kv[0].lower()] if q else self.index[:]
        hits.sort(key=lambda kv: (not kv[0].lower().startswith(q), kv[0].lower()))
        for name, lnk in hits[:40]:
            it = QListWidgetItem(self.icon_for(lnk), name)          # cached: typing stays instant
            it.setData(Qt.UserRole, lnk)
            self.list.addItem(it)
        if text and not path:
            it = QListWidgetItem(f"Search files for “{text}”")
            it.setData(Qt.UserRole, "search-ms:query=" + urllib.parse.quote(text))
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.setUpdatesEnabled(True)

    def launch_item(self, item):
        target = item.data(Qt.UserRole) if item else None
        if target:
            try:
                os.startfile(target)
            except OSError as ex:
                print("open failed:", ex)
        self.hide()

    def launch_current(self):
        self.launch_item(self.list.currentItem())

    def eventFilter(self, obj, e):
        if e.type() == QEvent.KeyPress:
            if e.key() == Qt.Key_Escape:
                self.hide()
                return True
            if e.key() in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if e.key() == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
        return False

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(e)


# ---- Live window previews (DWM thumbnails) -------------------------------------------
class ThumbSlot(QWidget):
    def __init__(self, hwnd, pix, on_click):
        super().__init__()
        self.hwnd, self.pix, self.on_click = hwnd, pix, on_click
        self.setFixedSize(220, 130)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click(self.hwnd, False)
        elif e.button() == Qt.MiddleButton:
            self.on_click(self.hwnd, True)

    def paintEvent(self, e):    # fallback drawn under the live thumbnail (minimized windows etc.)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*T["pill"]))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if self.pix:
            p.drawPixmap(QRectF(self.width() / 2 - 16, self.height() / 2 - 16, 32, 32), self.pix,
                         QRectF(self.pix.rect()))


class Previews(QWidget):
    def __init__(self, bar):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.bar = bar
        self.target = None
        self.thumbs = []
        self.slots = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.box = QFrame()
        self.box.setObjectName("card")
        outer.addWidget(self.box)
        self.row = QHBoxLayout(self.box)
        self.row.setContentsMargins(10, 8, 10, 10)
        self.row.setSpacing(10)
        bar.restyle(self)
        self.show_timer = QTimer(self, singleShot=True, interval=380, timeout=self._show)
        self.hide_timer = QTimer(self, singleShot=True, interval=260, timeout=self.close_now)

    def hover(self, btn):
        self.hide_timer.stop()
        self.target = btn
        if self.isVisible():
            self._show()
        else:
            self.show_timer.start()

    def leave(self):
        self.show_timer.stop()
        self.hide_timer.start()

    def enterEvent(self, e):
        self.hide_timer.stop()

    def leaveEvent(self, e):
        self.hide_timer.start()

    def _unregister(self):
        for th in self.thumbs:
            dwmapi.DwmUnregisterThumbnail(th)
        self.thumbs = []

    def close_now(self):
        self.show_timer.stop()
        self.hide_timer.stop()
        self._unregister()
        self.hide()

    def clicked(self, hwnd, close):
        self.close_now()
        close_window(hwnd) if close else activate_window(hwnd)

    def _show(self):
        btn = self.target
        if not btn or not btn.hwnds:
            return
        self._unregister()
        clear_layout(self.row)
        self.slots = []
        for h in btn.hwnds[:4]:
            col = QVBoxLayout()
            col.setSpacing(4)
            top = QHBoxLayout()
            title = lbl()
            title.setText(title.fontMetrics().elidedText(window_text(h), Qt.ElideRight, 180))
            x = glyph_button(G["close"], "Close window", 10)
            x.clicked.connect(lambda _=False, h=h: self.clicked(h, True))
            top.addWidget(title, 1)
            top.addWidget(x)
            col.addLayout(top)
            slot = ThumbSlot(h, btn.pix, self.clicked)
            col.addWidget(slot)
            self.row.addLayout(col)
            self.slots.append(slot)
        self.adjustSize()
        anchor = btn.mapToGlobal(QPoint(btn.width() // 2, 0))
        target = self.bar.popup_pos(self.size(), anchor, center=True)
        if self.isVisible():                       # moving between apps: just glide over
            self.move(target)
        else:
            animate_in(self, target, self.bar, ms=130)
        QTimer.singleShot(0, self._register)

    def _register(self):
        if not self.isVisible():
            return
        dest = int(self.winId())
        dpr = self.devicePixelRatioF()
        for slot in self.slots:
            th = wt.HANDLE()
            if dwmapi.DwmRegisterThumbnail(dest, slot.hwnd, ctypes.byref(th)) != 0:
                continue
            self.thumbs.append(th)
            src = wt.SIZE()
            dwmapi.DwmQueryThumbnailSourceSize(th, ctypes.byref(src))
            pos = slot.mapTo(self, QPoint(0, 0))
            bw, bh = slot.width() * dpr, slot.height() * dpr
            if src.cx <= 0 or src.cy <= 0:
                continue
            s = min(bw / src.cx, bh / src.cy)
            w, h = src.cx * s, src.cy * s
            x0, y0 = pos.x() * dpr + (bw - w) / 2, pos.y() * dpr + (bh - h) / 2
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY | DWM_TNP_SOURCECLIENTAREAONLY
            props.rcDestination = wt.RECT(int(x0), int(y0), int(x0 + w), int(y0 + h))
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = False
            dwmapi.DwmUpdateThumbnailProperties(th, ctypes.byref(props))



# ---- Battery (laptops) -----------------------------------------------------------------
def read_battery():
    """dict(percent, plugged, charging, saver, secs_left) or None when there is no battery."""
    st = SYSTEM_POWER_STATUS()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None
    flag = st.BatteryFlag & 0xFF
    pct = st.BatteryLifePercent & 0xFF
    if flag == 128 or flag == 255 or pct == 255:      # 128 = no system battery, 255 = unknown
        return None
    plugged = (st.ACLineStatus & 0xFF) == 1
    left = st.BatteryLifeTime
    return {"percent": float(pct), "plugged": plugged, "charging": bool(flag & 8),
            "saver": bool(st.SystemStatusFlag & 1), "secs_left": None if left == 0xFFFFFFFF else int(left)}


def fmt_dur(secs):
    secs = int(secs)
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m} min"


def battery_color(pct, charging):
    if charging:
        return QColor("#34d399")
    if pct <= 15:
        return QColor("#f87171")
    if pct <= 35:
        return QColor("#fbbf24")
    return QColor(TEXT)


class BatteryGraph(QWidget):
    def __init__(self, widget):
        super().__init__()
        self.w = widget
        self.setFixedHeight(64)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height() - 12
        p.setPen(QPen(QColor(*T["border"]), 1, Qt.DashLine))
        for lvl in (25, 50, 75):
            y = h - h * lvl / 100
            p.drawLine(0, int(y), w, int(y))
        pts = list(self.w.history)
        if len(pts) >= 2:
            now = time.time()
            span = max(600.0, now - pts[0][0])
            path = QPainterPath()
            for i, (t, v) in enumerate(pts):
                x = w * (1 - (now - t) / span)
                y = h - h * v / 100
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            fill = QPainterPath(path)
            fill.lineTo(w, h)
            fill.lineTo(w * (1 - (now - pts[0][0]) / span), h)
            fill.closeSubpath()
            c = QColor(self.bar_accent())
            c.setAlpha(55)
            p.fillPath(fill, c)
            p.setPen(QPen(QColor(self.bar_accent()), 1.6))
            p.drawPath(path)
            p.setPen(QColor(DIM))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            mins = int(span // 60)
            p.drawText(0, self.height() - 1, f"{mins // 60}h {mins % 60:02d}m ago" if mins >= 60 else f"{mins} min ago")
            p.drawText(w - 22, self.height() - 1, "now")
        else:
            p.setPen(QColor(DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "History builds up while OrbitBar runs")

    def bar_accent(self):
        return self.w.bar.cfg["accent"]


class BatteryCard(Card):
    def __init__(self, bar, widget):
        super().__init__(bar, width=300)
        self.w = widget
        top = QHBoxLayout()
        self.big = lbl("", "h1")
        col = QVBoxLayout()
        self.state = lbl("", "h2")
        self.sub = lbl("", "dim")
        col.addWidget(self.state)
        col.addWidget(self.sub)
        top.addWidget(self.big)
        top.addSpacing(10)
        top.addLayout(col, 1)
        self.body.addLayout(top)
        self.body.addWidget(section("Battery level"))
        self.graph = BatteryGraph(widget)
        self.body.addWidget(self.graph)
        self.rows = QVBoxLayout()
        self.body.addLayout(self.rows)
        btns = QHBoxLayout()
        for text, uri in (("Battery settings", "ms-settings:batterysaver"), ("Power && sleep", "ms-settings:powersleep")):
            b = text_button(text)
            b.clicked.connect(lambda _=False, u=uri: (self.hide(), os.startfile(u)))
            btns.addWidget(b)
        self.body.addLayout(btns)

    def refresh(self):
        d = self.w.data
        if not d:
            return
        self.big.setText(f"{d['percent']:.0f}%")
        if d["charging"]:
            self.state.setText("⚡ Charging")
            eta = self.w.eta_full()
            self.sub.setText(f"Full in about {fmt_dur(eta)}" if eta else "Estimating time to full…")
        elif d["plugged"]:
            self.state.setText("Plugged in")
            self.sub.setText("Fully charged" if d["percent"] >= 99.5 else "Not charging (battery care)")
        else:
            self.state.setText("On battery")
            self.sub.setText(f"About {fmt_dur(d['secs_left'])} left" if d.get("secs_left") else "Estimating time left…")
        clear_layout(self.rows)
        for k, v in (("Power source", "Charger" if d["plugged"] else "Battery"),
                     ("Battery saver", "On" if d["saver"] else "Off")):
            r = QHBoxLayout()
            r.addWidget(lbl(k, "dim"))
            r.addStretch(1)
            r.addWidget(lbl(v))
            self.rows.addLayout(r)
        self.graph.update()


class BatteryWidget(QWidget):
    """Battery icon (or ring) with a charging shimmer + bolt, low-battery pulse and alerts."""

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.cfg = bar.cfg.setdefault("battery", dict(DEFAULT_CONFIG["battery"]))
        self.source = read_battery
        self.data = None
        self.phase = 0.0
        self.history = deque(maxlen=480)       # (unix time, percent), >= 30 s apart -> ~4 h
        self.alerted = set()
        self.setFixedSize(66, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.card = BatteryCard(bar, self)
        self.poll = QTimer(self, interval=3000, timeout=self.update_status)
        self.poll.start()
        self.anim = QTimer(self, interval=60, timeout=self.step)
        self.update_status()

    @staticmethod
    def available():
        return read_battery() is not None

    # ---- data
    def update_status(self):
        d = self.source()
        if d is None:
            return
        prev, self.data = self.data, d
        now = time.time()
        if not self.history or now - self.history[-1][0] >= 30:
            self.history.append((now, d["percent"]))
        self.alerts(prev, d)
        self.setToolTip(self.tooltip())
        want_anim = (d["charging"] or self.is_low()) and self.isVisible()
        if want_anim and not self.anim.isActive():
            self.anim.start()
        elif not want_anim and self.anim.isActive():
            self.anim.stop()
        self.update()
        if self.card.isVisible():
            self.card.refresh()

    def is_low(self):
        d = self.data
        return bool(d and not d["plugged"] and d["percent"] <= self.cfg.get("low_alert", 20))

    def eta_full(self):
        """Time to full from the recent charge rate (Windows doesn't report it)."""
        pts = [(t, v) for t, v in self.history if time.time() - t <= 900]
        if len(pts) < 3 or not self.data:
            return None
        (t0, v0), (t1, v1) = pts[0], pts[-1]
        rate = (v1 - v0) / max(t1 - t0, 1)
        return (100 - self.data["percent"]) / rate if rate > 0 else None

    def tooltip(self):
        d = self.data
        if d["charging"]:
            eta = self.eta_full()
            state = "Charging" + (f", full in ~{fmt_dur(eta)}" if eta else "")
        elif d["plugged"]:
            state = "Plugged in, fully charged" if d["percent"] >= 99.5 else "Plugged in, not charging"
        else:
            state = "On battery" + (f", ~{fmt_dur(d['secs_left'])} left" if d.get("secs_left") else "")
        return f"Battery {d['percent']:.0f}% · {state}"

    def alerts(self, prev, d):
        if not prev:
            return
        if d["plugged"] != prev["plugged"]:
            self.alerted.clear()
        low = self.cfg.get("low_alert", 20)
        if not d["plugged"]:
            for lvl in (low, 10):
                if d["percent"] <= lvl < prev["percent"] and lvl not in self.alerted:
                    self.alerted.add(lvl)
                    self.bar.toast.notify("🪫 Battery low", f"{d['percent']:.0f}% left - plug in your charger.", self)
        elif self.cfg.get("full_alert", True) and d["percent"] >= 99.5 > prev["percent"] and "full" not in self.alerted:
            self.alerted.add("full")
            self.bar.toast.notify("🔋 Fully charged", "You can unplug the charger.", self)

    # ---- animation
    def step(self):
        self.phase = (self.phase + 0.035) % 1.0
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self.update_status()

    def hideEvent(self, e):
        self.anim.stop()            # no animation work while hidden (fullscreen, auto-hide)
        super().hideEvent(e)

    # ---- interaction
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.card.open_from(self)

    def contextMenuEvent(self, e):
        e.accept()
        m = QMenu(self)
        for style in ("icon", "ring"):
            a = m.addAction(f"{style.capitalize()} style")
            a.setCheckable(True)
            a.setChecked(self.cfg.get("style", "icon") == style)
            a.triggered.connect(lambda _=False, st=style: self.set_style(st))
        m.addSeparator()
        m.addAction("Battery settings").triggered.connect(lambda: os.startfile("ms-settings:batterysaver"))
        m.exec(self.bar.popup_pos(m.sizeHint(), e.globalPos()))

    def set_style(self, style):
        self.cfg["style"] = style
        save_config(self.bar.cfg)
        self.update()

    # ---- drawing
    def paintEvent(self, e):
        d = self.data
        if not d:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pct = max(0.0, min(100.0, d["percent"]))
        color = battery_color(pct, d["charging"] or (d["plugged"] and pct >= 99.5))
        style = self.cfg.get("style", "icon")
        if style == "ring":
            self.paint_ring(p, pct, color, d)
        elif style == "gauge":
            self.paint_gauge(p, pct, color, d)
        elif style == "dots":
            self.paint_dots(p, pct, color, d)
        else:
            self.paint_icon(p, pct, color, d)
        p.setPen(QColor(TEXT))
        f = QFont("Segoe UI Variable Text, Segoe UI")
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(QRectF(34, 0, self.width() - 34, self.height()), Qt.AlignVCenter | Qt.AlignLeft, f"{pct:.0f}%")

    def outline_pen(self):
        if self.is_low():           # pulse red when low
            import math
            a = int(140 + 115 * (0.5 + 0.5 * math.sin(self.phase * 2 * math.pi * 2)))
            c = QColor("#f87171")
            c.setAlpha(a)
            return QPen(c, 1.6)
        c = QColor(TEXT)
        c.setAlpha(170)
        return QPen(c, 1.3)

    def paint_icon(self, p, pct, color, d):
        body = QRectF(4, self.height() / 2 - 6.5, 25, 13)
        p.setPen(self.outline_pen())
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body, 3.5, 3.5)
        p.setPen(Qt.NoPen)
        c = QColor(TEXT)
        c.setAlpha(170)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(body.right() + 1, body.center().y() - 2.5, 2, 5), 1, 1)
        inner = body.adjusted(2, 2, -2, -2)
        fill = QRectF(inner.left(), inner.top(), max(1.5, inner.width() * pct / 100), inner.height())
        p.setBrush(color)
        p.drawRoundedRect(fill, 1.8, 1.8)
        if d["charging"]:
            # shimmer sweeping through the filled part
            from PySide6.QtGui import QLinearGradient
            x = fill.left() - 10 + self.phase * (fill.width() + 20)
            g = QLinearGradient(x - 8, 0, x + 8, 0)
            g.setColorAt(0, QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QColor(255, 255, 255, 170))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.save()
            p.setClipRect(fill)
            p.fillRect(fill, g)
            p.restore()
        if d["plugged"]:
            self.paint_bolt(p, body.center(), 1.0)

    def paint_gauge(self, p, pct, color, d):
        r = QRectF(3, self.height() / 2 - 4, 28, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*T["hover"]))
        p.drawRoundedRect(r, 4, 4)
        fill = QRectF(r.left(), r.top(), max(4.0, r.width() * pct / 100), r.height())
        p.setBrush(color)
        p.drawRoundedRect(fill, 4, 4)
        if d["charging"]:
            from PySide6.QtGui import QLinearGradient
            x = fill.left() - 8 + self.phase * (fill.width() + 16)
            g = QLinearGradient(x - 6, 0, x + 6, 0)
            g.setColorAt(0, QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QColor(255, 255, 255, 170))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.save()
            p.setClipRect(fill)
            p.fillRect(fill, g)
            p.restore()
        if self.is_low():
            p.setPen(self.outline_pen())
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 5, 5)
        if d["plugged"]:
            self.paint_bolt(p, QPoint(int(r.center().x()), int(r.center().y())), 0.75)

    def paint_dots(self, p, pct, color, d):
        lit = int(round(pct / 20))
        for i in range(5):
            c = QColor(color) if i < lit else QColor(*T["hover"])
            if d["charging"] and i == min(lit, 4):                 # next dot pulses while charging
                import math
                c = QColor(color)
                c.setAlpha(int(90 + 150 * (0.5 + 0.5 * math.sin(self.phase * 2 * math.pi))))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(3 + i * 6, self.height() / 2 - 2.5, 5, 5))
        if self.is_low():
            p.setPen(self.outline_pen())
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, self.height() / 2 - 5, 31, 10), 5, 5)

    def paint_ring(self, p, pct, color, d):
        r = QRectF(6, self.height() / 2 - 11, 22, 22)
        track = QColor(*T["hover"])
        p.setPen(QPen(track, 3.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        pen = QPen(color, 3.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, -int(360 * 16 * pct / 100))
        if d["charging"]:           # bright comet running around the filled arc
            c = QColor(255, 255, 255, 210)
            pen = QPen(c, 3.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            start = 90 - 360 * (pct / 100) * self.phase
            p.drawArc(r, int(start * 16), -int(28 * 16))
        if self.is_low():
            p.setPen(self.outline_pen())
            p.drawEllipse(r.adjusted(-2.5, -2.5, 2.5, 2.5))
        if d["plugged"]:
            self.paint_bolt(p, r.center(), 1.05)

    def paint_bolt(self, p, c, s):
        path = QPainterPath()
        pts = [(1.5, -7.5), (-4.0, 1.0), (-0.5, 1.0), (-1.5, 7.5), (4.0, -1.0), (0.5, -1.0)]
        path.moveTo(c.x() + pts[0][0] * s, c.y() + pts[0][1] * s)
        for x, y in pts[1:]:
            path.lineTo(c.x() + x * s, c.y() + y * s)
        path.closeSubpath()
        bg = QColor(*T["bg"])
        bg.setAlpha(255)
        p.setPen(QPen(bg, 1.6))
        p.setBrush(QColor("#fde047"))
        p.drawPath(path)



# ---- Security shield: antivirus + firewall --------------------------------------------
SECURITY_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$mp = Get-MpComputerStatus
$def = $null
if ($mp) {
  $q = $null; if ($mp.QuickScanEndTime) { $q = $mp.QuickScanEndTime.ToString('yyyy-MM-dd HH:mm') }
  $def = @{ rtp = [bool]$mp.RealTimeProtectionEnabled; av = [bool]$mp.AntivirusEnabled;
            tamper = [bool]$mp.IsTamperProtected; sigAge = [int]$mp.AntivirusSignatureAge;
            quickAge = [int]$mp.QuickScanAge; lastQuick = $q; sigVersion = [string]$mp.AntivirusSignatureVersion }
}
$av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct |
        ForEach-Object { @{ name = [string]$_.displayName; state = [int]$_.productState } })
$fw = @(Get-NetFirewallProfile | ForEach-Object { @{ name = [string]$_.Name; enabled = ([string]$_.Enabled -eq 'True') } })
@{ defender = $def; av = $av; fw = $fw } | ConvertTo-Json -Depth 4 -Compress
"""


def run_powershell(script, timeout=40):
    import base64
    import subprocess
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", enc], capture_output=True, text=True, timeout=timeout,
                       creationflags=0x08000000)          # CREATE_NO_WINDOW: no console flash
    return r.stdout.strip()


def decode_av_state(state):
    """SecurityCenter2 productState -> (enabled, up_to_date). e.g. 0x061100 = on + current."""
    h = f"{int(state):06x}"
    return h[2:4] in ("10", "11"), h[4:6] == "00"


def evaluate_security(raw):
    """Turns the raw PowerShell result into a verdict: level 'ok' | 'warn' | 'bad' | 'unknown'."""
    if not raw:
        return {"level": "unknown", "summary": "Couldn't read security status", "items": []}
    items, problems, warnings = [], [], []
    d = raw.get("defender") or None
    av_products = raw.get("av") or []
    active = []
    for p in av_products:
        on, current = decode_av_state(p.get("state", 0))
        items.append(("Antivirus", f"{p.get('name', '?')}: {'on' if on else 'off'}"
                                   + ("" if current or not on else ", definitions out of date"), on))
        if on:
            active.append(p.get("name", "?"))
            if not current:
                warnings.append(f"{p.get('name')} definitions out of date")
    if d:
        items.append(("Real-time protection", "On" if d.get("rtp") else "Off", bool(d.get("rtp"))))
        items.append(("Tamper protection", "On" if d.get("tamper") else "Off", bool(d.get("tamper"))))
        age = d.get("sigAge")
        if age is not None:
            items.append(("Virus definitions", "Today" if age == 0 else f"{age} day{'s' if age != 1 else ''} old",
                          age <= 3))
        if d.get("lastQuick"):
            qa = d.get("quickAge") or 0
            items.append(("Last quick scan", f"{d['lastQuick']}", qa <= 14))
        defender_active = bool(d.get("rtp") and d.get("av"))
        if not defender_active and active:      # another antivirus is in charge: Defender off is expected
            items[:] = [(n, (f"Off (handled by {active[0]})" if n == "Real-time protection" else v),
                         True if n in ("Real-time protection", "Tamper protection") else g) for n, v, g in items]
        if defender_active and "Windows Defender" not in active and "Microsoft Defender Antivirus" not in active:
            active.append("Microsoft Defender")
        if defender_active and age is not None and age > 3:
            warnings.append("virus definitions are old")
        if defender_active and not d.get("tamper"):
            warnings.append("tamper protection is off")
    if not active:
        problems.append("no antivirus is active")
    fw = raw.get("fw") or []
    other_fw = [p["name"] for p in (raw.get("fw_products") or []) if decode_av_state(p.get("state", 0))[0]
                and "windows" not in p.get("name", "").lower()]
    if other_fw:                         # e.g. Bitdefender/ESET firewall replaces Windows Firewall
        items.append(("Firewall", f"{other_fw[0]}: on", True))
        fw = []
    off = [p["name"] for p in fw if not p.get("enabled")]
    for p in fw:
        items.append((f"Firewall · {p['name']}", "On" if p.get("enabled") else "Off", bool(p.get("enabled"))))
    if fw and len(off) == len(fw):
        problems.append("the firewall is off")
    elif off:
        (problems if "Public" in off else warnings).append(f"firewall off for {', '.join(off)}")
    if problems:
        level, summary = "bad", "At risk: " + "; ".join(problems)
    elif warnings:
        level, summary = "warn", "Check: " + "; ".join(warnings)
    else:
        level, summary = "ok", "Protected · " + ", ".join(active) + (" · firewall on" if fw else "")
    return {"level": level, "summary": summary, "items": items}


def _cim_time(v):
    """WMI datetime '20261114091200.000000-300' -> '2026-11-14 09:12'."""
    v = str(v or "")
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]} {v[8:10]}:{v[10:12]}" if len(v) >= 12 and v[:8].isdigit() else None


def com_init():
    """Initialise COM on this thread. If it's already initialised (comtypes does that when imported),
    keep that mode: WMI works in either, so 'cannot change thread mode' is not an error."""
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass


def wmi_rows(namespace, query, fields):
    import comtypes.client
    svc = comtypes.client.CoGetObject("winmgmts:{impersonationLevel=impersonate}!\\\\.\\" + namespace, dynamic=True)
    out = []
    for row in svc.ExecQuery(query):
        item = {}
        for f in fields:
            try:                          # a property this Windows build / antivirus setup doesn't report
                item[f] = getattr(row, f, None)
            except Exception:
                item[f] = None
        out.append(item)
    return out


def firewall_from_registry():
    """Windows Firewall on/off per profile; Group Policy settings win over local ones."""
    out = []
    base = r"SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy"
    for name, key in (("Domain", "DomainProfile"), ("Private", "StandardProfile"), ("Public", "PublicProfile")):
        val = None
        for path in (rf"SOFTWARE\Policies\Microsoft\WindowsFirewall\{key.replace('Standard', 'Private')}",
                     rf"{base}\{key}"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                    val = winreg.QueryValueEx(k, "EnableFirewall")[0]
                    break
            except OSError:
                continue
        if val is not None:
            out.append({"name": name, "enabled": bool(val)})
    return out


def security_raw_direct():
    """Same data as the PowerShell script, read directly: WMI for Defender/antivirus, registry for the firewall."""
    d = None
    rows = wmi_rows(r"root\Microsoft\Windows\Defender", "SELECT * FROM MSFT_MpComputerStatus",
                    ["RealTimeProtectionEnabled", "AntivirusEnabled", "IsTamperProtected", "AntivirusSignatureAge",
                     "QuickScanAge", "QuickScanEndTime", "AntivirusSignatureVersion"])
    if rows:
        r = rows[0]
        if r["RealTimeProtectionEnabled"] is None:     # Defender passive / another antivirus: let PowerShell answer
            raise RuntimeError("Defender doesn't report RealTimeProtectionEnabled here")
        d = {"rtp": bool(r["RealTimeProtectionEnabled"]), "av": bool(r["AntivirusEnabled"]),
             "tamper": bool(r["IsTamperProtected"]), "sigAge": int(r["AntivirusSignatureAge"] or 0),
             "quickAge": int(r["QuickScanAge"] or 0), "lastQuick": _cim_time(r["QuickScanEndTime"]),
             "sigVersion": str(r["AntivirusSignatureVersion"] or "")}
    av = [{"name": str(r["displayName"]), "state": int(r["productState"] or 0)}
          for r in wmi_rows(r"root\SecurityCenter2", "SELECT displayName, productState FROM AntiVirusProduct",
                            ["displayName", "productState"])]
    try:
        fwp = [{"name": str(r["displayName"]), "state": int(r["productState"] or 0)}
               for r in wmi_rows(r"root\SecurityCenter2", "SELECT displayName, productState FROM FirewallProduct",
                                 ["displayName", "productState"])]
    except Exception:
        fwp = []
    return {"defender": d, "av": av, "fw": firewall_from_registry(), "fw_products": fwp, "method": "direct"}


class SecurityWorker(QThread):
    done = Signal(dict)
    direct_failed = False

    def run(self):
        raw = None
        if not SecurityWorker.direct_failed:
            try:                                 # fast path: no PowerShell window, ~10 ms
                com_init()
                raw = security_raw_direct()
            except Exception as ex:              # won't work on this PC: say so once, then go straight to PowerShell
                SecurityWorker.direct_failed = True
                print("direct security check unavailable, using PowerShell from now on:", ex)
        if raw is None:
            try:
                out = run_powershell(SECURITY_PS)
                raw = json.loads(out) if out else None
                if raw is not None:
                    raw["method"] = "powershell"
            except Exception as ex:
                print("security check failed:", ex)
                raw = None
        self.done.emit(evaluate_security(raw))


SHIELD_COLORS = {"ok": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444", "unknown": "#8a8a99"}


class SecurityCard(Card):
    def __init__(self, bar, widget):
        super().__init__(bar, width=320)
        self.w = widget
        self.title = lbl("", "h2")
        self.title.setWordWrap(True)
        self.body.addWidget(self.title)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        self.body.addLayout(self.rows)
        self.when = lbl("", "dim")
        self.body.addWidget(self.when)
        btns = QHBoxLayout()
        for text, fn in (("Windows Security", lambda: os.startfile("windowsdefender:")),
                         ("Firewall", lambda: os.startfile("windowsdefender://network")),
                         ("Check now", self.w.check)):
            b = text_button(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            btns.addWidget(b)
        self.body.addLayout(btns)

    def refresh(self):
        s = self.w.status
        if s is None:
            self.title.setText("Checking security status…")
            clear_layout(self.rows)
            return
        icon = {"ok": "🛡 ", "warn": "⚠ ", "bad": "⛔ ", "unknown": "? "}[s["level"]]
        self.title.setText(icon + s["summary"])
        clear_layout(self.rows)
        for name, value, good in s["items"]:
            r = QHBoxLayout()
            dot = lbl("●")
            dot.setStyleSheet(f"color: {SHIELD_COLORS['ok' if good else 'bad']}; font-size: 9px;")
            r.addWidget(dot)
            r.addWidget(lbl(name, "dim"))
            r.addStretch(1)
            r.addWidget(lbl(value))
            self.rows.addLayout(r)
        if self.w.checked_at:
            self.when.setText("Checked " + self.w.checked_at.strftime("%H:%M") + " · rechecks every 5 min")


class SecurityWidget(QWidget):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.status = None
        self.checked_at = None
        self.worker = None
        self.hover = False
        self.setFixedSize(uw(32), 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Checking security status…")
        self.card = SecurityCard(bar, self)
        self.timer = QTimer(self, interval=5 * 60 * 1000, timeout=self.check)
        self.timer.start()
        QTimer.singleShot(2500, self.check)

    def rescale(self):
        self.setFixedSize(uw(32), 36)
        self.update()

    def check(self):
        if self.worker and self.worker.isRunning():
            return
        self.worker = SecurityWorker()
        self.worker.done.connect(self.on_status)
        self.worker.start()

    def on_status(self, s):
        prev = self.status
        self.status, self.checked_at = s, datetime.now()
        self.setToolTip(s["summary"] + "\nClick for details")
        if (prev and prev["level"] == "ok" and s["level"] in ("warn", "bad")
                and self.bar.cfg.get("alerts", {}).get("security", True)):
            self.bar.toast.notify("⛔ Security alert" if s["level"] == "bad" else "⚠ Security warning",
                                  s["summary"], self)
        self.update()
        if self.card.isVisible():
            self.card.refresh()
            self.card.reposition()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.card.open_from(self)
            if not self.status:
                self.check()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*T["hover"]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 3, -1, -3), 8, 8)
        level = self.status["level"] if self.status else "unknown"
        color = QColor(SHIELD_COLORS[level])
        cx, cy = self.width() / 2, self.height() / 2
        w, h = 16.0 * SIZES["w"], 19.0 * SIZES["w"]
        x0, y0 = cx - w / 2, cy - h / 2
        shield = QPainterPath()
        shield.moveTo(cx, y0)
        shield.cubicTo(cx + w * 0.28, y0 + 2.2, x0 + w, y0 + 2.6, x0 + w, y0 + 2.6)
        shield.lineTo(x0 + w, y0 + h * 0.45)
        shield.cubicTo(x0 + w, y0 + h * 0.78, cx + 2, y0 + h - 1.2, cx, y0 + h)
        shield.cubicTo(cx - 2, y0 + h - 1.2, x0, y0 + h * 0.78, x0, y0 + h * 0.45)
        shield.lineTo(x0, y0 + 2.6)
        shield.cubicTo(x0, y0 + 2.6, cx - w * 0.28, y0 + 2.2, cx, y0)
        shield.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(shield)
        pen = QPen(QColor("white"), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        my = cy + 0.5
        if level == "ok":                                   # check mark
            p.drawPolyline([QPoint(int(cx - 4), int(my)), QPoint(int(cx - 1), int(my + 3)),
                            QPoint(int(cx + 4), int(my - 3))])
        elif level == "warn":                               # exclamation
            p.drawLine(QPoint(int(cx), int(my - 4)), QPoint(int(cx), int(my + 1)))
            p.drawPoint(QPoint(int(cx), int(my + 4)))
        elif level == "bad":                                # cross
            p.drawLine(QPoint(int(cx - 3), int(my - 3)), QPoint(int(cx + 3), int(my + 3)))
            p.drawLine(QPoint(int(cx + 3), int(my - 3)), QPoint(int(cx - 3), int(my + 3)))
        else:                                               # checking: three dots
            for dx in (-4, 0, 4):
                p.drawPoint(QPoint(int(cx + dx), int(my)))


# ---- Camera / microphone in-use indicator -----------------------------------------------
CONSENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"


def pretty_app(key, nonpackaged):
    if nonpackaged:                               # "C:#Program Files#Zoom#bin#Zoom.exe"
        name = key.replace("#", "\\").rsplit("\\", 1)[-1]
        return name[:-4] if name.lower().endswith(".exe") else name
    pkg = key.split("_")[0]                       # "Microsoft.WindowsCamera_8wekyb3d8bbwe"
    return pkg.split(".")[-1] or pkg


def _filetime_str(ft):
    if not ft:
        return "-"
    try:
        return (datetime(1601, 1, 1) + timedelta(microseconds=int(ft) // 10)).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OverflowError, ValueError):
        return str(ft)


def consent_entries(capability):
    """Every app Windows has recorded for 'webcam' / 'microphone', from both the per-user (HKCU)
    and machine-wide (HKLM) stores: [(app, start, stop, hive)]."""
    out = []

    def scan(root, hive, path, nonpackaged):
        try:
            k = winreg.OpenKey(root, path)
        except OSError:
            return
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    scan(root, hive, path + "\\" + sub, True)
                    continue
                try:
                    with winreg.OpenKey(k, sub) as sk:
                        try:
                            start = winreg.QueryValueEx(sk, "LastUsedTimeStart")[0]
                        except OSError:
                            continue
                        try:
                            stop = winreg.QueryValueEx(sk, "LastUsedTimeStop")[0]
                        except OSError:
                            stop = 0            # some builds omit "stop" while the device is in use
                except OSError:
                    continue
                out.append((pretty_app(sub, nonpackaged), start, stop, hive))

    for root, hive in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        scan(root, hive, CONSENT_KEY + "\\" + capability, False)
    return out


def apps_using(capability):
    """Apps using the device right now: started, and not stopped since."""
    users = {app for app, start, stop, _ in consent_entries(capability) if start and (not stop or stop < start)}
    return sorted(users, key=str.lower)


def calendar_report():
    """python orbitbar.py --check-calendar : what OrbitBar understood from your links and entries."""
    cfg = load_config()
    st = cfg.setdefault("study", {})
    for k, v in DEFAULT_STUDY.items():
        st.setdefault(k, v)
    print(f"OrbitBar {APP_VERSION} calendar check\n")
    links = [u for k in ("lms_links", "class_links") for u in (st.get(k) or "").splitlines() if u.strip()]
    print(f"Calendar links: {len(links)}")
    for u in links:
        print("   ", u[:60] + ("…" if len(u) > 60 else ""))
    w = StudySync(cfg)
    got = {}
    w.done.connect(lambda items, errors: got.update(items=items, errors=errors))
    w.run()                                    # run synchronously here
    items = got.get("items", [])
    items += parse_manual_dated(st.get("deadlines", ""), "deadline")
    items += parse_manual_dated(st.get("exams", ""), "exam")
    items += parse_timetable(st.get("timetable", ""))
    items = dedupe_items(items)
    for e in got.get("errors", []):
        print("  ⚠", e)
    now = local_now()
    for kind, title in (("class", "CLASSES (next 7 days)"), ("deadline", "DEADLINES"), ("exam", "EXAMS")):
        rows = [i for i in items if i["kind"] == kind and i["end"] >= now - timedelta(days=1)
                and i["when"] <= now + timedelta(days=7 if kind == "class" else 90)]
        print(f"\n== {title}: {len(rows)} ==")
        for i in rows[:25]:
            print(f"  {i['when']:%a %d %b %H:%M}  {fmt_left(i['when'] - now):>9}  {i['course'][:10]:10} {i['title'][:45]}"
                  f"  [{i['source']}]" + (f"  @ {i['room']}" if i["room"] else ""))
    if not items:
        print("\nNothing found. Add links or entries in Settings > Study.")


def media_report():
    """python orbitbar.py --check-media : every media session Windows reports, and which one OrbitBar picks."""
    import asyncio
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
    except Exception:
        try:
            from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        except Exception as ex:
            print("Windows media API not available:", ex)
            return
    names = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "PLAYING", 5: "paused"}

    async def go():
        mgr = await M.request_async()
        cur = mgr.get_current_session()
        w = MediaWorker()
        snaps = []
        for s in list(mgr.get_sessions()):
            snaps.append(await w._snapshot(s))
        print(f"OrbitBar {APP_VERSION}: {len(snaps)} media session(s)\n")
        for x in snaps:
            st = names.get(int(x["session"].get_playback_info().playback_status), "?")
            mark = " <- Windows' 'current'" if cur and x["aumid"] == cur.source_app_user_model_id and \
                x["title"] == (await cur.try_get_media_properties_async()).title else ""
            print(f"  [{st:7}] {x['app']:12} {x['title'][:60]!r}  next={'yes' if x['can_next'] else 'no'}{mark}")
        pick = choose_session(snaps, None)
        print("\nOrbitBar would show:", next((x['title'] for x in snaps if x['key'] == pick), "nothing"))

    asyncio.run(go())


def privacy_report():
    """python orbitbar.py --check-privacy : prints what OrbitBar can see, for troubleshooting."""
    print(f"OrbitBar {APP_VERSION} privacy check\n")
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:
        pass
    cam = CameraMonitor()
    time.sleep(2.0)
    print("== LIVE camera (Media Foundation sensor monitor) ==")
    if cam.apps() is None:
        print(f"  unavailable: {cam.error or 'no report received'}")
    else:
        print(f"  reports received: {cam.reports}")
        print("  streaming now:", ", ".join(f"{proc_name(p)} ({n})" for p, n in cam.streaming.items()) or "nothing")
    if cam.asked_iids:
        print("  callback was asked for:", ", ".join(cam.asked_iids))
    cam.stop()
    mics = mic_apps_live()
    print("\n== LIVE microphone (Core Audio sessions) ==")
    if mics is None:
        print("  unavailable:", getattr(mic_apps_live, "error", "pycaw/comtypes missing"))
    else:
        print("  recording now:", ", ".join(mics) or "nothing")
    print("\n== Registry history (older Windows builds) ==")
    for cap in ("webcam", "microphone"):
        rows = consent_entries(cap)
        print(f"\n== {cap}: {len(rows)} recorded apps ==")
        for app, start, stop, hive in sorted(rows, key=lambda r: -(r[1] or 0))[:15]:
            live = "IN USE" if start and (not stop or stop < start) else "      "
            print(f"  {live}  {app:28} {hive}  start {_filetime_str(start)}  stop {_filetime_str(stop)}")
    print("\nIN USE = what OrbitBar shows. Turn your camera on (e.g. Zoom video preview) and run this again.")




# ---- Live detection (works on new Windows 11 builds where the registry is no longer updated) ----
_name_cache = {}


def proc_name(pid):
    if pid in _name_cache:
        return _name_cache[pid]
    try:
        n = psutil.Process(pid).name()
    except (psutil.Error, OSError):
        n = f"pid {pid}"
    n = n[:-4] if n.lower().endswith(".exe") else n
    if len(_name_cache) > 500:
        _name_cache.clear()
    _name_cache[pid] = n
    return n


def mic_apps_live():
    """Apps with an *active* recording session on any microphone (Core Audio session API).
    Returns a list of app names, or None if the API isn't available."""
    try:
        import comtypes
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator
    except Exception:
        return None
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
        coll = enum.EnumAudioEndpoints(1, 1)                  # eCapture, DEVICE_STATE_ACTIVE
        names = set()
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            mgr = dev.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            mgr = mgr.QueryInterface(IAudioSessionManager2)
            sessions = mgr.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                if ctl.GetState() != 1:                       # AudioSessionStateActive
                    continue
                pid = ctl.GetProcessId()
                if pid and pid != os.getpid():
                    names.add(proc_name(pid))
        return sorted(names, key=str.lower)
    except Exception as ex:
        mic_apps_live.error = f"{ex.__class__.__name__}: {ex}"
        return None


# Camera: Media Foundation "sensor activity monitor" (Windows 10 1703+). We implement the
# IMFSensorActivitiesReportCallback COM object by hand with ctypes and read the reports through
# their vtables. Every call is checked; anything unexpected disables this method instead of crashing.
class _GUID(ctypes.Structure):
    _fields_ = [("raw", ctypes.c_ubyte * 16)]


def _iid(s):
    import uuid
    return bytes(uuid.UUID(s).bytes_le)


IID_IUnknown = _iid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = _iid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90")
IID_SensorReportCallback = _iid("DE5072EE-DBE3-46DC-8A87-B6F631194751")
E_NOINTERFACE = ctypes.c_long(0x80004002).value

_QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
_REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_ONREPORT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


class _CallbackVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("OnActivitiesReport", _ONREPORT)]


class _CallbackObj(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(_CallbackVtbl))]


def _vcall(ptr, index, argtypes=(), *args, restype=ctypes.c_long):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return fn(ptr, *args)


def _release(ptr):
    if ptr:
        _vcall(ptr, 2, restype=ctypes.c_ulong)


class CameraMonitor:
    """Keeps the set of processes currently streaming from any camera, pushed by Windows."""

    def __init__(self):
        import threading
        self.streaming = {}          # pid -> camera name
        self.ok = False
        self.error = None
        self.reports = 0
        self.asked_iids = []         # IIDs Windows asked our callback for (diagnostics)
        self._stop = threading.Event()
        self._vt = _CallbackVtbl(_QI(self._qi), _REF(lambda this: 1), _REF(lambda this: 1),
                                 _ONREPORT(self._on_report))
        self._obj = _CallbackObj(ctypes.pointer(self._vt))
        self._thread = threading.Thread(target=self._run, name="CameraMonitor", daemon=True)
        self._thread.start()

    def _qi(self, this, riid, ppv):
        iid = bytes(riid.contents.raw)
        if iid in (IID_IUnknown, IID_SensorReportCallback, IID_IAgileObject):
            ppv[0] = this
            return 0
        import uuid
        self.asked_iids.append(str(uuid.UUID(bytes_le=iid)).upper())
        ppv[0] = None
        return E_NOINTERFACE

    def _on_report(self, this, report):
        try:
            found = {}
            cnt = ctypes.c_ulong()
            if _vcall(report, 3, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(cnt)) != 0:
                return 0
            for i in range(cnt.value):
                dev = ctypes.c_void_p()
                if _vcall(report, 4, (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)), i, ctypes.byref(dev)) != 0:
                    continue
                try:
                    buf = ctypes.create_unicode_buffer(256)
                    written = ctypes.c_ulong()
                    _vcall(dev, 3, (ctypes.c_wchar_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)),
                           ctypes.cast(buf, ctypes.c_wchar_p), 256, ctypes.byref(written))
                    cam = buf.value or "Camera"
                    pc = ctypes.c_ulong()
                    if _vcall(dev, 5, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(pc)) != 0:
                        continue
                    for j in range(min(pc.value, 64)):
                        pa = ctypes.c_void_p()
                        if _vcall(dev, 6, (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)), j,
                                  ctypes.byref(pa)) != 0:
                            continue
                        try:
                            pid = ctypes.c_ulong()
                            st = (ctypes.c_ubyte * 16)()          # roomy buffer: never overflow
                            if _vcall(pa, 3, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(pid)) != 0:
                                continue
                            if _vcall(pa, 4, (ctypes.c_void_p,), ctypes.cast(st, ctypes.c_void_p)) != 0:
                                continue
                            val = int.from_bytes(bytes(st[:4]), "little")
                            if val not in (0, 1):              # not a BOOL -> layout isn't what we expect
                                self.error = "unexpected sensor report layout"
                                self.ok = False
                                return 0
                            if val:
                                found[pid.value] = cam
                        finally:
                            _release(pa)
                finally:
                    _release(dev)
            self.streaming = found
            self.reports += 1
        except Exception as ex:
            self.error = f"{ex.__class__.__name__}: {ex}"
        return 0

    def _run(self):
        mon = ctypes.c_void_p()
        try:
            ole32 = ctypes.WinDLL("ole32")
            ole32.CoInitializeEx(None, 0)                         # MTA
            mfplat = ctypes.WinDLL("mfplat")
            mfplat.MFStartup(ctypes.c_ulong(0x00020070), ctypes.c_ulong(0))
            sg = ctypes.WinDLL("mfsensorgroup")
            sg.MFCreateSensorActivityMonitor.restype = ctypes.c_long
            sg.MFCreateSensorActivityMonitor.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            hr = sg.MFCreateSensorActivityMonitor(ctypes.byref(self._obj), ctypes.byref(mon))
            if hr != 0 or not mon:
                self.error = f"MFCreateSensorActivityMonitor failed (0x{hr & 0xFFFFFFFF:08X})"
                return
            hr = _vcall(mon, 3)                                    # IMFSensorActivityMonitor::Start
            if hr != 0:
                self.error = f"Start failed (0x{hr & 0xFFFFFFFF:08X})"
                return
            self.ok = True
            self._stop.wait()
            _vcall(mon, 4)                                         # Stop
        except Exception as ex:
            self.error = f"{ex.__class__.__name__}: {ex}"
            self.ok = False
        finally:
            _release(mon.value if mon else None)

    def apps(self):
        """App names streaming from a camera right now, or None if this method isn't working."""
        if not self.ok or self.error:
            return None
        return sorted({proc_name(pid) for pid in self.streaming if pid and pid != os.getpid()}, key=str.lower)

    def stop(self):
        self._stop.set()


class PrivacyWorker(QThread):
    changed = Signal(list, list)       # camera apps, microphone apps

    def run(self):
        try:
            import comtypes
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        self.cam = CameraMonitor()
        last = None
        n = 0
        reg_cam, reg_mic = set(), set()
        while not self.isInterruptionRequested():
            try:
                live_cam, live_mic = self.cam.apps(), mic_apps_live()
                # The registry history is a large scan and only matters on older Windows builds: read it every
                # ~6 s while the live detectors work, every round when they don't.
                if n % 4 == 0 or live_cam is None:
                    reg_cam = set(apps_using("webcam"))
                if n % 4 == 0 or live_mic is None:
                    reg_mic = set(apps_using("microphone"))
                n += 1
                cams = set(live_cam or []) | reg_cam
                mics = set(live_mic or []) | reg_mic
                now = (sorted(cams, key=str.lower), sorted(mics, key=str.lower))
            except Exception:
                now = ([], [])
            if now != last:
                last = now
                self.changed.emit(*now)
            for _ in range(15):                  # every ~1.5 s, quick to stop
                if self.isInterruptionRequested():
                    self.cam.stop()
                    return
                self.msleep(100)
        self.cam.stop()


class PrivacyCard(Card):
    def __init__(self, bar, widget):
        super().__init__(bar, width=290)
        self.w = widget
        self.rows = QVBoxLayout()
        self.body.addLayout(self.rows)
        btns = QHBoxLayout()
        for text, uri in (("Camera privacy", "ms-settings:privacy-webcam"),
                          ("Microphone privacy", "ms-settings:privacy-microphone")):
            b = text_button(text)
            b.clicked.connect(lambda _=False, u=uri: (self.hide(), os.startfile(u)))
            btns.addWidget(b)
        self.body.addLayout(btns)

    def refresh(self):
        clear_layout(self.rows)
        for title, apps, color in (("Camera", self.w.cams, PrivacyWidget.CAM),
                                   ("Microphone", self.w.mics, PrivacyWidget.MIC)):
            h = lbl(f"● {title} {'in use' if apps else 'not in use'}", "h2")
            if apps:
                h.setStyleSheet(f"color: {color};")
            self.rows.addWidget(h)
            for a in apps:
                self.rows.addWidget(lbl("   " + a))


class PrivacyWidget(QWidget):
    """Shows only while an app is using the camera or microphone (green = camera, orange = mic)."""
    CAM, MIC = "#22c55e", "#f59e0b"

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.cams, self.mics = [], []
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(36)
        self.card = PrivacyCard(bar, self)
        self.hide()
        self.worker = PrivacyWorker()
        self.worker.changed.connect(self.on_change)
        self.worker.start()

    def on_change(self, cams, mics):
        new = [("📷 Camera turned on", a) for a in cams if a not in self.cams] + \
              [("🎙 Microphone turned on", a) for a in mics if a not in self.mics]
        first_run = not hasattr(self, "_seen")
        self._seen = True
        self.cams, self.mics = cams, mics
        if new and not first_run and self.bar.cfg.get("alerts", {}).get("privacy", True):
            title, app = new[0]
            self.bar.toast.notify(title, f"{app} is using it." + (f"  (+{len(new) - 1} more)" if len(new) > 1 else ""),
                                  self if self.isVisible() else self.bar)
        parts = []
        if cams:
            parts.append("Camera: " + ", ".join(cams))
        if mics:
            parts.append("Microphone: " + ", ".join(mics))
        self.setToolTip("\n".join(parts))
        self.setFixedWidth(uw(14 + 26 * (bool(cams) + bool(mics))))
        self.setVisible(bool(cams or mics))
        self.update()
        if self.card.isVisible():
            self.card.refresh()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.card.open_from(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 5, -1, -5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*T["pill"]))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(13)
        p.setFont(f)
        x = 7.0
        for active, glyph, color in ((self.cams, "", self.CAM), (self.mics, "", self.MIC)):
            if not active:
                continue
            c = QColor(color)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(x, self.height() / 2 - 11, 22, 22))
            p.setPen(QColor("white"))
            p.drawText(QRectF(x, self.height() / 2 - 11, 22, 22), Qt.AlignCenter, glyph)
            x += 26

    def stop(self):
        self.worker.requestInterruption()
        self.worker.wait(1500)


# ---- Instant window tracking (WinEvent hooks instead of fast polling) -------------------
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, ctypes.c_long, ctypes.c_long,
                                  wt.DWORD, wt.DWORD)
_sig(user32.SetWinEventHook, wt.HANDLE, wt.DWORD, wt.DWORD, wt.HMODULE, WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD)
_sig(user32.UnhookWinEvent, wt.BOOL, wt.HANDLE)

# (first, last) event ranges we care about; LOCATIONCHANGE is deliberately excluded (fires on every mouse move)
WIN_EVENT_RANGES = [
    (0x0003, 0x0003),   # EVENT_SYSTEM_FOREGROUND
    (0x0016, 0x0017),   # EVENT_SYSTEM_MINIMIZESTART / MINIMIZEEND
    (0x8001, 0x8003),   # EVENT_OBJECT_DESTROY / SHOW / HIDE
    (0x800C, 0x800C),   # EVENT_OBJECT_NAMECHANGE (window titles)
    (0x8017, 0x8018),   # EVENT_OBJECT_CLOAKED / UNCLOAKED (virtual desktop switches, UWP apps)
]


class WindowEvents:
    """Windows tells us when windows open, close, change title or focus; we refresh right away
    (debounced) instead of re-scanning every 0.7 s."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.hooks = []
        self.proc = WINEVENTPROC(self._cb)        # keep a reference or Windows calls freed memory
        for first, last in WIN_EVENT_RANGES:
            h = user32.SetWinEventHook(first, last, None, self.proc, 0, 0, 0x0000 | 0x0002)  # OUTOFCONTEXT|SKIPOWNPROCESS
            if h:
                self.hooks.append(h)

    def _cb(self, hook, event, hwnd, id_object, id_child, thread, time_ms):
        if id_object != 0 or id_child != 0 or not hwnd:          # OBJID_WINDOW, the window itself
            return
        if event == 0x800C and user32.GetAncestor(hwnd, 2) != hwnd:   # title change of a child control
            return
        try:
            self.on_change(event, hwnd)
        except Exception as ex:                                  # never let an error escape into Windows
            print("window event error:", ex)

    @property
    def active(self):
        return bool(self.hooks)

    def stop(self):
        for h in self.hooks:
            user32.UnhookWinEvent(h)
        self.hooks = []


# ---- Settings window -----------------------------------------------------------------
class NoWheelCombo(QComboBox):
    """A dropdown that ignores the mouse wheel unless you clicked it first. Scrolling down the Settings page
    used to change whatever dropdown passed under the mouse (e.g. Bar widgets jumping to Huge 150 %)."""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()                   # let the page scroll instead


class NoWheelSpin(QSpinBox):
    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


ACCENTS = ["#7c9cff", "#a78bfa", "#f472b6", "#fb7185", "#f59e0b", "#34d399", "#22d3ee", "#e5e7eb"]


class SettingsWindow(QWidget):
    def __init__(self, bar):
        super().__init__(None, Qt.Window)
        self.bar = bar
        self.cfg = json.loads(json.dumps(bar.cfg))          # edit a copy; Save applies it
        self.setWindowTitle("OrbitBar Settings")
        self.resize(500, 700)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("settingsPage")
        self.page, self.scroll, self._fitted = page, scroll, False
        self.form = QVBoxLayout(page)
        self.form.setContentsMargins(22, 18, 22, 18)
        self.form.setSpacing(8)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        self.build()
        foot = QFrame()
        foot.setObjectName("settingsFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(16, 10, 16, 12)
        cfgbtn = text_button("Open config file", "link")
        cfgbtn.clicked.connect(lambda: os.startfile(CONFIG_PATH))
        cancel = text_button("Cancel")
        cancel.clicked.connect(self.close)
        save = text_button("Save && apply", "primary")
        save.clicked.connect(self.save)
        fl.addWidget(cfgbtn)
        fl.addStretch(1)
        fl.addWidget(cancel)
        fl.addWidget(save)
        outer.addWidget(foot)
        bar.restyle(self)
        a = bar.cfg["accent"]
        self.setStyleSheet(self.styleSheet() + f"""
            QWidget#settingsPage, SettingsWindow, QScrollArea {{ background: {rgba(T['card'][:3] + (255,))}; }}
            QFrame#settingsFoot {{ background: {rgba(T['card'][:3] + (255,))}; border-top: 1px solid {rgba(T['border'])}; }}
            QComboBox {{ background: {rgba(T['pill'])}; border: 1px solid {rgba(T['border'])}; border-radius: 8px;
                         padding: 5px 8px; min-width: 150px; }}
            QComboBox QAbstractItemView {{ background: {rgba(T['card'][:3] + (255,))}; selection-background-color: {a}; }}
            QCheckBox {{ spacing: 8px; padding: 3px 0; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {rgba(T['border'])};
                                    background: {rgba(T['pill'])}; }}
            QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}
            QPlainTextEdit {{ background: {rgba(T['pill'])}; border: 1px solid {rgba(T['border'])}; border-radius: 8px;
                              padding: 4px; }}
            QPushButton[class="primary"] {{ background: {a}; color: #0d0d14; font-weight: 600; padding: 7px 14px; }}
            QPushButton[class="swatch"] {{ border-radius: 11px; min-width: 22px; max-width: 22px; min-height: 22px;
                                           max-height: 22px; padding: 0; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {rgba(T['hover'])}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted:                  # once styles and font sizes are known: open wide enough for every row
            self._fitted = True
            QTimer.singleShot(0, self.fit_to_content)

    def fit_to_content(self):
        """Open as wide as the widest row needs (bigger text sizes need more room), never wider or taller than
        the screen. Before, it always opened 500 px wide and the right side of the rows was cut off."""
        self.page.adjustSize()
        need_w = self.page.minimumSizeHint().width()
        need_w = max(need_w, self.page.sizeHint().width())
        sb = self.scroll.verticalScrollBar().sizeHint().width()
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        w = min(max(520, need_w + sb + 12), int(scr.width() * 0.9))
        h = min(max(self.height(), 700), int(scr.height() * 0.88))
        self.resize(w, h)
        self.setMinimumWidth(min(need_w + sb + 12, int(scr.width() * 0.9)))
        g = self.frameGeometry()
        g.moveCenter(scr.center())            # keep it on screen after growing
        self.move(g.topLeft())

    # ---- helpers
    def head(self, text):
        self.form.addWidget(section(text))

    def row(self, label, widget):
        r = QHBoxLayout()
        r.addWidget(lbl(label))
        r.addStretch(1)
        r.addWidget(widget)
        self.form.addLayout(r)
        return widget

    def combo(self, label, options, current):
        c = NoWheelCombo()
        for text, value in options:
            c.addItem(text, value)
        idx = [v for _, v in options].index(current) if current in [v for _, v in options] else 0
        c.setCurrentIndex(idx)
        return self.row(label, c)

    def check(self, label, value):
        c = QCheckBox(label)
        c.setChecked(bool(value))
        self.form.addWidget(c)
        return c

    def spin(self, label, value, lo, hi, suffix=""):
        s = NoWheelSpin()
        s.setRange(lo, hi)
        s.setValue(int(value))
        s.setSuffix(suffix)
        return self.row(label, s)

    # ---- build
    def build(self):
        c = self.cfg
        title = lbl("OrbitBar", "h1")
        self.form.addWidget(title)
        self.form.addWidget(lbl(f"{APP_NAME} {APP_VERSION} by {APP_PUBLISHER} · changes apply instantly when you press Save.", "dim"))

        self.head("Appearance")
        self.theme = self.combo("Style", [("Glass", "glass"), ("Midnight", "midnight"), ("Black", "black"),
                                          ("White", "white")], c.get("theme", "midnight"))
        pill = c.get("margin", 0) > 0
        self.shape = self.combo("Shape", [("Floating pill", "pill"), ("Full width", "full")], "pill" if pill else "full")
        self.position = self.combo("Position", [("Bottom", "bottom"), ("Top", "top")], c.get("position", "bottom"))
        self.height_spin = self.spin("Height", c.get("height", 56), 40, 96, " px")
        self.scale = self.combo("Size of everything (restarts the bar)", [("Compact (90%)", 0.9), ("Normal", 1.0), ("Large (115%)", 1.15),
                                                       ("Extra large (130%)", 1.3), ("Huge (150%)", 1.5)],
                                min((0.9, 1.0, 1.15, 1.3, 1.5), key=lambda v: abs(v - float(c.get("scale", 1.0)))))
        self.icon_size = self.spin("App icon size", c.get("icon_size", 24), 16, 48, " px")
        near = lambda v: min((x for _, x in SIZE_CHOICES), key=lambda x: abs(x - float(v)))  # noqa: E731
        self.widget_size = self.combo("Bar widgets (weather, stats, volume…)", SIZE_CHOICES,
                                      near(c.get("widget_size", 1.15)))
        self.card_size = self.combo("Cards, calendar & notifications", SIZE_CHOICES, near(c.get("card_size", 1.15)))
        tip = lbl("Bigger sizes fit fewer widgets. If the app icons drift off-centre, remove a widget or two "
                  "in 🧩 Customize widgets. The bar grows taller by itself if the icons need it.", "dim")
        tip.setWordWrap(True)
        self.form.addWidget(tip)
        sw = QHBoxLayout()
        sw.setSpacing(6)
        self.accent = c.get("accent", ACCENTS[0])
        self.swatches = []
        for col in ACCENTS:
            b = QPushButton()
            b.setProperty("class", "swatch")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, col=col: self.set_accent(col))
            self.swatches.append((b, col))
            sw.addWidget(b)
        custom = text_button("Custom…", "link")
        custom.clicked.connect(self.pick_accent)
        sw.addWidget(custom)
        r = QHBoxLayout()
        r.addWidget(lbl("Accent"))
        r.addStretch(1)
        r.addLayout(sw)
        self.form.addLayout(r)
        self.paint_swatches()

        self.head("Behavior")
        self.autohide = self.check("Auto-hide (show when the mouse touches the screen edge)", c.get("autohide"))
        self.previews = self.check("Live window previews on hover", c.get("previews", True))
        self.fullscreen = self.check("Step aside for fullscreen games and video", c.get("hide_on_fullscreen", True))
        self.hide_tb = self.check("Hide the Windows taskbar while OrbitBar runs", c.get("hide_windows_taskbar", True))
        self.autostart = self.check("Start OrbitBar when I sign in to Windows", starts_with_windows())

        self.head("Widgets")
        cust = text_button("🧩 Customize widgets…  (add, remove, reorder, styles)", "chip")
        cust.clicked.connect(lambda: (self.close(), self.bar.open_gallery()))
        self.form.addWidget(cust)

        self.head("Alerts")
        al = c.get("alerts", {})
        self.alert_sec = self.check("Pop up when security protection turns off", al.get("security", True))
        self.alert_priv = self.check("Pop up when an app turns on the camera or microphone", al.get("privacy", True))
        self.alert_net = self.check("Pop up on open Wi-Fi or when your VPN disconnects", al.get("network", True))
        self.updates = self.check("Tell me when a new version is on GitHub (checks twice a day)",
                                  c.get("check_updates", True))
        self.auto_update = self.check("Download new versions by themselves and install them the next time "
                                      "OrbitBar starts", c.get("auto_update", True))
        if not getattr(sys, "frozen", False):
            self.auto_update.setEnabled(False)
            self.auto_update.setToolTip("Only for the downloaded OrbitBar.exe. Running from source: use git pull.")

        self.head("Clock & calendar")
        self.h24 = self.check("24-hour clock", c.get("clock_24h", True))
        self.sunday = self.check("Week starts on Sunday", c.get("week_starts_sunday", True))
        self.form.addWidget(lbl("World clocks — one per line: Name = Area/City", "dim"))
        self.clocks = QPlainTextEdit("\n".join(f"{n} = {z}" for n, z in c.get("world_clocks", [])))
        self.clocks.setFixedHeight(76)
        self.form.addWidget(self.clocks)

        self.head("Weather")
        w = c.get("weather", {})
        self.city = QLineEdit(w.get("city", "Toronto"))
        self.city.setPlaceholderText('City, or "auto"')
        self.row("City", self.city)
        self.units = self.combo("Units", [("Celsius", "celsius"), ("Fahrenheit", "fahrenheit")], w.get("units", "celsius"))
        self.provider = self.combo("Try first", [("Automatic", "auto"), ("Open-Meteo", "open-meteo"),
                                                 ("wttr.in", "wttr.in"), ("MET Norway", "met-norway")],
                                   w.get("provider", "auto"))

        self.head("Battery")
        b = c.get("battery", {})
        self.bstyle = self.combo("Style", [("Icon", "icon"), ("Ring", "ring")], b.get("style", "icon"))
        self.blow = self.spin("Low battery alert at", b.get("low_alert", 20), 5, 50, " %")
        self.bfull = self.check("Notify when fully charged", b.get("full_alert", True))

        self.head("Study")
        st = c.setdefault("study", dict(DEFAULT_STUDY))
        for k, v in DEFAULT_STUDY.items():
            st.setdefault(k, v)

        def box(label, key, placeholder, height=64):
            hint = lbl(label, "dim")
            hint.setWordWrap(True)
            self.form.addWidget(hint)
            e = QPlainTextEdit(st.get(key, ""))
            e.setPlaceholderText(placeholder)
            e.setFixedHeight(height)
            self.form.addWidget(e)
            return e
        self.lms = box("School calendar links: Moodle, Brightspace/D2L, Canvas, Blackboard, Schoology, "
                       "Google Classroom, Outlook (any .ics link, one per line)",
                       "lms_links", "Moodle: Calendar > Export > Get calendar URL\n"
                       "D2L: Calendar > Settings > Enable feeds > Subscribe · Canvas: Calendar Feed\n"
                       "Blackboard: Calendar > Share calendar")
        self.clinks = box("Class schedule calendar links (.ics, optional)", "class_links",
                          "https://calendar.google.com/calendar/ical/.../basic.ics")
        self.tt = box("Timetable — Days Start-End | Course | Room", "timetable",
                      "Mon,Wed 10:00-11:50 | CYB404 | B2034\nThu 18:00-20:50 | CYT145 | Online", 76)
        self.dls = box("Deadlines — Date Time | Title | Course", "deadlines",
                       "2026-11-20 23:59 | Lab 4 | CYT145\n2026-11-25 | Project proposal")
        self.exs = box("Exams — Date Time | Title | Course", "exams", "2026-12-10 09:00 | Final exam | CYB404")
        self.track = self.check("Track study time (stays on this PC)",
                                st.get("track_time", True))
        self.fmin = self.spin("Focus length", st.get("focus_minutes", 25), 5, 120, " min")
        self.bmin = self.spin("Break length", st.get("break_minutes", 5), 1, 60, " min")
        self.strict = self.check("Strict focus: minimize distracting apps, ask about websites", st.get("strict", False))
        self.dsites = QLineEdit(st.get("distraction_sites", ""))
        self.row("Distracting sites", self.dsites).setMinimumWidth(260)
        self.dapps = QLineEdit(st.get("distraction_apps", ""))
        self.row("Distracting apps", self.dapps).setMinimumWidth(260)
        self.ssites = QLineEdit(st.get("study_sites", ""))
        self.row("Study sites", self.ssites).setMinimumWidth(260)
        self.sapps = QLineEdit(st.get("study_apps", ""))
        self.row("Study apps", self.sapps).setMinimumWidth(260)

        self.head("Timer")
        self.presets = QLineEdit(", ".join(f"{n} {m}" for n, m in c.get("timer_presets", [])))
        self.presets.setPlaceholderText("Focus 25, Break 5, Long break 15")
        self.form.addWidget(self.presets)
        self.form.addStretch(1)

    def set_accent(self, col):
        self.accent = col
        self.paint_swatches()

    def pick_accent(self):
        from PySide6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(QColor(self.accent), self, "Accent colour")
        if col.isValid():
            self.set_accent(col.name())

    def paint_swatches(self):
        for b, col in self.swatches:
            ring = f"3px solid {TEXT}" if col.lower() == self.accent.lower() else f"1px solid {rgba(T['border'])}"
            b.setStyleSheet(f"background: {col}; border: {ring};")

    # ---- save
    def collect(self):
        clocks = []
        for line in self.clocks.toPlainText().splitlines():
            if "=" in line:
                n, z = line.split("=", 1)
                if n.strip() and z.strip():
                    clocks.append([n.strip(), z.strip()])
        presets = []
        for part in self.presets.text().split(","):
            bits = part.strip().rsplit(" ", 1)
            if len(bits) == 2 and bits[1].isdigit():
                presets.append([bits[0].strip(), int(bits[1])])
        pill = self.shape.currentData() == "pill"
        return dict(
            theme=self.theme.currentData(), accent=self.accent, position=self.position.currentData(),
            height=self.height_spin.value(), margin=5 if pill else 0, radius=16 if pill else 0,
            scale=self.scale.currentData(), icon_size=self.icon_size.value(),
            widget_size=self.widget_size.currentData(), card_size=self.card_size.currentData(),
            autohide=self.autohide.isChecked(), previews=self.previews.isChecked(),
            hide_on_fullscreen=self.fullscreen.isChecked(), hide_windows_taskbar=self.hide_tb.isChecked(),
            check_updates=self.updates.isChecked(), auto_update=self.auto_update.isChecked(),
            alerts={"security": self.alert_sec.isChecked(), "privacy": self.alert_priv.isChecked(),
                    "network": self.alert_net.isChecked()},
            clock_24h=self.h24.isChecked(), week_starts_sunday=self.sunday.isChecked(),
            world_clocks=clocks or self.cfg.get("world_clocks", []),
            weather={"city": self.city.text().strip() or "auto", "units": self.units.currentData(),
                     "provider": self.provider.currentData()},
            battery={"style": self.bstyle.currentData(), "low_alert": self.blow.value(),
                     "full_alert": self.bfull.isChecked()},
            timer_presets=presets or self.cfg.get("timer_presets", []),
            study={"lms_links": self.lms.toPlainText().strip(), "class_links": self.clinks.toPlainText().strip(),
                   "timetable": self.tt.toPlainText().strip(), "deadlines": self.dls.toPlainText().strip(),
                   "exams": self.exs.toPlainText().strip(), "track_time": self.track.isChecked(),
                   "focus_minutes": self.fmin.value(), "break_minutes": self.bmin.value(),
                   "strict": self.strict.isChecked(), "distraction_sites": self.dsites.text(),
                   "distraction_apps": self.dapps.text(), "study_sites": self.ssites.text(),
                   "study_apps": self.sapps.text()},
        )

    def save(self):
        if self.autostart.isChecked() != starts_with_windows():
            set_start_with_windows(self.autostart.isChecked())
        changes = self.collect()
        self.close()
        self.bar.set_cfg(**changes)

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()
        hwnd = int(self.winId())
        if T.get("text", "#fff").lower() not in ("#1c1c26",):          # dark title bar for dark styles
            on = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
        user32.SwitchToThisWindow(hwnd, True)



# ---- System tray (notification area) host ------------------------------------------------
# How it works: apps add tray icons with Shell_NotifyIcon, which finds the window of class
# "Shell_TrayWnd" and sends it WM_COPYDATA. We create our own Shell_TrayWnd, keep it above
# Explorer's in z-order so it is found first, then broadcast "TaskbarCreated" so running apps
# re-add their icons. Every message is also forwarded to Explorer's real taskbar, so if
# OrbitBar quits or crashes, nothing is lost.
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wt.DWORD), ("lpData", ctypes.c_void_p)]


class NOTIFYICONDATA32(ctypes.Structure):
    """NOTIFYICONDATAW as the shell transports it: handles are always 32-bit, strings UTF-16."""
    _fields_ = [("cbSize", ctypes.c_uint32), ("hWnd", ctypes.c_uint32), ("uID", ctypes.c_uint32),
                ("uFlags", ctypes.c_uint32), ("uCallbackMessage", ctypes.c_uint32), ("hIcon", ctypes.c_uint32),
                ("szTip", ctypes.c_uint16 * 128), ("dwState", ctypes.c_uint32), ("dwStateMask", ctypes.c_uint32),
                ("szInfo", ctypes.c_uint16 * 256), ("uVersion", ctypes.c_uint32),
                ("szInfoTitle", ctypes.c_uint16 * 64), ("dwInfoFlags", ctypes.c_uint32),
                ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", ctypes.c_uint32)]


class SHELLTRAYDATA(ctypes.Structure):
    _fields_ = [("dwSignature", ctypes.c_uint32), ("dwMessage", ctypes.c_uint32), ("nid", NOTIFYICONDATA32)]


class WINNOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("dwMagic", ctypes.c_uint32), ("dwMessage", ctypes.c_uint32), ("cbSize", ctypes.c_uint32),
                ("dwPadding", ctypes.c_uint32), ("hWnd", ctypes.c_uint32), ("uID", ctypes.c_uint32),
                ("guidItem", ctypes.c_ubyte * 16)]


NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETFOCUS, NIM_SETVERSION = 0, 1, 2, 3, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_STATE, NIF_GUID = 0x1, 0x2, 0x4, 0x8, 0x20
NIS_HIDDEN = 0x1
WM_COPYDATA = 0x004A
NIN_SELECT, NIN_KEYSELECT, WM_CONTEXTMENU_MSG = 0x400, 0x401, 0x007B


def _wstr(arr):
    raw = bytes(arr)
    return raw.decode("utf-16-le", "ignore").split("\x00", 1)[0]


def parse_tray_data(raw):
    """bytes of SHELLTRAYDATA -> (message, NOTIFYICONDATA32) or None. Short (older) structs are padded."""
    if len(raw) < 8:
        return None
    buf = raw[:ctypes.sizeof(SHELLTRAYDATA)].ljust(ctypes.sizeof(SHELLTRAYDATA), b"\x00")
    d = SHELLTRAYDATA.from_buffer_copy(buf)
    if d.dwSignature != 0x34753423:
        return None
    return d.dwMessage, d.nid


class TrayItem:
    def __init__(self, key):
        self.key = key
        self.hwnd = self.uid = self.cbmsg = 0
        self.version = 0
        self.tip = ""
        self.pixmap = None
        self.hidden = False
        self.pid = 0
        self.exe = ""

    @property
    def title(self):
        return self.tip or self.exe or "App"


class TrayStore:
    """Pure bookkeeping of tray icons (no Win32), so it can be unit-tested."""

    def __init__(self, icon_loader=None, pid_of=None, exe_of=None):
        self.items = OrderedDict()
        self.icon_loader = icon_loader or (lambda h: None)
        self.pid_of = pid_of or (lambda h: 0)
        self.exe_of = exe_of or (lambda pid: "")

    @staticmethod
    def key_of(nid):
        guid = bytes(nid.guidItem)
        if nid.uFlags & NIF_GUID and any(guid):
            return ("guid", guid)
        return ("id", nid.hWnd, nid.uID)

    def find(self, nid):
        k = self.key_of(nid)
        if k in self.items:
            return self.items[k]
        # an app may add by hWnd/uID and later modify with a GUID (or the reverse)
        for it in self.items.values():
            if it.hwnd == nid.hWnd and it.uid == nid.uID:
                return it
        return None

    def apply(self, msg, nid):
        """Returns True if the visible set of icons changed."""
        if msg == NIM_DELETE:
            it = self.find(nid)
            if it:
                del self.items[it.key]
                return True
            return False
        if msg == NIM_SETVERSION:
            it = self.find(nid)
            if it:
                it.version = nid.uVersion
            return False
        if msg not in (NIM_ADD, NIM_MODIFY):
            return False
        it = self.find(nid)
        if it is None:
            if msg == NIM_MODIFY and not (nid.uFlags & NIF_ICON):
                return False
            it = TrayItem(self.key_of(nid))
            self.items[it.key] = it
        it.hwnd, it.uid = nid.hWnd, nid.uID
        if not it.pid:
            it.pid = self.pid_of(nid.hWnd)
            it.exe = self.exe_of(it.pid)
        if nid.uFlags & NIF_MESSAGE:
            it.cbmsg = nid.uCallbackMessage
        if nid.uFlags & NIF_ICON and nid.hIcon:
            pm = self.icon_loader(nid.hIcon)
            if pm is not None:
                it.pixmap = pm
        if nid.uFlags & NIF_TIP:
            it.tip = _wstr(nid.szTip).strip()
        if nid.uFlags & NIF_STATE:
            mask = nid.dwStateMask or NIS_HIDDEN
            if mask & NIS_HIDDEN:
                it.hidden = bool(nid.dwState & NIS_HIDDEN)
        return True

    def visible(self):
        return [it for it in self.items.values() if not it.hidden]

    def prune(self, alive):
        """Drop icons whose owner window no longer exists (app crashed without NIM_DELETE)."""
        dead = [k for k, it in self.items.items() if not alive(it.hwnd)]
        for k in dead:
            del self.items[k]
        return bool(dead)


def tray_click_messages(item, kind, x, y):
    """(wParam, lParam) pairs to post to the app's callback, per NOTIFYICON_VERSION semantics."""
    if item.version >= 4:
        wp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        lp = lambda m: ((item.uid & 0xFFFF) << 16) | m
        seq = {"left": [0x201, 0x202, NIN_SELECT],
               "right": [0x204, 0x205, WM_CONTEXTMENU_MSG],
               "double": [0x201, 0x202, 0x203, 0x202]}[kind]
        return [(wp, lp(m)) for m in seq]
    seq = {"left": [0x201, 0x202], "right": [0x204, 0x205], "double": [0x201, 0x202, 0x203, 0x202]}[kind]
    return [(item.uid, m) for m in seq]


_sig(user32.RegisterClassExW, wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p)
_sig(user32.DestroyWindow, wt.BOOL, wt.HWND)
_sig(user32.UnregisterClassW, wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
_sig(user32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.SendNotifyMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.CopyIcon, wt.HICON, wt.HICON)
_sig(user32.DestroyIcon, wt.BOOL, wt.HICON)
_sig(user32.IsWindow, wt.BOOL, wt.HWND)
_sig(user32.AllowSetForegroundWindow, wt.BOOL, wt.DWORD)
_sig(kernel32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)


def tray_windows():
    """Every window called Shell_TrayWnd, in z-order (the first one is where apps send their icons)."""
    out, h = [], None
    while True:
        h = user32.FindWindowExW(None, h, "Shell_TrayWnd", None)
        if not h:
            return out
        out.append(h)


def explorer_tray():
    """Explorer's real Shell_TrayWnd. Picked by process name: during a reload the old OrbitBar copy also
    has one, and mistaking it for Explorer sent every tray icon to the copy that was about to quit."""
    others = [h for h in tray_windows() if window_pid(h) != os.getpid()]
    for h in others:
        if proc_name(window_pid(h)).lower() == "explorer":
            return h
    return others[0] if others else None


def check_tray():
    """python orbitbar.py --check-tray : which tray windows exist and which one apps are talking to."""
    wins = tray_windows()
    print(f"{len(wins)} window(s) called Shell_TrayWnd, in the order apps find them:")
    for i, h in enumerate(wins):
        pid = window_pid(h)
        who = proc_name(pid)
        role = "Explorer's taskbar" if who.lower() == "explorer" else (
            "OrbitBar tray" if "python" in who.lower() or "orbitbar" in who.lower() else "other")
        print(f"  {i + 1}. hwnd {h:#x}  pid {pid}  {who}  ({role})" + ("   <- apps send icons here" if i == 0 else ""))
    if wins and proc_name(window_pid(wins[0])).lower() == "explorer":
        print("\nExplorer is first, so new icons go to the Windows tray. If OrbitBar is running, open its tray card "
              "and press 'Missing an app? Refresh'.")
    elif len([h for h in wins if proc_name(window_pid(h)).lower() != "explorer"]) > 1:
        print("\nMore than one OrbitBar tray window: an old copy may still be running. Quit OrbitBar and start it again.")


def load_icon_pixmap(hicon):
    """Copy another process's HICON (they are global objects) into a QPixmap we own."""
    own = user32.CopyIcon(hicon)
    if not own:
        return None
    try:
        img = QImage.fromHICON(own)
        return QPixmap.fromImage(img) if not img.isNull() else None
    except Exception:
        return None
    finally:
        user32.DestroyIcon(own)


class TrayHost(QObject):
    changed = Signal()

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.hwnd = None
        self.error = None
        self.anchor = None                 # the ^ button: where apps place their popups
        self.store = TrayStore(load_icon_pixmap, window_pid, lambda pid: proc_name(pid) if pid else "")
        self.msg_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.last_broadcast = 0.0
        self.explorer = None
        self._proc = WNDPROC(self._wndproc)
        self._pending = False
        try:
            self._create()
        except Exception as ex:
            self.error = f"{ex.__class__.__name__}: {ex}"
            print("tray host unavailable:", self.error)

    def _create(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "Shell_TrayWnd"
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(f"RegisterClassEx failed ({ctypes.get_last_error()})")
        self.hwnd = user32.CreateWindowExW(0x80 | 0x8, "Shell_TrayWnd", "", 0x80000000,     # TOOLWINDOW|TOPMOST, POPUP
                                           0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowEx failed ({ctypes.get_last_error()})")
        TRAY_HOST_HWND[0] = self.hwnd
        self.explorer = explorer_tray()
        self.keep_on_top()
        QTimer.singleShot(300, self.broadcast)

    @property
    def active(self):
        return bool(self.hwnd)

    def keep_on_top(self):
        """Stay above Explorer's Shell_TrayWnd so Shell_NotifyIcon finds us first."""
        if self.hwnd:
            first = user32.FindWindowExW(None, None, "Shell_TrayWnd", None)     # who was first before we act
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOACTIVATE | 0x0001 | 0x0002)
            exp = explorer_tray()
            if exp and exp != self.explorer:          # Explorer restarted: ask apps to re-add icons
                self.explorer = exp
                QTimer.singleShot(1500, self.broadcast)
            if first and first != self.hwnd and time.monotonic() - self.last_broadcast > 30:
                # another tray window got in front (an old copy, Explorer re-asserting itself): icons added
                # meanwhile went there. We're back on top now, so ask apps to re-add them here.
                self._ahead_count = getattr(self, "_ahead_count", 0) + 1
                if self._ahead_count <= 3 or self._ahead_count % 25 == 0:
                    print(f"{datetime.now():%H:%M:%S} tray: another tray window was first in line - "
                          f"asking apps to re-add their icons (x{self._ahead_count})")
                QTimer.singleShot(200, self.broadcast)
        if self.store.prune(lambda h: bool(user32.IsWindow(h))):
            self.changed.emit()

    def broadcast(self):
        """Ask every running app to (re)add its tray icon."""
        self.last_broadcast = time.monotonic()
        user32.SendNotifyMessageW(0xFFFF, self.msg_taskbar_created, 0, 0)

    def after_takeover(self):
        """The bar now owns the screen (and after a reload the old copy is gone): ask again, twice, so apps
        that answered the first request while another tray window was in front land here."""
        if not self.hwnd:
            return
        self.keep_on_top()
        self.explorer = explorer_tray()
        QTimer.singleShot(300, self.broadcast)
        QTimer.singleShot(4000, lambda: (self.keep_on_top(), self.broadcast()))

    def _forward(self, msg, wparam, lparam, timeout=500):
        exp = self.explorer or explorer_tray()
        if not exp:
            return 0
        res = ctypes.c_size_t(0)
        user32.SendMessageTimeoutW(exp, msg, wparam, lparam, 0x2, timeout, ctypes.byref(res))   # ABORTIFHUNG
        return res.value

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_COPYDATA and lparam:
                cds = ctypes.cast(lparam, ctypes.POINTER(COPYDATASTRUCT)).contents
                if cds.dwData == 1 and cds.lpData:                      # Shell_NotifyIcon
                    self._forward(msg, wparam, lparam)                  # keep Explorer in sync
                    parsed = parse_tray_data(ctypes.string_at(cds.lpData, cds.cbData))
                    if parsed:
                        if self.store.apply(*parsed):
                            self._changed_soon()
                    return 1
                if cds.dwData == 3 and cds.lpData:                      # Shell_NotifyIconGetRect
                    return self._icon_rect(ctypes.string_at(cds.lpData, cds.cbData))
                return self._forward(msg, wparam, lparam, 3000)         # AppBar & everything else -> Explorer
            if msg == self.msg_taskbar_created and time.monotonic() - self.last_broadcast > 5:
                QTimer.singleShot(1500, self.broadcast)                 # Explorer restarted
        except Exception as ex:
            print("tray message error:", ex)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _changed_soon(self):
        if not self._pending:                 # coalesce bursts of updates (animated icons)
            self._pending = True
            QTimer.singleShot(50, self._emit)

    def _emit(self):
        self._pending = False
        self.changed.emit()

    def _icon_rect(self, raw):
        """Apps ask where their icon is (to place popups). Answer with the ^ button."""
        try:
            buf = raw[:ctypes.sizeof(WINNOTIFYICONIDENTIFIER)].ljust(ctypes.sizeof(WINNOTIFYICONIDENTIFIER), b"\0")
            ident = WINNOTIFYICONIDENTIFIER.from_buffer_copy(buf)
            w = self.anchor
            if w is None:
                return 0
            r = wt.RECT()
            user32.GetWindowRect(int(w.window().winId()), ctypes.byref(r))
            dpr = w.devicePixelRatioF()
            p = w.mapTo(w.window(), QPoint(0, 0))
            left, top = r.left + int(p.x() * dpr), r.top + int(p.y() * dpr)
            right, bottom = left + int(w.width() * dpr), top + int(w.height() * dpr)
            x, y = (left, top) if ident.dwMessage == 1 else (right, bottom)
            return ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        except Exception:
            return 0

    def click(self, item, kind):
        if not item.cbmsg or not user32.IsWindow(item.hwnd):
            return
        if item.pid:
            user32.AllowSetForegroundWindow(item.pid)       # let the app show its window / menu
        x, y = cursor_pos()
        for wp, lp in tray_click_messages(item, kind, x, y):
            user32.PostMessageW(item.hwnd, item.cbmsg, wp, lp)

    def stop(self):
        if self.hwnd:
            TRAY_HOST_HWND[0] = None
            user32.DestroyWindow(self.hwnd)
            user32.UnregisterClassW("Shell_TrayWnd", kernel32.GetModuleHandleW(None))
            self.hwnd = None
            self.broadcast()                  # apps re-add to Explorer's tray (they're already there too)


class TrayIconButton(QWidget):
    def __init__(self, card, item):
        super().__init__()
        self.card, self.item = card, item
        self.hover = False
        self.setFixedSize(40, 40)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(item.title)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        kind = {Qt.LeftButton: "left", Qt.RightButton: "right"}.get(e.button())
        if kind:
            self.card.activate(self.item, kind)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.card.activate(self.item, "double")

    def contextMenuEvent(self, e):
        e.accept()                           # the app shows its own menu

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*T["hover"]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 9, 9)
        pm = self.item.pixmap
        if pm and not pm.isNull():
            s = 22
            p.drawPixmap(QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s), pm, QRectF(pm.rect()))
        else:
            p.setPen(QColor(TEXT))
            p.drawText(self.rect(), Qt.AlignCenter, (self.item.title[:1] or "?").upper())


class TrayCard(Card):
    COLS = 6

    def __init__(self, bar, host):
        super().__init__(bar, width=6 * 42 + 34)
        self.host = host
        self.title = lbl("Background apps", "h2")
        self.body.addWidget(self.title)
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        self.body.addLayout(self.grid)
        self.hint = lbl("Click: open · Right-click: app menu", "dim")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)
        foot = QHBoxLayout()
        self.asked = lbl("", "dim")
        refresh = text_button("Missing an app? Refresh", "link", "Ask every running app to show its tray icon again")
        refresh.clicked.connect(self.ask_again)
        foot.addWidget(self.asked)
        foot.addStretch(1)
        foot.addWidget(refresh)
        self.body.addLayout(foot)
        host.changed.connect(self.on_change)

    def ask_again(self):
        self.host.keep_on_top()
        self.host.broadcast()
        self.asked.setText("Asked - icons appear in a moment")
        QTimer.singleShot(4000, lambda: self.asked.setText(""))

    def on_change(self):
        if self.isVisible():
            self.refresh()
            self.reposition()

    def refresh(self):
        clear_layout(self.grid)
        items = self.host.store.visible()
        if not self.host.active:
            self.title.setText("System tray unavailable")
            self.hint.setText(self.host.error or "")
            return
        self.title.setText(f"Background apps · {len(items)}")
        self.hint.setText("Click: open · Right-click: app menu" if items else
                          "No tray icons yet. Apps that were already running may need a restart.")
        for i, it in enumerate(items):
            self.grid.addWidget(TrayIconButton(self, it), i // self.COLS, i % self.COLS)

    def activate(self, item, kind):
        self.hide()                                     # close first so the app's menu isn't dismissed
        QTimer.singleShot(30, lambda: self.host.click(item, kind))


class TrayButton(QPushButton):
    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.host = TrayHost(bar)
        self.card = TrayCard(bar, self.host)
        self.host.anchor = self
        self.setProperty("class", "glyph")
        self.setFont(QFont(GLYPH_FONT.split(",")[0], 9))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.set_chevron()
        self.setFixedWidth(uw(26))
        self.clicked.connect(lambda: self.card.open_from(self))
        self.host.changed.connect(self.update_tip)
        self.update_tip()

    def rescale(self):
        self.setFixedWidth(uw(26))

    def set_chevron(self):
        self.setText("\uE70D" if self.bar.cfg["position"] == "top" else "\uE70E")

    def update_tip(self):
        n = len(self.host.store.visible())
        self.setToolTip(f"Background apps ({n})" if self.host.active else "System tray unavailable")



class StudyState:
    """Local-only study data: completed deadlines, sent reminders, daily stats (last 60 days)."""

    def __init__(self):
        self.data = {"done": [], "notified": [], "stats": {}}
        try:
            with open(STUDY_PATH, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.dirty = False

    def save(self, force=False):
        if not (self.dirty or force):
            return
        stats = self.data["stats"]
        for day in sorted(stats)[:-60]:
            del stats[day]
        self.data["notified"] = self.data["notified"][-500:]
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = STUDY_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, STUDY_PATH)
            self.dirty = False
        except OSError as ex:
            print("couldn't save study data:", ex)

    def day(self, d=None):
        key = (d or datetime.now()).strftime("%Y-%m-%d")
        return self.data["stats"].setdefault(key, {"study": 0, "distract": 0, "other": 0, "focus": 0,
                                                   "sessions": 0, "caught": 0, "apps": {}})

    def is_done(self, item_id):
        return item_id in self.data["done"]

    def set_done(self, item_id, done):
        lst = self.data["done"]
        if done and item_id not in lst:
            lst.append(item_id)
        elif not done and item_id in lst:
            lst.remove(item_id)
        self.dirty = True
        self.save()

    def notify_once(self, tag):
        if tag in self.data["notified"]:
            return False
        self.data["notified"].append(tag)
        self.dirty = True
        return True

    def streak(self, minutes=25):
        n, d = 0, datetime.now()
        if self.data["stats"].get(d.strftime("%Y-%m-%d"), {}).get("focus", 0) < minutes * 60:
            d -= timedelta(days=1)                  # today isn't over yet: count from yesterday
        while self.data["stats"].get(d.strftime("%Y-%m-%d"), {}).get("focus", 0) >= minutes * 60:
            n += 1
            d -= timedelta(days=1)
        return n


class StudySync(QThread):
    """Downloads LMS / class calendar links in the background."""
    done = Signal(list, list)          # items, errors

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        st = self.cfg.get("study", {})
        items, errors = [], []
        for kind, key in (("lms", "lms_links"), ("classes", "class_links")):
            for url in [u.strip().strip('"') for u in (st.get(key) or "").splitlines() if u.strip()]:
                real = "https://" + url[len("webcal://"):] if url.lower().startswith("webcal://") else url
                local = real[len("file:///"):] if real.lower().startswith("file:///") else real
                if os.path.isfile(os.path.expandvars(local)):          # a .ics file on this PC (handy for testing)
                    try:
                        with open(os.path.expandvars(local), encoding="utf-8", errors="replace") as f:
                            items += events_to_items(parse_ics(f.read()), kind)
                    except OSError as ex:
                        errors.append(f"{os.path.basename(local)}: {ex}")
                    continue
                try:
                    import ssl
                    req = urllib.request.Request(real, headers={"User-Agent": WeatherWorker.UA})
                    try:
                        with urllib.request.urlopen(req, timeout=20) as r:
                            text = r.read().decode("utf-8", "replace")
                    except urllib.error.URLError as ex:
                        if not isinstance(getattr(ex, "reason", None), ssl.SSLError):
                            raise
                        import certifi
                        ctx = ssl.create_default_context(cafile=certifi.where())
                        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                            text = r.read().decode("utf-8", "replace")
                    if "BEGIN:VCALENDAR" not in text[:2000]:
                        raise ValueError("not a calendar (.ics) link")
                    items += events_to_items(parse_ics(text), kind)
                except Exception as ex:
                    host = urllib.parse.urlparse(real).netloc or real[:30]
                    errors.append(f"{host}: {ex.__class__.__name__}: {str(ex)[:80]}")
        self.done.emit(items, errors)


class StudyData(QObject):
    """Merges manual entries + synced calendars, sends reminders."""
    changed = Signal()

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.state = StudyState()
        self.synced, self.errors, self.synced_at = [], [], None
        self.worker = None
        self.items = []
        self.rebuild()
        self.sync_timer = QTimer(self, interval=30 * 60 * 1000, timeout=self.sync)
        self.sync_timer.start()
        QTimer.singleShot(4000, self.sync)

    @property
    def cfg(self):
        return self.bar.cfg.setdefault("study", dict(DEFAULT_STUDY))

    def sync(self):
        if not ((self.cfg.get("lms_links") or "").strip() or (self.cfg.get("class_links") or "").strip()):
            return
        if self.worker and self.worker.isRunning():
            self._again = True                  # links changed mid-sync: sync once more when it's done
            return
        self.worker = StudySync(self.bar.cfg)
        self.worker.done.connect(self.on_synced)
        self.worker.finished.connect(self._after_sync)
        self.worker.start()

    def on_synced(self, items, errors):
        self.synced, self.errors, self.synced_at = items, errors, datetime.now()
        self.rebuild()

    def _after_sync(self):
        if getattr(self, "_again", False):
            self._again = False
            self.worker.wait(1000)
            self.sync()

    def rebuild(self):
        c = self.cfg
        items = list(self.synced)
        items += parse_manual_dated(c.get("deadlines", ""), "deadline")
        items += parse_manual_dated(c.get("exams", ""), "exam")
        items += parse_timetable(c.get("timetable", ""))
        self.items = dedupe_items(items)
        self.changed.emit()

    # ---- study context: YouTube opened from Moodle is study, not a distraction
    CONTEXT_SECS = 600          # a browser window counts as "in study context" for 10 min after a study site
    ALLOW_SECS = 900            # "It's for study" allows that window for 15 min

    def classify_window(self, h, exe, title):
        """classify_activity + context: same browser window recently on a study site, or allowed by the user."""
        cat, label = classify_activity(exe, title, self.cfg)
        now = time.monotonic()
        ctx = self.__dict__.setdefault("_ctx", {})          # hwnd -> (time on a study site, site name)
        allowed = self.__dict__.setdefault("_allowed", {})  # hwnd -> allowed until
        browser = exe.lower().removesuffix(".exe") in BROWSERS
        if cat == "study" and browser:
            ctx[h] = (now, label)
        if cat == "distract" and browser:
            if allowed.get(h, 0) > now:
                return "study", f"{label} (allowed)"
            seen = ctx.get(h)
            if seen and now - seen[0] < self.CONTEXT_SECS:
                return "study", f"{label} via {seen[1]}"
        if len(ctx) > 200:
            ctx.clear()
        return cat, label

    def allow_window(self, h):
        self.__dict__.setdefault("_allowed", {})[h] = time.monotonic() + self.ALLOW_SECS

    # ---- queries
    def deadlines(self, days=14, include_done=False):
        now = local_now()
        out = [i for i in self.items if i["kind"] == "deadline" and i["when"] <= now + timedelta(days=days)
               and (include_done or not self.state.is_done(i["id"]))
               and i["when"] >= now - timedelta(days=2)]
        return out

    def next_deadline(self):
        d = self.deadlines()
        return d[0] if d else None

    def exams(self, days=90):
        now = local_now()
        return [i for i in self.items if i["kind"] == "exam" and now - timedelta(hours=3) <= i["when"] <= now + timedelta(days=days)]

    def classes(self, days=2):
        now = local_now()
        return [i for i in self.items if i["kind"] == "class" and i["end"] >= now and i["when"] <= now + timedelta(days=days)]

    def current_or_next_class(self):
        now = local_now()
        for c in self.classes(days=1):
            if c["when"] <= now <= c["end"]:
                return c, True
            if c["when"] > now and c["when"].date() == now.date():
                return c, False
        return None, False

    # ---- reminders (called every 30 s)
    def reminders(self, anchor):
        now = local_now()
        toast = self.bar.toast.notify
        for d in self.deadlines(days=2):
            left = d["when"] - now
            for hours, label in ((1, "in 1 hour" if left > timedelta(minutes=50) else f"in {fmt_left(left)}"),
                                 (24, "within 24 hours")):
                if timedelta(0) < left <= timedelta(hours=hours) and self.state.notify_once(f"{d['id']}|{hours}h"):
                    if hours == 1:
                        self.state.notify_once(f"{d['id']}|24h")      # don't send the 24 h one afterwards
                    toast("📌 Deadline " + label, f"{d['course'] + ' · ' if d['course'] else ''}{d['title']}\n"
                          f"due {d['when']:%a %H:%M} ({fmt_left(left)} left)", anchor)
                    break
        for c in self.classes(days=1):
            left = c["when"] - now
            if timedelta(0) < left <= timedelta(minutes=10) and self.state.notify_once(f"{c['id']}|10m"):
                toast("🎓 Class in " + fmt_left(left), f"{c['title']}" + (f" · {c['room']}" if c["room"] else ""), anchor)
        for e in self.exams(days=8):
            left = e["when"] - now
            for days, label in ((1, "tomorrow"), (7, "within a week")):
                if timedelta(0) < left <= timedelta(days=days) and self.state.notify_once(f"{e['id']}|{days}d"):
                    if days == 1:
                        self.state.notify_once(f"{e['id']}|7d")
                    toast("📝 Exam " + label, f"{e['title']} · {e['when']:%a %d %b %H:%M}", anchor)
                    break
        self.state.save()


class StudyCard(Card):
    def __init__(self, bar, data):
        super().__init__(bar, width=360)
        self.data = data
        self.content = QVBoxLayout()
        self.content.setSpacing(4)
        self.body.addLayout(self.content)
        foot = QHBoxLayout()
        self.status = lbl("", "dim")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        sync = text_button("Sync", "link")
        sync.clicked.connect(data.sync)
        edit = text_button("Edit…", "link")
        edit.clicked.connect(lambda: (self.hide(), bar.open_settings()))
        foot.addWidget(sync)
        foot.addWidget(edit)
        self.body.addLayout(foot)
        data.changed.connect(lambda: self.isVisible() and (self.refresh(), self.reposition()))

    def row(self, left, mid, right, right_cls=None, color=None):
        r = QHBoxLayout()
        a = lbl(left, "dim")
        a.setFixedWidth(78)
        r.addWidget(a)
        m = lbl(mid)
        m.setWordWrap(True)
        r.addWidget(m, 1)
        b = lbl(right, right_cls)
        if color:
            b.setStyleSheet(f"color: {color}; font-weight: 600;")
        r.addWidget(b)
        self.content.addLayout(r)

    def deadline_row(self, it, now, done):
        d = self.data
        r = QHBoxLayout()
        cb = QCheckBox()
        cb.setChecked(done)
        cb.setToolTip("Mark as not done" if done else "Mark as done")
        t = lbl((f"<b>{it['course']}</b> · " if it["course"] else "") + it["title"])
        t.setWordWrap(True)
        left = it["when"] - now
        when = lbl(("done" if done else fmt_left(left)) + f"\n{it['when']:%a %d %b %H:%M}", "dim")
        when.setAlignment(Qt.AlignRight)
        if done:
            t.setStyleSheet(f"color: {DIM}; text-decoration: line-through;")
        else:
            when.setStyleSheet(f"color: {URGENT[urgency(left)]};")

        def toggled(on, i=it["id"]):
            d.state.set_done(i, on)
            if on:     # strike it through right away, then let it slide out (a moment to change your mind)
                t.setStyleSheet(f"color: {DIM}; text-decoration: line-through;")
                when.setText("done ✓" + f"\n{it['when']:%a %d %b %H:%M}")
                when.setStyleSheet(f"color: {URGENT['green']};")
                QTimer.singleShot(900, d.changed.emit)
            else:
                QTimer.singleShot(0, d.changed.emit)
        cb.toggled.connect(toggled)
        r.addWidget(cb)
        r.addWidget(t, 1)
        r.addWidget(when)
        self.content.addLayout(r)

    def refresh(self):
        clear_layout(self.content)
        d, now = self.data, local_now()
        # classes
        self.content.addWidget(section("Classes"))
        cls = d.classes(days=2)
        if not cls:
            self.content.addWidget(lbl("No classes in the next 2 days", "dim"))
        for c in cls[:5]:
            day = "Today" if c["when"].date() == now.date() else c["when"].strftime("%a")
            live = c["when"] <= now <= c["end"]
            self.row(f"{day} {c['when']:%H:%M}", c["title"] + (f" · {c['room']}" if c["room"] else ""),
                     "now" if live else fmt_left(c["when"] - now), color=URGENT["green"] if live else None)
        # deadlines: open ones only; ticked ones fold into a "done" line (click it to see or undo)
        dls = d.deadlines(days=14, include_done=True)
        open_ = [i for i in dls if not d.state.is_done(i["id"])]
        done_ = [i for i in dls if d.state.is_done(i["id"])]
        self.content.addWidget(section(f"Deadlines · {len(open_)} left · next 14 days" if open_
                                       else "Deadlines · next 14 days"))
        if not open_:
            self.content.addWidget(lbl("All done 🎉" if done_ else "Nothing due 🎉", "dim"))
        for it in open_[:10]:
            self.deadline_row(it, now, False)
        if done_:
            show = getattr(self, "show_done", False)
            t = text_button(f"✓ {len(done_)} done · {'hide' if show else 'show'}", "link")
            t.clicked.connect(lambda: (setattr(self, "show_done", not show), self.refresh(), self.reposition()))
            r = QHBoxLayout()
            r.addWidget(t)
            r.addStretch(1)
            self.content.addLayout(r)
            if show:
                for it in done_[:10]:
                    self.deadline_row(it, now, True)
        # exams
        exams = d.exams()
        if exams:
            self.content.addWidget(section("Exams"))
            for e in exams[:4]:
                left = e["when"] - now
                self.row(f"{e['when']:%a %d %b}", e["title"] + (f" · {e['room']}" if e["room"] else ""),
                         fmt_left(left), color=URGENT[urgency(left)] if left.days < 3 else None)
        # status line
        links = sum(1 for k in ("lms_links", "class_links") for u in (d.cfg.get(k) or "").splitlines() if u.strip())
        if d.errors:
            self.status.setText("⚠ " + d.errors[0][:70])
            self.status.setToolTip("\n".join(d.errors))
        elif links:
            self.status.setText(f"{links} calendar link{'s' if links != 1 else ''}"
                                + (f" · synced {d.synced_at:%H:%M}" if d.synced_at else " · syncing…"))
        else:
            self.status.setText("Tip: sync your Moodle calendar via Edit…")


URGENT = {"red": "#f87171", "amber": "#fbbf24", "normal": "", "green": "#34d399"}


class StudyChips(QWidget):
    """Up to three chips on the bar: next class, next deadline, next exam (each hides when empty)."""

    def __init__(self, bar, data):
        super().__init__()
        self.bar, self.data = bar, data
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.card = StudyCard(bar, data)
        self.cls = text_button("", "chip")
        self.dl = text_button("", "chip")
        self.ex = text_button("", "chip")
        for b in (self.cls, self.dl, self.ex):
            b.clicked.connect(lambda _=False, b=b: self.card.open_from(b))
            lay.addWidget(b)
        self.compact_mode = False
        data.changed.connect(self.refresh)
        self.refresh()

    def set_compact(self, on):
        """Short on room: show only the most pressing chip (deadline > class > exam)."""
        if on != self.compact_mode:
            self.compact_mode = on
            self.refresh(refit=False)

    @staticmethod
    def short(text, n=14):
        return text if len(text) <= n else text[: n - 1] + "…"

    @staticmethod
    def compact(delta):
        """Chip countdown: 45m / 19h / 2d / 11d."""
        secs = delta.total_seconds()
        if secs < 0:
            return "overdue"
        if secs < 3600:
            return f"{max(1, int(secs // 60))}m"
        if secs < 86400:
            return f"{int(secs // 3600)}h"
        return f"{int(secs // 86400)}d"

    def refresh(self, refit=True):
        before = (self.cls.text(), self.dl.text(), self.ex.text(), self.isVisibleTo(self.parentWidget() or self))
        now = local_now()
        c, live = self.data.current_or_next_class()
        if c:
            label = c["course"] or c["title"]
            if live:
                self.cls.setText(f"🎓 {self.short(label, 9)} · till {c['end']:%H:%M}")
            else:
                self.cls.setText(f"🎓 {self.short(label, 9)} · {self.compact(c['when'] - now)}")
            self.cls.setToolTip(f"{c['title']}\n{c['when']:%a %H:%M}–{c['end']:%H:%M}" + (f" · {c['room']}" if c["room"] else "")
                                + ("" if live else f"\nstarts in {fmt_left(c['when'] - now)}"))
        self.cls.setVisible(bool(c))
        d = self.data.next_deadline()
        if d:
            left = d["when"] - now
            more = len(self.data.deadlines(days=7)) - 1
            self.dl.setText(f"📌 {self.short(d['title'], 11)} · {self.compact(left)}" + (f"  +{more}" if more > 0 else ""))
            col = URGENT[urgency(left)]
            set_sheet(self.dl, f"color: {col};" if col else "")
            more = len(self.data.deadlines(days=7)) - 1
            self.dl.setToolTip((f"{d['course']} · " if d["course"] else "") + f"{d['title']}\ndue {d['when']:%a %d %b %H:%M}"
                               + f" ({fmt_left(left)})"
                               + (f"\n+{more} more this week" if more > 0 else ""))
        self.dl.setVisible(bool(d and d["when"] - now <= timedelta(days=7)))
        exams = self.data.exams(days=60)
        if exams:
            e = exams[0]
            left = e["when"] - now
            days = left.days
            self.ex.setText(f"📝 {self.short(e['course'] or e['title'], 9)} · {self.compact(left)}")
            set_sheet(self.ex, f"color: {URGENT['red']};" if days < 3 else "")
            self.ex.setToolTip(f"{e['title']}\n{e['when']:%A %d %B %H:%M}" + (f"\n{e['room']}" if e["room"] else ""))
        self.ex.setVisible(bool(exams))
        if self.compact_mode:
            shown = [b for b in (self.dl, self.cls, self.ex) if b.isVisibleTo(self)]
            for b in shown[1:]:
                b.hide()
        self.setVisible(self.cls.isVisibleTo(self) or self.dl.isVisibleTo(self) or self.ex.isVisibleTo(self))
        after = (self.cls.text(), self.dl.text(), self.ex.text(), self.isVisibleTo(self.parentWidget() or self))
        if refit and after != before and getattr(self.bar, "fit_timer", None):
            self.bar.fit_timer.start()


# ---- activity tracking (study time) -------------------------------------------------------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


_sig(user32.GetLastInputInfo, wt.BOOL, ctypes.POINTER(LASTINPUTINFO))
_sig(kernel32.GetTickCount, wt.DWORD)


def idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return 0
    return max(0, (kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


class ActivityTracker(QObject):
    """Every 5 s: what's in front of you -> study / distraction / other. Totals only, stored locally."""
    INTERVAL = 5

    def __init__(self, bar, data):
        super().__init__()
        self.bar, self.data = bar, data
        self.current = ("other", "")
        self.timer = QTimer(self, interval=self.INTERVAL * 1000, timeout=self.sample)
        if data.cfg.get("track_time", True):
            self.timer.start()

    def sample(self):
        h = user32.GetForegroundWindow()
        if not h or window_pid(h) == os.getpid():
            return
        exe = exe_base(window_exe(h))
        title = window_text(h)
        cat, label = self.data.classify_window(h, exe, title)
        self.current = (cat, label)
        playing_video = cat == "distract" and self.bar.media and self.bar.media.data and self.bar.media.data.get("playing")
        if idle_seconds() > 120 and not playing_video:           # away from the keyboard
            return
        day = self.data.state.day()
        day[cat] = day.get(cat, 0) + self.INTERVAL
        if cat != "other":
            name = label[:-4] if label.lower().endswith(".exe") else label
            apps = day.setdefault("apps", {})
            apps[f"{cat}:{name}"] = apps.get(f"{cat}:{name}", 0) + self.INTERVAL
        self.data.state.dirty = True


class WeekChart(QWidget):
    """Last 7 days: study (accent) vs distraction (red) bars."""

    def __init__(self, state, bar):
        super().__init__()
        self.state, self.bar = state, bar
        self.accent = QColor(bar.cfg["accent"])
        self.setFixedHeight(96)

    def paintEvent(self, e):
        self.accent = QColor(self.bar.cfg["accent"])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        days = [datetime.now() - timedelta(days=i) for i in range(6, -1, -1)]
        vals = [self.state.data["stats"].get(d.strftime("%Y-%m-%d"), {}) for d in days]
        top = max([v.get("study", 0) + v.get("distract", 0) for v in vals] + [3600])
        w, h = self.width(), self.height() - 16
        bw = w / 7
        f = p.font()
        f.setPixelSize(10)
        p.setFont(f)
        for i, (d, v) in enumerate(zip(days, vals)):
            x = i * bw + bw * 0.22
            st, di = v.get("study", 0), v.get("distract", 0)
            hs, hd = h * st / top, h * di / top
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*T["pill"]))
            p.drawRoundedRect(QRectF(x, 0, bw * 0.56, h), 4, 4)
            p.setBrush(self.accent)
            p.drawRoundedRect(QRectF(x, h - hs, bw * 0.56, hs), 4, 4)
            p.setBrush(QColor("#f87171"))
            p.drawRoundedRect(QRectF(x, h - hs - hd, bw * 0.56, hd), 4, 4)
            p.setPen(QColor(TEXT if i == 6 else DIM))
            p.drawText(QRectF(i * bw, h + 2, bw, 14), Qt.AlignCenter, "Today" if i == 6 else d.strftime("%a"))


def fmt_hm(secs):
    m = int(secs) // 60
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"



# --------------------------------------------------------------------------------------
# Widget registry + layout engine (the gallery builds on this)
# --------------------------------------------------------------------------------------
WIDGET_MIME = "application/x-orbitbar-widget"


def _make_search(bar):
    b = glyph_button(G["search"], "Search apps & folders  (right-click: Windows Search)", 14)
    b.clicked.connect(lambda: bar.search_card.open(b))
    b.setContextMenuPolicy(Qt.CustomContextMenu)
    b.customContextMenuRequested.connect(lambda *_: send_keys("win", "s"))
    return b


def _make_tray(bar):
    try:
        return TrayButton(bar)
    except Exception as ex:
        print("system tray disabled:", ex)
        return None



# ---- Network safety: VPN, Wi-Fi security, DNS --------------------------------------------
# Everything here comes from Windows itself (no internet requests). The public IP is looked up only
# when you press "Look up" in the card, and it's masked unless you press "Show".
iphlpapi = ctypes.WinDLL("iphlpapi")


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class _NET_ADDR(ctypes.Structure):          # header shared by unicast / DNS / gateway address entries
    pass


_NET_ADDR._fields_ = [("Length", ctypes.c_ulong), ("Flags", wt.DWORD), ("Next", ctypes.POINTER(_NET_ADDR)),
                      ("Address", _SOCKET_ADDRESS)]


class _ADAPTER(ctypes.Structure):
    pass


_ADAPTER._fields_ = [
    ("Length", ctypes.c_ulong), ("IfIndex", wt.DWORD), ("Next", ctypes.POINTER(_ADAPTER)),
    ("AdapterName", ctypes.c_char_p), ("FirstUnicastAddress", ctypes.POINTER(_NET_ADDR)),
    ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.POINTER(_NET_ADDR)), ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p), ("FriendlyName", ctypes.c_wchar_p), ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", wt.DWORD), ("Flags", wt.DWORD), ("Mtu", wt.DWORD), ("IfType", wt.DWORD),
    ("OperStatus", ctypes.c_int), ("Ipv6IfIndex", wt.DWORD), ("ZoneIndices", wt.DWORD * 16),
    ("FirstPrefix", ctypes.c_void_p), ("TransmitLinkSpeed", ctypes.c_ulonglong),
    ("ReceiveLinkSpeed", ctypes.c_ulonglong), ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(_NET_ADDR)),
]
_sig(iphlpapi.GetAdaptersAddresses, wt.ULONG, wt.ULONG, wt.ULONG, ctypes.c_void_p, ctypes.c_void_p,
     ctypes.POINTER(wt.ULONG))
_sig(iphlpapi.GetBestInterface, wt.DWORD, wt.DWORD, ctypes.POINTER(wt.DWORD))


def _sockaddr_str(sa):
    import ipaddress
    if not sa.lpSockaddr:
        return None
    fam = ctypes.c_ushort.from_address(sa.lpSockaddr).value
    if fam == 2:
        return str(ipaddress.IPv4Address(ctypes.string_at(sa.lpSockaddr + 4, 4)))
    if fam == 23:
        return str(ipaddress.IPv6Address(ctypes.string_at(sa.lpSockaddr + 8, 16)))
    return None


def _addr_list(ptr):
    out = []
    while ptr:
        a = _sockaddr_str(ptr.contents.Address)
        if a:
            out.append(a)
        ptr = ptr.contents.Next
    return out


def list_adapters():
    size = wt.ULONG(32768)
    buf = None
    for _ in range(4):
        buf = ctypes.create_string_buffer(size.value)
        rc = iphlpapi.GetAdaptersAddresses(0, 0x2 | 0x4 | 0x80, None, buf, ctypes.byref(size))
        if rc == 0:
            break
        if rc != 111:                                 # ERROR_BUFFER_OVERFLOW: try again with the size asked for
            return []
    else:
        return []
    out, ptr = [], ctypes.cast(buf, ctypes.POINTER(_ADAPTER))
    while ptr:
        a = ptr.contents
        out.append(dict(index=int(a.IfIndex), name=a.FriendlyName or "", desc=a.Description or "",
                        type=int(a.IfType), up=a.OperStatus == 1, ips=_addr_list(a.FirstUnicastAddress),
                        dns=_addr_list(a.FirstDnsServerAddress), gw=_addr_list(a.FirstGatewayAddress)))
        ptr = a.Next
    return out


def best_interface():
    """Index of the adapter Windows would use to reach the internet right now."""
    import socket
    import struct
    idx = wt.DWORD(0)
    dest = struct.unpack("<I", socket.inet_aton("1.1.1.1"))[0]
    return int(idx.value) if iphlpapi.GetBestInterface(dest, ctypes.byref(idx)) == 0 else None


VPN_WORDS = ("wireguard", "wintun", "tap-windows", "tap-win32", "openvpn", "nordlynx", "nordvpn", "protonvpn",
             "proton vpn", "mullvad", "expressvpn", "surfshark", "windscribe", "private internet access",
             "anyconnect", "cisco", "globalprotect", "pangp", "fortinet", "forticlient", "sonicwall", "zscaler",
             "cloudflare", "tailscale", "zerotier", "juniper", "pulse secure", "ivanti", "check point", "wan miniport",
             "ikev2", "sstp", "l2tp", "pptp", "vpn")
NOT_VPN = ("hyper-v", "vethernet", "vmware", "virtualbox", "loopback", "bluetooth", "npcap", "wsl", "docker",
           "kernel debug", "teredo", "isatap", "6to4")


def is_vpn_adapter(a):
    text = (a["name"] + " " + a["desc"]).lower()
    if any(w in text for w in NOT_VPN):
        return False
    return a["type"] == 23 or any(w in text for w in VPN_WORDS)       # 23 = PPP (Windows' own VPN)


class _GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class _WLAN_IF(ctypes.Structure):
    _fields_ = [("guid", _GUID), ("desc", ctypes.c_wchar * 256), ("state", ctypes.c_int)]


class _WLAN_IF_LIST(ctypes.Structure):
    _fields_ = [("n", wt.DWORD), ("i", wt.DWORD), ("items", _WLAN_IF * 1)]


class _DOT11_SSID(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ulong), ("ssid", ctypes.c_ubyte * 32)]


class _WLAN_ASSOC(ctypes.Structure):
    _fields_ = [("ssid", _DOT11_SSID), ("bss_type", ctypes.c_int), ("bssid", ctypes.c_ubyte * 6),
                ("phy", ctypes.c_int), ("phy_index", ctypes.c_ulong), ("quality", ctypes.c_ulong),
                ("rx", ctypes.c_ulong), ("tx", ctypes.c_ulong)]


class _WLAN_SEC(ctypes.Structure):
    _fields_ = [("enabled", wt.BOOL), ("onex", wt.BOOL), ("auth", ctypes.c_int), ("cipher", ctypes.c_int)]


class _WLAN_CONN(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int), ("mode", ctypes.c_int), ("profile", ctypes.c_wchar * 256),
                ("assoc", _WLAN_ASSOC), ("sec", _WLAN_SEC)]


WIFI_AUTH = {1: "Open", 2: "WEP", 3: "WPA-Enterprise", 4: "WPA-Personal", 6: "WPA2-Enterprise", 7: "WPA2-Personal",
             8: "WPA3-Enterprise", 9: "WPA3-Personal", 10: "Enhanced Open", 11: "WPA3-Enterprise"}


class WlanClient:
    """Reads the current Wi-Fi connection (name, security, signal). Not present on PCs without Wi-Fi."""

    def __init__(self):
        self.api = ctypes.WinDLL("wlanapi")
        _sig(self.api.WlanOpenHandle, wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD),
             ctypes.POINTER(wt.HANDLE))
        _sig(self.api.WlanEnumInterfaces, wt.DWORD, wt.HANDLE, ctypes.c_void_p,
             ctypes.POINTER(ctypes.POINTER(_WLAN_IF_LIST)))
        _sig(self.api.WlanQueryInterface, wt.DWORD, wt.HANDLE, ctypes.POINTER(_GUID), ctypes.c_int, ctypes.c_void_p,
             ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
        _sig(self.api.WlanFreeMemory, None, ctypes.c_void_p)
        _sig(self.api.WlanCloseHandle, wt.DWORD, wt.HANDLE, ctypes.c_void_p)
        ver, self.h = wt.DWORD(), wt.HANDLE()
        if self.api.WlanOpenHandle(2, None, ctypes.byref(ver), ctypes.byref(self.h)) != 0:
            raise OSError("Wi-Fi service not available")

    def current(self):
        lst = ctypes.POINTER(_WLAN_IF_LIST)()
        if self.api.WlanEnumInterfaces(self.h, None, ctypes.byref(lst)) != 0:
            return None
        try:
            n = lst.contents.n
            items = ctypes.cast(ctypes.addressof(lst.contents.items), ctypes.POINTER(_WLAN_IF * max(n, 1))).contents
            for i in range(n):
                if items[i].state != 1:                          # wlan_interface_state_connected
                    continue
                size, data = wt.DWORD(), ctypes.c_void_p()
                if self.api.WlanQueryInterface(self.h, ctypes.byref(items[i].guid), 7, None, ctypes.byref(size),
                                               ctypes.byref(data), None) != 0:
                    continue
                try:
                    c = ctypes.cast(data, ctypes.POINTER(_WLAN_CONN)).contents
                    ssid = bytes(c.assoc.ssid.ssid[:c.assoc.ssid.len]).decode("utf-8", "replace")
                    return dict(ssid=ssid, auth=int(c.sec.auth), cipher=int(c.sec.cipher),
                                quality=int(c.assoc.quality) // 10 * 10)
                finally:
                    self.api.WlanFreeMemory(data)
        finally:
            self.api.WlanFreeMemory(lst)
        return None

    def close(self):
        self.api.WlanCloseHandle(self.h, None)


def wifi_level(auth, cipher):
    """(level, plain-English reason). level: ok / old / open / weak."""
    if cipher in (1, 5, 0x101) or auth == 2:
        return "weak", "WEP encryption is broken: anyone nearby can read this traffic."
    if auth == 1 and cipher == 0:
        return "open", "Open network with no encryption: anyone nearby can read unencrypted traffic."
    if auth in (3, 4) or cipher == 2:
        return "old", "Old WPA/TKIP security: weak by today's standards."
    return "ok", ""


def net_snapshot(wlan=None):
    adapters = list_adapters()
    best = best_interface()
    up = [a for a in adapters if a["up"] and a["ips"] and a["type"] != 24]          # 24 = loopback
    vpns = [a for a in up if is_vpn_adapter(a)]
    main = next((a for a in up if a["index"] == best), None)
    full = bool(main and is_vpn_adapter(main))
    phys = next((a for a in up if not is_vpn_adapter(a) and a["gw"]), None)
    kind = None if not phys else ("wifi" if phys["type"] == 71 else "ethernet")
    wifi = None
    if kind == "wifi" and wlan is not None:
        try:
            wifi = wlan.current()
        except Exception:
            wifi = None
    level, why = wifi_level(wifi["auth"], wifi["cipher"]) if wifi else ("ok", "")
    src = phys or main or {}
    return dict(online=bool(main or phys), kind=kind, uplink=(phys or {}).get("name", ""),
                vpn_full=full, vpn_name=main["name"] if full else (vpns[0]["name"] if vpns else ""),
                vpn_any=bool(vpns), wifi=wifi, level=level, why=why,
                ips=[ip for ip in src.get("ips", []) if ":" not in ip][:2], gateway=(src.get("gw") or [""])[0],
                dns=((main or phys or {}).get("dns") or [])[:4])


KNOWN_DNS = {"1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "8.8.8.8": "Google", "8.8.4.4": "Google",
             "9.9.9.9": "Quad9", "149.112.112.112": "Quad9", "208.67.222.222": "OpenDNS", "208.67.220.220": "OpenDNS",
             "94.140.14.14": "AdGuard", "94.140.15.15": "AdGuard", "76.76.2.0": "Control D"}


def http_json(url, timeout=8):
    import ssl
    req = urllib.request.Request(url, headers={"User-Agent": f"OrbitBar/{APP_VERSION} ({APP_PUBLISHER})"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as ex:
        if not isinstance(getattr(ex, "reason", None), ssl.SSLError):
            raise
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


def fetch_latest_release():
    """{"version": "5.6.0", "url": release page} of the newest published (non-draft, non-prerelease) release."""
    if not GITHUB_REPO:
        return None
    d = http_json(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
    tag = d.get("tag_name") or ""
    info = {"version": tag.lstrip("vV"), "url": d.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"}
    for a in d.get("assets") or []:                  # the zip GitHub Actions built (see release.yml)
        if str(a.get("name", "")).lower().endswith("-windows.zip"):
            info["zip"] = a.get("browser_download_url") or ""
            info["size"] = int(a.get("size") or 0)
            info["digest"] = a.get("digest") or ""   # "sha256:<hex>", worked out by GitHub when it stored the file
            break
    return info


# ---- Self-update (downloaded build only) -------------------------------------------------
# Running from source? `git pull` is the update. For OrbitBar.exe: download the release zip from GitHub,
# check it (right repo, right size, GitHub's own SHA-256 of the file, an OrbitBar.exe inside), unpack it next
# to the settings, and let a tiny PowerShell helper copy it over the program folder once OrbitBar has quit.
UPDATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", CONFIG_DIR), APP_NAME, "update")
PENDING_PATH = os.path.join(UPDATE_DIR, "pending.json")


def can_self_update():
    if not getattr(sys, "frozen", False):
        return False
    app_dir = os.path.dirname(sys.executable)
    probe = os.path.join(app_dir, ".orbitbar-write-test")
    try:                                              # e.g. not in Program Files without admin rights
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def download_update(info):
    """Runs in the background. Returns {"version", "src"} of the unpacked new OrbitBar folder, or {"error": why}."""
    import hashlib
    import shutil
    import ssl
    import zipfile
    ver, url = info.get("version", ""), info.get("zip") or ""
    if not url.startswith(f"https://github.com/{GITHUB_REPO}/releases/download/"):
        return {"error": "the release has no Windows zip from this project"}
    want = (info.get("digest") or "").lower()
    if not want.startswith("sha256:"):
        return {"error": "GitHub didn't give a checksum for the download"}
    os.makedirs(UPDATE_DIR, exist_ok=True)
    zpath = os.path.join(UPDATE_DIR, f"OrbitBar-v{ver}.zip")
    req = urllib.request.Request(url, headers={"User-Agent": f"OrbitBar/{APP_VERSION} ({APP_PUBLISHER})"})

    def fetch(ctx=None):
        h, total = hashlib.sha256(), 0
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r, open(zpath, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > 400 * 1024 * 1024:
                    raise ValueError("download is far too big")
                h.update(chunk)
                f.write(chunk)
        return h.hexdigest(), total
    try:
        try:
            got, total = fetch()
        except urllib.error.URLError as ex:
            if not isinstance(getattr(ex, "reason", None), ssl.SSLError):
                raise
            import certifi
            got, total = fetch(ssl.create_default_context(cafile=certifi.where()))
    except Exception as ex:
        return {"error": f"download failed ({ex})"}
    if info.get("size") and total != info["size"]:
        os.remove(zpath)
        return {"error": "the download was incomplete"}
    if got != want.split(":", 1)[1]:
        os.remove(zpath)
        return {"error": "the download didn't match GitHub's checksum - not installed"}
    dest = os.path.join(UPDATE_DIR, ver)
    shutil.rmtree(dest, ignore_errors=True)
    try:
        with zipfile.ZipFile(zpath) as z:
            root = os.path.realpath(dest)
            for name in z.namelist():                 # never write outside the update folder
                if not os.path.realpath(os.path.join(root, name)).startswith(root + os.sep):
                    return {"error": "the zip has an unsafe file path - not installed"}
            z.extractall(dest)
    except Exception as ex:
        return {"error": f"couldn't unpack the update ({ex})"}
    finally:
        try:
            os.remove(zpath)
        except OSError:
            pass
    src = os.path.join(dest, "OrbitBar")
    if not os.path.isfile(os.path.join(src, "OrbitBar.exe")):
        return {"error": "the zip doesn't contain OrbitBar.exe"}
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": ver, "src": src}, f)
    return {"version": ver, "src": src}


def pending_update():
    """A downloaded update newer than this copy, ready to install - or None (and stale leftovers cleaned up)."""
    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            p = json.load(f)
        if (version_tuple(p["version"]) > version_tuple(APP_VERSION)
                and os.path.isfile(os.path.join(p["src"], "OrbitBar.exe"))):
            return p
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if os.path.isdir(UPDATE_DIR):
        import shutil
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    return None


UPDATER_PS = r"""
param([int]$WaitPid, [string]$Src, [string]$Dst, [string]$Log, [string]$Ver)
function Note($m) { Add-Content -LiteralPath $Log -Value ("{0} updater: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }
try { Wait-Process -Id $WaitPid -Timeout 30 -ErrorAction SilentlyContinue } catch {}
# the crash watchdog is a second OrbitBar.exe from the same folder: wait for every copy to close
for ($i = 0; $i -lt 60; $i++) {
  $busy = Get-Process OrbitBar -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dst, 'OrdinalIgnoreCase') }
  if (-not $busy) { break }
  Start-Sleep -Milliseconds 500
}
# the program's own library folder is mirrored (old versions' files removed); top-level files are just replaced,
# so nothing else that happens to be in the folder is ever deleted
robocopy (Join-Path $Src '_internal') (Join-Path $Dst '_internal') /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
$a = $LASTEXITCODE
robocopy $Src $Dst /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
$b = $LASTEXITCODE
if ($a -lt 8 -and $b -lt 8) {
  Note "installed OrbitBar $Ver"
  Remove-Item -LiteralPath (Split-Path $Src) -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath (Join-Path (Split-Path (Split-Path $Src)) 'pending.json') -Force -ErrorAction SilentlyContinue
} else {
  Note "couldn't copy OrbitBar $Ver into $Dst (robocopy $a/$b) - starting the old version"
  # forget it, so the old version doesn't try again at every start; the next update check offers it again
  Remove-Item -LiteralPath (Join-Path (Split-Path (Split-Path $Src)) 'pending.json') -Force -ErrorAction SilentlyContinue
}
Start-Process -FilePath (Join-Path $Dst 'OrbitBar.exe') -WorkingDirectory $Dst
"""


def mark_update_attempted(p):
    """Remember that installing `p` was tried, so a failed install can't keep the bar from starting."""
    try:
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump(dict(p, attempted=True), f)
    except OSError:
        pass


def run_updater(p):
    """Start the installer for update `p`; it waits until this process (and its watchdog) has exited.
    From 5.5.4 on, the downloaded OrbitBar.exe installs itself (no PowerShell: 5.5.2/5.5.3 used a hidden
    PowerShell script, which antivirus software can silently block)."""
    import subprocess
    mark_update_attempted(p)
    flags = 0x00000008 | 0x00000200 | 0x08000000           # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    app_dir = os.path.dirname(sys.executable)
    new_exe = os.path.join(p["src"], "OrbitBar.exe")
    if version_tuple(p["version"]) >= (5, 5, 4):
        subprocess.Popen([new_exe, "--install-update", f"--from={p['src']}", f"--to={app_dir}",
                          f"--wait={os.getpid()}", f"--ver={p['version']}"],
                         creationflags=flags, close_fds=True, cwd=p["src"])
        return
    os.makedirs(UPDATE_DIR, exist_ok=True)
    script = os.path.join(UPDATE_DIR, "install-update.ps1")
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(UPDATER_PS)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", script, "-WaitPid", str(os.getpid()), "-Src", p["src"], "-Dst", app_dir,
                      "-Log", LOG_PATH, "-Ver", p["version"]],
                     creationflags=flags, close_fds=True, cwd=UPDATE_DIR)


def install_update_here():
    """`OrbitBar.exe --install-update --from=<new> --to=<program folder> --wait=<pid>`: run by the old version,
    from the downloaded copy. Wait for the old bar (and its watchdog) to close, swap the files in, start it."""
    import shutil
    import subprocess
    arg = lambda k: next((a.split("=", 1)[1] for a in sys.argv if a.startswith(f"--{k}=")), "")  # noqa: E731
    src, dst, ver = arg("from"), arg("to"), arg("ver")
    try:
        wait_pid = int(arg("wait") or 0)
    except ValueError:
        wait_pid = 0

    def note(msg):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} updater: {msg}\n")
        except OSError:
            pass

    if not (src and dst and os.path.isfile(os.path.join(src, "OrbitBar.exe")) and os.path.isdir(dst)):
        note(f"nothing to install (from {src!r} to {dst!r})")
        return
    if wait_pid:
        try:
            psutil.Process(wait_pid).wait(timeout=30)
        except Exception:
            pass
    me = os.getpid()
    dst_n = os.path.normcase(os.path.abspath(dst)) + os.sep
    for _ in range(60):                                   # the crash watchdog is another OrbitBar.exe from there
        busy = []
        for pr in psutil.process_iter(["pid", "exe"]):
            try:
                exe = pr.info.get("exe") or ""
                if pr.info["pid"] != me and os.path.normcase(exe).startswith(dst_n):
                    busy.append(pr)
            except Exception:
                continue
        if not busy:
            break
        time.sleep(0.5)
    new_int, old_int = os.path.join(src, "_internal"), os.path.join(dst, "_internal")
    backup = os.path.join(dst, "_internal.old")
    ok = False
    try:
        shutil.rmtree(backup, ignore_errors=True)
        if os.path.isdir(old_int):
            os.replace(old_int, backup)                   # keep the old libraries until the new ones are in
        shutil.copytree(new_int, old_int)
        for name in os.listdir(src):                      # top-level files: OrbitBar.exe, README, ...
            s_path = os.path.join(src, name)
            if os.path.isfile(s_path):
                for attempt in range(10):
                    try:
                        shutil.copy2(s_path, os.path.join(dst, name))
                        break
                    except OSError:
                        if attempt == 9:
                            raise
                        time.sleep(0.5)
        ok = True
    except Exception as ex:
        note(f"couldn't install OrbitBar {ver} into {dst}: {ex} - keeping the old version")
        if os.path.isdir(backup):                         # put the old libraries back
            shutil.rmtree(old_int, ignore_errors=True)
            try:
                os.replace(backup, old_int)
            except OSError:
                pass
    if ok:
        shutil.rmtree(backup, ignore_errors=True)
        note(f"installed OrbitBar {ver}")
    try:
        os.remove(PENDING_PATH)                           # done, or given up: never retried at every start
    except OSError:
        pass
    subprocess.Popen([os.path.join(dst, "OrbitBar.exe")], cwd=dst, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)


def fetch_public_ip():
    return http_json("https://api.ipify.org?format=json").get("ip")


def mask_ip(ip):
    if not ip:
        return "•••"
    if ":" in ip:
        return "••••:••••:…:" + ip.split(":")[-1]
    return "•••.•••.•••." + ip.split(".")[-1]


class NetGuardWorker(QThread):
    changed = Signal(dict)

    def run(self):
        try:
            wlan = WlanClient()
        except Exception:
            wlan = None                        # desktop without Wi-Fi
        last = None
        while not self.isInterruptionRequested():
            try:
                snap = net_snapshot(wlan)
            except Exception as ex:
                snap = {"error": str(ex), "online": True, "level": "ok", "vpn_full": False}
            sig = json.dumps(snap, sort_keys=True, default=str)
            if sig != last:
                last = sig
                self.changed.emit(snap)
            for _ in range(40):                # every 4 s, quick to stop
                if self.isInterruptionRequested():
                    break
                self.msleep(100)
        if wlan is not None:
            wlan.close()


class NetworkCard(Card):
    def __init__(self, bar, widget):
        super().__init__(bar, width=340)
        self.w = widget
        self.ip, self.ip_state, self.reveal_until = None, None, 0.0
        self.head = lbl("", "h2")
        self.head.setWordWrap(True)
        self.why = lbl("", "dim")
        self.why.setWordWrap(True)
        self.body.addWidget(self.head)
        self.body.addWidget(self.why)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        self.body.addLayout(self.rows)
        ip_row = QHBoxLayout()
        ip_row.addWidget(lbl("Public IP"))
        ip_row.addStretch(1)
        self.ip_lbl = lbl("hidden", "dim")
        self.ip_btn = text_button("Look up", "link", "Asks api.ipify.org once. Nothing is looked up unless you click.")
        self.ip_btn.clicked.connect(self.ip_click)
        ip_row.addWidget(self.ip_lbl)
        ip_row.addWidget(self.ip_btn)
        self.body.addWidget(section("Public address"))
        self.body.addLayout(ip_row)
        self.changed = lbl("", "dim")
        self.changed.setWordWrap(True)
        self.body.addWidget(self.changed)
        links = QHBoxLayout()
        for text, uri in (("Network settings", "ms-settings:network"), ("VPN settings", "ms-settings:network-vpn")):
            b = text_button(text, "link")
            b.clicked.connect(lambda _=False, u=uri: (self.hide(), os.startfile(u)))
            links.addWidget(b)
        links.addStretch(1)
        self.body.addLayout(links)

    def forget_ip(self):
        self.ip, self.ip_state, self.reveal_until = None, None, 0.0

    def ip_click(self):
        if self.ip_state in (None, "error"):
            self.ip_state = "loading"
            BgCall(fetch_public_ip, self.on_ip)
        elif self.ip_state == "ok":
            if time.monotonic() < self.reveal_until:
                self.reveal_until = 0.0
            else:
                self.reveal_until = time.monotonic() + 20          # hides itself again after 20 s
                QTimer.singleShot(20200, self.refresh)
        self.refresh()

    def on_ip(self, ip):
        self.ip, self.ip_state = ip, ("ok" if ip else "error")
        if self.isVisible():
            self.refresh()

    def row(self, name, value):
        r = QHBoxLayout()
        k = lbl(name)
        r.addWidget(k)
        r.addStretch(1)
        v = lbl(value, "dim")
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        r.addWidget(v)
        self.rows.addLayout(r)
        k.show()                    # visible in the same frame (no empty flash while the card rebuilds)
        v.show()

    def refresh(self):
        d = self.w.data or {}
        clear_layout(self.rows)
        if not d.get("online"):
            self.head.setText("No network connection")
            self.why.setText("")
        elif d.get("vpn_full"):
            self.head.setText(f"🔒 Internet traffic goes through your VPN ({d.get('vpn_name')})")
            self.why.setText("Your network and nearby people only see encrypted VPN traffic.")
        elif d.get("level") in ("open", "weak"):
            self.head.setText("⚠ " + ("Open Wi-Fi" if d["level"] == "open" else "Weak Wi-Fi security"))
            self.why.setText(d.get("why", "") + " Turn on a VPN before signing in to anything.")
        else:
            self.head.setText("Not using a VPN")
            extra = (f" {d['vpn_name']} is connected, but only for its own network, not your internet traffic."
                     if d.get("vpn_any") else "")
            self.why.setText((d.get("why") or "Your connection is direct.") + extra)
        self.why.setVisible(bool(self.why.text()))
        wifi = d.get("wifi")
        if wifi:
            sec = WIFI_AUTH.get(wifi["auth"], "Unknown security")
            self.row("Wi-Fi", f"{wifi['ssid']} · {sec} · {wifi['quality']}%")
        elif d.get("kind") == "ethernet":
            self.row("Connection", f"Ethernet ({d.get('uplink')})")
        elif d.get("kind") == "wifi":
            self.row("Connection", f"Wi-Fi ({d.get('uplink')})")
        if d.get("ips"):
            self.row("This PC", ", ".join(d["ips"]))
        if d.get("gateway"):
            self.row("Router", d["gateway"])
        if d.get("dns"):
            names = [f"{x} ({KNOWN_DNS[x]})" if x in KNOWN_DNS else x for x in d["dns"][:2]]
            self.row("DNS", ", ".join(names))
        st = self.ip_state
        if st == "loading":
            self.ip_lbl.setText("looking up…")
            self.ip_btn.setText("")
        elif st == "error":
            self.ip_lbl.setText("couldn't reach the lookup service")
            self.ip_btn.setText("Retry")
        elif st == "ok":
            shown = time.monotonic() < self.reveal_until
            self.ip_lbl.setText(self.ip if shown else mask_ip(self.ip))
            self.ip_btn.setText("Hide" if shown else "Show")
        else:
            self.ip_lbl.setText("not looked up")
            self.ip_btn.setText("Look up")
        self.ip_btn.setVisible(bool(self.ip_btn.text()))
        self.changed.setText(self.w.change_text())
        self.changed.setVisible(bool(self.changed.text()))


class NetGuardWidget(QWidget):
    """Bar shows status only - never an address: a green lock with a VPN, an amber/red warning on open or
    weak Wi-Fi, otherwise a quiet network icon."""
    GLYPH = {"wifi": "\uE701", "ethernet": "\uE839", None: "\uE701"}

    def __init__(self, bar):
        super().__init__()
        self.bar = bar
        self.data = None
        self.hover = False
        self.changed_at, self.change_desc = None, ""
        self.setFixedSize(uw(30), 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Network: checking…")
        self.card = NetworkCard(bar, self)
        self.worker = NetGuardWorker()
        self.worker.changed.connect(self.on_change)
        self.worker.start()

    def rescale(self):
        self.setFixedSize(uw(30), 36)
        self.update()

    def stop(self):
        self.worker.requestInterruption()
        self.worker.wait(1500)

    @staticmethod
    def where(d):
        if not d or not d.get("online"):
            return "offline"
        w = d.get("wifi")
        return f"Wi-Fi “{w['ssid']}”" if w else ("Ethernet" if d.get("kind") == "ethernet" else d.get("uplink") or "network")

    def change_text(self):
        if not self.changed_at:
            return ""
        mins = int((time.monotonic() - self.changed_at) // 60)
        ago = "just now" if mins < 1 else (f"{mins} min ago" if mins < 60 else f"{mins // 60} h ago")
        return f"Network changed {ago}: {self.change_desc}"

    def on_change(self, d):
        prev, self.data = self.data, d
        if prev is not None:
            a, b = (self.where(prev), prev.get("vpn_full")), (self.where(d), d.get("vpn_full"))
            if a != b:
                parts = []
                if a[0] != b[0]:
                    parts.append(f"{a[0]} → {b[0]}")
                if a[1] != b[1]:
                    parts.append("VPN on" if b[1] else "VPN off")
                self.changed_at, self.change_desc = time.monotonic(), ", ".join(parts)
                self.card.forget_ip()                     # a new network means a new public address
        alerts = self.bar.cfg.get("alerts", {}).get("network", True)
        if alerts and d.get("online"):
            bad = d.get("level") in ("open", "weak") and not d.get("vpn_full")
            was_bad = prev is not None and prev.get("level") in ("open", "weak") and not prev.get("vpn_full") \
                and self.where(prev) == self.where(d)
            if bad and not was_bad:
                self.bar.toast.notify("⚠ " + ("Open Wi-Fi" if d["level"] == "open" else "Weak Wi-Fi security")
                                      + f": {d['wifi']['ssid'] if d.get('wifi') else ''}",
                                      d.get("why", "") + " Turn on your VPN before signing in to anything.", self)
            if prev is not None and prev.get("vpn_full") and not d.get("vpn_full"):
                self.bar.toast.notify("🔓 VPN disconnected", f"Internet traffic now goes directly through "
                                      f"{self.where(d)}.", self)
        self.setToolTip(self.tip())
        self.update()
        if self.card.isVisible():
            self.card.refresh()
            self.card.reposition()

    def tip(self):
        d = self.data or {}
        if not d.get("online"):
            return "No network connection"
        if d.get("vpn_full"):
            head = f"VPN on ({d.get('vpn_name')})"
        elif d.get("level") in ("open", "weak"):
            head = "Open Wi-Fi - not encrypted" if d["level"] == "open" else "Weak Wi-Fi security (WEP)"
        else:
            head = "No VPN" + (" · older Wi-Fi security" if d.get("level") == "old" else "")
        return f"{head} · {self.where(d)}\nClick for details"

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.card.open_from(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*T["hover"]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 3, -1, -3), 8, 8)
        d = self.data or {}
        f = QFont(GLYPH_FONT.split(",")[0])
        f.setPixelSize(uw(15))
        p.setFont(f)
        if d.get("vpn_full"):
            glyph, color = "\uE72E", QColor("#22c55e")                 # lock
        elif d.get("online") and d.get("level") in ("open", "weak"):
            glyph, color = "\uE7BA", QColor("#f87171" if d["level"] == "weak" else "#f59e0b")   # warning
        else:
            glyph = self.GLYPH.get(d.get("kind"), "\uE701")
            color = QColor("#f59e0b") if d.get("level") == "old" else QColor(DIM)
        p.setPen(color)
        p.drawText(self.rect(), Qt.AlignCenter, glyph)
        if self.data is not None and not d.get("online"):                   # offline: strike through
            p.setPen(QPen(QColor("#f87171"), 1.6))
            c = self.rect().center()
            k = uw(8)
            p.drawLine(QPoint(c.x() - k, c.y() + k), QPoint(c.x() + k, c.y() - k))


# id: name, category, description, group (widgets of the same group share one pill), maker, variants, attr
WIDGETS = OrderedDict([
    ("start", dict(name="Start", cat="Navigation", group="nav", attr="start",
                   desc="Opens Start. Right-click for the power menu.",
                   make=lambda b: StartButton(b.cfg["accent"], b))),
    ("search", dict(name="Quick launcher", cat="Navigation", group="nav", attr="search_btn",
                    desc="Search apps, folders and paths. Right-click for Windows Search.", make=_make_search)),
    ("workspaces", dict(name="Workspaces", cat="Navigation", group=None, attr="workspaces",
                        desc="Virtual desktops: click to switch, + to add, middle-click to close.",
                        make=lambda b: Workspaces(b))),
    ("study", dict(name="Classes, deadlines & exams", cat="Study", group=None, attr="study_chips",
                   desc="Next class, next deadline and exam countdown from your school calendar.",
                   make=lambda b: StudyChips(b, b.study))),
    ("timer", dict(name="Focus mode & timer", cat="Study", group="tools", attr="timer_w",
                   desc="Pomodoro focus sessions, quick timers and study stats.",
                   make=lambda b: TimerWidget(b))),
    ("weather", dict(name="Weather", cat="Time & weather", group="tools", attr="weather",
                     desc="Current weather. Click for hourly and 5-day forecast.",
                     make=lambda b: WeatherWidget(b))),
    ("recycle", dict(name="Recycle Bin", cat="System", group="tools", attr="recycle",
                     desc="Item count. Click to open, right-click to empty.",
                     make=lambda b: RecycleBinWidget(b))),
    ("privacy", dict(name="Camera / mic indicator", cat="Security", group=None, attr="privacy",
                     desc="Appears while an app uses your camera or microphone, and shows which one.",
                     make=lambda b: PrivacyWidget(b))),
    ("media", dict(name="Now playing", cat="Media", group=None, attr="media",
                   desc="Song or video playing in any app or tab, with controls and a hover card.",
                   make=lambda b: Media(b))),
    ("cpu", dict(name="CPU", cat="System", group="system", attr="cpu_w",
                 desc="Processor load.", variants=[("graph", "Graph"), ("number", "Number"), ("ring", "Ring")],
                 make=lambda b: MetricWidget(b, "cpu", b.variant("cpu", "graph")))),
    ("ram", dict(name="Memory", cat="System", group="system", attr="ram_w",
                 desc="Memory in use.", variants=[("number", "Number"), ("ring", "Ring")],
                 make=lambda b: MetricWidget(b, "ram", b.variant("ram", "number")))),
    ("gpu", dict(name="GPU", cat="System", group="system", attr="gpu_w",
                 desc="Graphics card load (NVIDIA, AMD, Intel).", variants=[("number", "Number"), ("ring", "Ring")],
                 make=lambda b: MetricWidget(b, "gpu", b.variant("gpu", "number")))),
    ("network", dict(name="Network speed", cat="System", group="system", attr="net_w",
                     desc="Download and upload speed.",
                     make=lambda b: MetricWidget(b, "network"))),
    ("tray", dict(name="System tray", cat="System", group="status", attr="tray",
                  desc="Background apps (WhatsApp, Telegram, Steam…) with their own menus.", make=_make_tray)),
    ("security", dict(name="Security shield", cat="Security", group="status", attr="security",
                      desc="Antivirus and firewall status: green, amber or red.",
                      make=lambda b: SecurityWidget(b))),
    ("netguard", dict(name="Network safety", cat="Security", group="status", attr="netguard",
                      desc="VPN on/off and Wi-Fi safety (warns on open Wi-Fi). Your IP is never shown on the bar.",
                      make=lambda b: NetGuardWidget(b))),
    ("battery", dict(name="Battery", cat="System", group="status", attr="battery",
                     desc="Charge level with charging animation (laptops only).",
                     variants=[("icon", "Icon"), ("ring", "Ring"), ("gauge", "Gauge"), ("dots", "Dots")],
                     make=lambda b: BatteryWidget(b) if BatteryWidget.available() else None)),
    ("volume", dict(name="Volume", cat="Media", group="status", attr="volume_btn",
                    desc="Scroll to change, click for a slider, middle-click to mute.",
                    make=lambda b: VolumeButton(b))),
    ("worldclock", dict(name="World clock", cat="Time & weather", group="status", attr="worldclock",
                        desc="A second time zone (the first one in Settings › World clocks).",
                        make=lambda b: WorldClockWidget(b))),
    ("clock", dict(name="Clock", cat="Time & weather", group="status", attr="clock",
                   desc="Time and date. Click for calendar and world clocks.",
                   variants=[("digital", "Digital"), ("analog", "Analog"), ("flip", "Flip")],
                   make=lambda b: Clock(b, b.variant("clock", "digital")))),
])
CATEGORIES = ["Navigation", "Study", "System", "Media", "Security", "Time & weather"]
DEFAULT_LAYOUT = {"left": ["start", "search", "workspaces", "weather", "timer", "recycle", "study", "privacy"],
                  "right": ["media", "cpu", "ram", "gpu", "network", "tray", "security", "netguard", "battery",
                            "volume", "clock"]}
NEW_WIDGETS = [("netguard", "security")]       # (widget, put it after) - offered once to existing layouts


def migrate_layout(cfg):
    """Older configs had on/off switches; turn them into a layout once."""
    if isinstance(cfg.get("layout"), dict) and "left" in cfg["layout"]:
        lay = cfg["layout"]
    else:
        show = cfg.get("show", {})
        flag = {"weather": "weather", "timer": "timer", "recycle": "recycle_bin", "study": "study",
                "privacy": "privacy", "media": "media", "cpu": "stats", "ram": "stats", "gpu": "gpu",
                "network": "network", "tray": "tray", "security": "security", "battery": "battery",
                "volume": "volume", "clock": "clock", "workspaces": "workspaces"}
        stats_on = show.get("stats", True)

        def on(w):
            if w in ("gpu", "network") and not stats_on:
                return False
            return show.get(flag.get(w, w), True) if w in flag else True
        lay = {side: [w for w in ids if on(w)] for side, ids in DEFAULT_LAYOUT.items()}
        cfg["layout"] = lay
    offered = cfg.setdefault("offered_widgets", [])
    for wid, after in NEW_WIDGETS:          # new widgets appear once on existing bars; removing them sticks
        if wid in offered:
            continue
        offered.append(wid)
        if wid in lay.get("left", []) or wid in lay.get("right", []):
            continue
        side = "left" if after in lay.get("left", []) else "right"
        ids = lay.setdefault(side, [])
        ids.insert(ids.index(after) + 1 if after in ids else len(ids), wid)
    seen = set()
    for side in ("left", "right"):          # drop unknown / duplicate ids
        lay[side] = [w for w in lay.get(side, []) if w in WIDGETS and not (w in seen or seen.add(w))]
    var = cfg.setdefault("widget_variants", {})
    var.setdefault("battery", cfg.get("battery", {}).get("style", "icon"))
    return lay


class WidgetSlot(QWidget):
    """Holds one widget on the bar. In edit mode it gets an outline, a remove button and can be dragged."""

    def __init__(self, bar, wid, widget):
        super().__init__()
        self.bar, self.wid, self.widget = bar, wid, widget
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(widget)
        self.overlay = SlotOverlay(self)
        self.overlay.hide()

    def set_edit(self, on):
        self.overlay.setVisible(on)
        # in edit mode even "empty" widgets (no camera in use, nothing due…) need a visible handle
        self.setMinimumWidth(64 if on and not self.widget.isVisibleTo(self) else 0)
        if on:
            self.overlay.raise_()
            self.overlay.setGeometry(self.rect())

    def resizeEvent(self, e):
        self.overlay.setGeometry(self.rect())
        super().resizeEvent(e)


class SlotOverlay(QWidget):
    def __init__(self, slot):
        super().__init__(slot)
        self.slot = slot
        self.press = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"{WIDGETS[slot.wid]['name']} · drag to move · × to remove")

    def close_rect(self):
        return QRectF(self.width() - 15, 1, 14, 14)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(self.slot.bar.cfg["accent"])
        fill = QColor(accent)
        fill.setAlpha(28)
        p.setBrush(fill)
        pen = QPen(accent, 1.3, Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 2, -1, -2), 8, 8)
        if not self.slot.widget.isVisibleTo(self.slot):
            p.setPen(QColor(DIM))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(self.rect().adjusted(4, 0, -12, 0), Qt.AlignCenter, WIDGETS[self.slot.wid]["name"].split()[0])
        r = self.close_rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ef4444"))
        p.drawEllipse(r)
        p.setPen(QPen(QColor("white"), 1.6))
        p.drawLine(QPoint(int(r.left() + 4), int(r.top() + 4)), QPoint(int(r.right() - 4), int(r.bottom() - 4)))
        p.drawLine(QPoint(int(r.right() - 4), int(r.top() + 4)), QPoint(int(r.left() + 4), int(r.bottom() - 4)))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.close_rect().contains(e.position()):
                self.slot.bar.remove_widget(self.slot.wid)
                return
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.press is None or (e.position().toPoint() - self.press).manhattanLength() < QApplication.startDragDistance():
            return
        self.press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(WIDGET_MIME, self.slot.wid.encode())
        drag.setMimeData(mime)
        drag.setPixmap(self.slot.grab())
        drag.setHotSpot(QPoint(self.width() // 2, self.height() // 2))
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, e):
        self.press = None


class WidgetColumn(QWidget):
    """Left or right side of the bar. Accepts widgets dragged in edit mode (or from the gallery)."""

    def __init__(self, bar, side):
        super().__init__()
        self.bar, self.side = bar, side
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.drop_x = None
        self.setAcceptDrops(True)

    def slots_in_order(self):
        out = []
        for i in range(self.lay.count()):
            w = self.lay.itemAt(i).widget()
            if isinstance(w, WidgetSlot):
                out.append(w)
            elif isinstance(w, Pill):
                for j in range(w.lay.count()):
                    s = w.lay.itemAt(j).widget()
                    if isinstance(s, WidgetSlot):
                        out.append(s)
        return out

    def index_at(self, x):
        n = 0
        for s in self.slots_in_order():
            if s.isHidden():
                continue
            c = s.mapTo(self, QPoint(s.width() // 2, 0)).x()
            if c < x:
                n += 1
        return n

    def dragEnterEvent(self, e):
        if self.bar.edit_mode and e.mimeData().hasFormat(WIDGET_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self.bar.edit_mode and e.mimeData().hasFormat(WIDGET_MIME):
            slots = [s for s in self.slots_in_order() if not s.isHidden()]
            idx = self.index_at(e.position().x())
            if not slots:
                self.drop_x = self.width() - 4 if self.side == "right" else 4
            elif idx >= len(slots):
                self.drop_x = slots[-1].mapTo(self, QPoint(slots[-1].width(), 0)).x() + 3
            else:
                self.drop_x = slots[idx].mapTo(self, QPoint(0, 0)).x() - 3
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop_x = None
        self.update()

    def dropEvent(self, e):
        wid = bytes(e.mimeData().data(WIDGET_MIME)).decode()
        idx = self.index_at(e.position().x())
        self.drop_x = None
        self.update()
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.bar.move_widget(wid, self.side, idx))

    def paintEvent(self, e):
        if self.drop_x is None:
            return
        p = QPainter(self)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.bar.cfg["accent"]))
        p.drawRoundedRect(QRectF(self.drop_x - 1.5, 4, 3, self.height() - 8), 1.5, 1.5)



# ---- Widget gallery (right-click › Customize widgets…) ----------------------------------
# Widgets whose preview would start something heavy or intrusive get a drawn mock-up instead
STATIC_PREVIEWS = {"tray": "⌃  WhatsApp · Telegram · Steam…", "privacy": "🟢📷  🟠🎙  Zoom",
                   "media": "♪  Song — Artist   ⏮ ⏯ ⏭", "security": "🛡  Protected", "netguard": "🔒  VPN on"}
SAFE_PREVIEW = {"start", "search", "workspaces", "study", "timer", "weather", "recycle", "cpu", "ram", "gpu",
                "network", "battery", "volume", "worldclock", "clock"}


class PreviewTile(QLabel):
    """Live picture of a widget; drag it onto the bar to add it there."""

    def __init__(self, gallery, wid):
        super().__init__()
        self.gallery, self.wid = gallery, wid
        self.setFixedHeight(54)
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)     # never let the picture widen the card
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag onto the bar")
        self.press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.press is None or (e.position().toPoint() - self.press).manhattanLength() < QApplication.startDragDistance():
            return
        self.press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(WIDGET_MIME, self.wid.encode())
        drag.setMimeData(mime)
        pm = self.pixmap()
        if pm and not pm.isNull():
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2 // max(1, int(pm.devicePixelRatio())), 20))
        drag.exec(Qt.CopyAction | Qt.MoveAction)

    def mouseReleaseEvent(self, e):
        self.press = None


class WidgetCard(QFrame):
    def __init__(self, gallery, wid):
        super().__init__()
        self.gallery, self.bar, self.wid = gallery, gallery.bar, wid
        d = WIDGETS[wid]
        self.setObjectName("wcard")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(lbl(d["name"], "h2"))
        top.addStretch(1)
        self.status = lbl("", "dim")
        top.addWidget(self.status)
        v.addLayout(top)
        desc = lbl(d["desc"], "dim")
        desc.setWordWrap(True)
        v.addWidget(desc)
        self.preview = PreviewTile(gallery, wid)
        v.addWidget(self.preview)
        self.variant_btns = {}
        if d.get("variants"):
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(lbl("Style", "dim"))
            for key, label in d["variants"]:
                b = text_button(label, "seg")
                b.clicked.connect(lambda _=False, k=key: self.pick(k))
                row.addWidget(b)
                self.variant_btns[key] = b
            row.addStretch(1)
            v.addLayout(row)
        btns = QHBoxLayout()
        self.add_l = text_button("＋ Add left", "chip")
        self.add_r = text_button("＋ Add right", "chip")
        self.remove = text_button("Remove", "chip")
        self.add_l.clicked.connect(lambda: self.bar.add_widget(wid, "left"))
        self.add_r.clicked.connect(lambda: self.bar.add_widget(wid, "right"))
        self.remove.clicked.connect(lambda: self.bar.remove_widget(wid))
        for b in (self.add_l, self.add_r, self.remove):
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        self.sync()

    def pick(self, key):
        if self.wid not in self.bar.slots and self.wid in SAFE_PREVIEW:
            self.bar.instance(self.wid)
        self.bar.set_variant(self.wid, key)
        self.sync()

    def sync(self):
        lay = self.bar.cfg["layout"]
        side = "left" if self.wid in lay["left"] else "right" if self.wid in lay["right"] else None
        missing = side and self.wid not in self.bar.slots and self.bar.instance(self.wid) is None
        self.status.setText("Not available on this PC" if missing else f"● On the bar ({side})" if side else "Not added")
        self.status.setStyleSheet(f"color: {self.bar.cfg['accent']};" if side else "")
        self.add_l.setVisible(side is None)
        self.add_r.setVisible(side is None)
        self.remove.setVisible(side is not None)
        cur = self.bar.variant(self.wid, (WIDGETS[self.wid].get("variants") or [[None]])[0][0])
        if self.wid == "battery":
            cur = self.bar.cfg.get("battery", {}).get("style", cur)
        for k, b in self.variant_btns.items():
            b.setProperty("class", "seg-on" if k == cur else "seg")
            b.style().unpolish(b)
            b.style().polish(b)
        self.update_preview()

    def update_preview(self):
        w = None
        if self.wid in self.bar.slots:
            w = self.bar.slots[self.wid].widget
        elif self.wid in SAFE_PREVIEW and self.wid != "battery":
            w = self.bar.instance(self.wid)                 # lightweight: create it so the preview is real
        dpr = self.devicePixelRatioF()
        width = max(200, min(self.preview.width(), 420)) if self.preview.width() > 50 else 320
        canvas = QPixmap(int(width * dpr), int(54 * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(*T["border"]), 1))
        p.setBrush(QColor(*T["bg"][:3], 255))
        p.drawRoundedRect(QRectF(0.5, 0.5, width - 1, 53), 12, 12)
        shot = None
        if w is not None and self.wid not in STATIC_PREVIEWS and w.isVisibleTo(w.parentWidget() or w):
            if not w.isVisible():
                w.resize(w.sizeHint().expandedTo(w.minimumSize()))
            shot = QPixmap(int(w.width() * dpr), int(w.height() * dpr))
            shot.setDevicePixelRatio(dpr)
            shot.fill(Qt.transparent)                       # render, not grab: no white background
            w.render(shot, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        if shot is not None and not shot.isNull():
            sw, sh = shot.width() / shot.devicePixelRatio(), shot.height() / shot.devicePixelRatio()
            scale = min(1.0, (width - 16) / max(sw, 1))
            tw, th = sw * scale, sh * scale
            p.drawPixmap(QRectF((width - tw) / 2, (54 - th) / 2, tw, th), shot, QRectF(shot.rect()))
        else:
            text = STATIC_PREVIEWS.get(self.wid) or ("Not available on this PC" if self.wid == "battery" else
                                                      "Shows up when there's something to show")
            p.setPen(QColor(TEXT if self.wid in STATIC_PREVIEWS else DIM))
            p.drawText(QRectF(0, 0, width, 54), Qt.AlignCenter, text)
        p.end()
        self.preview.setPixmap(canvas)


class Gallery(QWidget):
    def __init__(self, bar):
        super().__init__(None, Qt.Window)
        self.bar = bar
        self.setWindowTitle("Customize OrbitBar")
        self.resize(780, 720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QFrame()
        head.setObjectName("ghead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 14, 16, 12)
        t = QVBoxLayout()
        t.addWidget(lbl("Widgets", "h1"))
        t.addWidget(lbl("Add widgets, pick a style, and drag them around on the bar.  × removes one.", "dim"))
        hl.addLayout(t, 1)
        reset = text_button("Reset to default", "link")
        reset.clicked.connect(self.reset)
        done = text_button("Done", "primary")
        done.clicked.connect(self.close)
        hl.addWidget(reset)
        hl.addWidget(done)
        outer.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("gpage")
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 6, 20, 20)
        v.setSpacing(8)
        self.cards = []
        for cat in CATEGORIES:
            ids = [w for w, d in WIDGETS.items() if d["cat"] == cat]
            if not ids:
                continue
            v.addWidget(section(cat))
            grid = QGridLayout()
            grid.setSpacing(10)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            for i, wid in enumerate(ids):
                c = WidgetCard(self, wid)
                self.cards.append(c)
                grid.addWidget(c, i // 2, i % 2)
            v.addLayout(grid)
        v.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        bar.restyle(self)
        a = bar.cfg["accent"]
        solid = rgba(T["card"][:3] + (255,))
        self.setStyleSheet(self.styleSheet() + f"""
            QWidget#gpage, Gallery, QScrollArea {{ background: {solid}; }}
            QFrame#ghead {{ background: {solid}; border-bottom: 1px solid {rgba(T['border'])}; }}
            QFrame#wcard {{ background: {rgba(T['pill'])}; border: 1px solid {rgba(T['border'])}; border-radius: 12px; }}
            QPushButton[class="seg"] {{ background: {rgba(T['hover'])}; padding: 4px 10px; border-radius: 7px; }}
            QPushButton[class="seg-on"] {{ background: {a}; color: #0d0d14; font-weight: 600; padding: 4px 10px;
                                           border-radius: 7px; }}
            QPushButton[class="primary"] {{ background: {a}; color: #0d0d14; font-weight: 600; padding: 7px 18px; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {rgba(T['hover'])}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)
        self.live = QTimer(self, interval=1000, timeout=self.refresh_previews)

    def sync(self):
        for c in self.cards:
            c.sync()

    def refresh_previews(self):
        for c in self.cards:
            if c.wid in ("clock", "cpu", "ram", "gpu", "network", "worldclock", "timer", "battery", "study", "media"):
                c.update_preview()

    def reset(self):
        self.bar.cfg["layout"] = json.loads(json.dumps(DEFAULT_LAYOUT))
        self.bar.cfg["widget_variants"] = {}
        self.bar.cfg.setdefault("battery", {})["style"] = "icon"
        for wid, slot in self.bar.slots.items():
            if hasattr(slot.widget, "set_variant"):
                default = (WIDGETS[wid].get("variants") or [[None]])[0][0]
                if default:
                    slot.widget.set_variant(default)
        self.bar.build_columns()
        self.bar.save_layout()
        self.sync()

    def open(self):
        self.bar.set_edit_mode(True)
        self.show()
        self.raise_()
        self.activateWindow()
        hwnd = int(self.winId())
        if T.get("text", "#fff").lower() != "#1c1c26":
            on = ctypes.c_int(1)
            dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
        user32.SwitchToThisWindow(hwnd, True)
        QTimer.singleShot(50, self.sync)
        self.live.start()

    def closeEvent(self, e):
        self.live.stop()
        self.bar.set_edit_mode(False)
        self.bar.save_layout()
        super().closeEvent(e)


# --------------------------------------------------------------------------------------
# The bar
# --------------------------------------------------------------------------------------
class Bar(QWidget):
    def __init__(self, cfg, taskbar):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.cfg, self.taskbar = cfg, taskbar
        apply_sizes(cfg)
        self.apply_stylesheet()               # one style sheet for the whole app, set before any widget exists
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(APP_NAME)
        self.appbar = None
        self.fs_hidden = False
        self.ah_hidden = False
        self.ah_away = 0
        self.effects_done = False
        self.gpu_data = None

        self.previews = Previews(self) if cfg.get("previews", True) else None
        self.toast = ToastManager(self)
        self._search_card = None                  # built on first use
        QTimer.singleShot(5000, lambda: self.search_card)    # quietly, once the bar is up
        self.update_info, self._update_told = None, None
        self.update_ready, self._updating = None, False      # a downloaded, checked update waiting to install
        self.update_timer = QTimer(self, interval=12 * 3600 * 1000, timeout=self.check_updates)
        if GITHUB_REPO:
            self.update_timer.start()
            QTimer.singleShot(60_000, self.check_updates)     # a minute after start, then twice a day

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 3, 8, 3)
        root.setSpacing(8)
        # The bar's size is set by Windows (the reserved strip), never by its contents. Without this, too
        # many widgets made Qt stretch the window past the screen and the two fought = constant flicker.
        root.setSizeConstraint(QLayout.SetNoConstraint)
        self.fit_timer = QTimer(self, singleShot=True, interval=0, timeout=self.fit)

        # shared data used by several widgets
        study_cfg = cfg.setdefault("study", {})
        for k, v in DEFAULT_STUDY.items():
            study_cfg.setdefault(k, v)
        self.study = StudyData(self)
        self.tracker = ActivityTracker(self, self.study)
        self.sys_source = SystemSource(self)
        self.system_card = None
        self.calendar_card = None

        # every widget attribute exists (None until the widget is on the bar)
        for d in WIDGETS.values():
            setattr(self, d["attr"], None)
        self.slots = {}                       # id -> WidgetSlot (kept alive when removed, for instant re-adding)
        self.edit_mode = False
        self.stash = QWidget(self)            # parks removed widgets: hidden, but still styled like the bar
        self.stash.hide()
        migrate_layout(cfg)

        # Left column | apps | right column.  Both side columns get the same stretch, so
        # the apps stay dead-centre no matter how wide the widgets on either side are.
        self.cols = {"left": WidgetColumn(self, "left"), "right": WidgetColumn(self, "right")}
        root.addWidget(self.cols["left"], 1)
        self.apps = AppsArea(self)
        root.addWidget(self.apps, 0)
        root.addWidget(self.cols["right"], 1)
        self.build_columns()
        self.study.changed.connect(self.fit_timer.start)

        self.restyle(self)

        self.settings_win = None
        self.win_events = None
        self.refresh_soon = QTimer(self, singleShot=True, interval=30, timeout=self.refresh_windows)
        self.titles_timer = QTimer(self, singleShot=True, interval=350, timeout=lambda: self.apps.refresh_titles())
        self.fast_ticks = 0
        self.fast = QTimer(self, interval=700, timeout=self.tick_fast)
        self.fs_timer = QTimer(self, interval=250, timeout=self.check_fullscreen)   # leaving fullscreen inside a
        self.fs_timer.start()                                                    # browser fires no window event
        self.slow = QTimer(self, interval=1000, timeout=self.tick_slow)
        self.fast.start()
        self.slow.start()
        self.slow_ticks = 0
        self.ah_timer = None
        if cfg.get("autohide"):
            self.ah_timer = QTimer(self, interval=120, timeout=self.tick_autohide)
            self.ah_timer.start()
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(140)

    # ---- layout engine ----
    def variant(self, wid, default):
        return self.cfg.setdefault("widget_variants", {}).get(wid, default)

    def instance(self, wid):
        """The widget for this id, created on first use (None if it can't exist here, e.g. no battery)."""
        if wid in self.slots:
            return self.slots[wid].widget
        failed = self.__dict__.setdefault("_failed", set())
        if wid in failed:                     # don't retry things that can't exist here (battery, tray…)
            return None
        try:
            w = WIDGETS[wid]["make"](self)
        except Exception as ex:
            print(f"widget {wid} failed:", ex)
            w = None
        if w is None:
            failed.add(wid)
            return None
        setattr(self, WIDGETS[wid]["attr"], w)
        slot = WidgetSlot(self, wid, w)
        slot.setParent(self.stash)            # styled like the bar even before it's placed
        self.slots[wid] = slot
        return w

    def build_columns(self):
        """(Re)arrange the side columns from cfg['layout']. Existing widgets are moved, not recreated,
        so timers, music, tray icons etc. keep running while you customise."""
        lay = migrate_layout(self.cfg)
        for side, col in self.cols.items():
            while col.lay.count():
                it = col.lay.takeAt(0)
                w = it.widget()
                if isinstance(w, Pill) and not isinstance(w, (Workspaces, Media)):   # a group pill: free its slots
                    while w.lay.count():
                        sw = w.lay.takeAt(0).widget()
                        if isinstance(sw, WidgetSlot):
                            sw.setParent(self.stash)
                    w.setParent(None)
                    w.deleteLater()
                elif w is not None:
                    w.setParent(self.stash)
        for side in ("left", "right"):
            col = self.cols[side]
            if side == "right":
                col.lay.addStretch(1)
            group, pill = None, None
            for wid in lay[side]:
                if self.instance(wid) is None:
                    continue
                slot = self.slots[wid]
                g = WIDGETS[wid]["group"]
                if g and g == group and pill is not None:
                    pill.lay.addWidget(slot)
                elif g:
                    pill = Pill(spacing=2)
                    pill.lay.addWidget(slot)
                    col.lay.addWidget(pill)
                    pill.show()
                    group = g
                else:
                    col.lay.addWidget(slot)
                    group, pill = None, None
                slot.show()
                slot.set_edit(self.edit_mode)
            if side == "left":
                col.lay.addStretch(1)
        self.restyle(self)
        if hasattr(self, "fit_timer"):
            self.fit_timer.start()

    def on_bar(self, wid):
        lay = self.cfg["layout"]
        return wid in lay["left"] or wid in lay["right"]

    def save_layout(self):
        save_config(self.cfg)

    def remove_widget(self, wid):
        for side in ("left", "right"):
            if wid in self.cfg["layout"][side]:
                self.cfg["layout"][side].remove(wid)
        self.build_columns()
        self.save_layout()
        self.layout_changed()

    def add_widget(self, wid, side):
        if self.on_bar(wid):
            return
        if self.instance(wid) is None:
            self.toast.notify("Can't add " + WIDGETS[wid]["name"], "It isn't available on this PC.", self)
            return
        self.cfg["layout"][side].append(wid) if side == "left" else self.cfg["layout"][side].insert(0, wid)
        self.build_columns()
        self.save_layout()
        self.layout_changed()

    def move_widget(self, wid, side, idx):
        """Drop handler: put widget `wid` at visible position `idx` in column `side`."""
        if self.instance(wid) is None:
            self.toast.notify("Can't add " + WIDGETS[wid]["name"], "It isn't available on this PC.", self)
            return
        col = self.cols[side]
        present = [sl.wid for sl in col.slots_in_order() if not sl.isHidden()]
        if wid in present and present.index(wid) < idx:
            idx -= 1
        present = [w for w in present if w != wid]
        for sd in ("left", "right"):
            if wid in self.cfg["layout"][sd]:
                self.cfg["layout"][sd].remove(wid)
        target = self.cfg["layout"][side]
        if idx < len(present):
            target.insert(target.index(present[idx]), wid)
        elif present:
            target.insert(target.index(present[-1]) + 1, wid)
        else:
            target.append(wid)
        self.instance(wid)
        self.build_columns()
        self.save_layout()
        self.layout_changed()

    def set_variant(self, wid, v):
        self.cfg.setdefault("widget_variants", {})[wid] = v
        if wid == "battery":
            self.cfg.setdefault("battery", {})["style"] = v
        w = self.slots[wid].widget if wid in self.slots else None
        if w is not None and hasattr(w, "set_variant"):
            w.set_variant(v)
        elif w is not None:
            w.update()
        self.save_layout()
        self.layout_changed()

    def set_edit_mode(self, on):
        self.edit_mode = on
        for slot in self.slots.values():
            slot.set_edit(on)
        if not on:
            for col in self.cols.values():
                col.drop_x = None
                col.update()
        self.fit_timer.start()          # edit mode shows tucked-away widgets so you can rearrange them

    # ---- too many widgets for the screen: tuck the least important ones away instead of overflowing
    SQUEEZE_ORDER = ("gpu", "network", "worldclock", "recycle", "weather", "ram", "search", "workspaces",
                     "security", "cpu", "privacy", "timer", "tray", "media", "battery", "volume")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "fit_timer") and e.size().width() != e.oldSize().width():
            self.fit_timer.start()

    def needed_width(self):
        """Width the bar's contents want, measured fresh (Qt's layout caches lag behind show/hide)."""
        def lay_width(lay, visible):
            m = lay.contentsMargins()
            ws = [w.sizeHint().width() for w in visible]
            return m.left() + m.right() + sum(ws) + max(0, len(ws) - 1) * max(0, lay.spacing())

        def slot_width(slot):
            inner = slot.widget
            return 0 if inner.isHidden() else max(inner.sizeHint().width(), inner.minimumWidth(), slot.minimumWidth())

        total = 0
        for col in self.cols.values():
            parts = []
            for i in range(col.lay.count()):
                w = col.lay.itemAt(i).widget()
                if w is None:
                    continue
                if isinstance(w, Pill) and not isinstance(w, (Workspaces, Media)):
                    slots = [w.lay.itemAt(j).widget() for j in range(w.lay.count())]
                    slots = [x for x in slots if isinstance(x, WidgetSlot) and not x.isHidden()]
                    w.setVisible(bool(slots))
                    if slots:
                        m = w.lay.contentsMargins()
                        sw = [slot_width(x) for x in slots]
                        parts.append(m.left() + m.right() + sum(sw) + (len(sw) - 1) * max(0, w.lay.spacing()))
                elif isinstance(w, WidgetSlot):
                    if not w.isHidden() and not w.widget.isHidden():
                        parts.append(slot_width(w))
                elif not w.isHidden():
                    parts.append(w.sizeHint().width())
            total += sum(parts) + max(0, len(parts) - 1) * max(0, col.lay.spacing())
        root = self.layout()
        m = root.contentsMargins()
        return m.left() + m.right() + 2 * root.spacing() + self.apps.sizeHint().width() + total

    def fit(self):
        """Decides from size hints only (never from current positions), so it can't oscillate."""
        if getattr(self, "_fitting", False) or not self.isVisible() or not getattr(self, "native_rect", None):
            return
        self._fitting = True
        self.note_activity("fit")
        self.setUpdatesEnabled(False)          # all show/hide below lands in one paint, never mid-way
        try:
            for slot in self.slots.values():
                if getattr(slot, "squeezed", False):
                    slot.squeezed = False
                    if self.on_bar(slot.wid):
                        slot.show()
            if self.study_chips:
                self.study_chips.set_compact(False)
            avail = self.width()
            if self.needed_width() <= avail:
                return
            if self.study_chips:
                self.study_chips.set_compact(True)
            hidden = []
            for wid in self.SQUEEZE_ORDER:
                if self.needed_width() <= avail:
                    break
                slot = self.slots.get(wid)
                if slot is None or slot.isHidden() or not self.on_bar(wid) or self.edit_mode:
                    continue
                slot.squeezed = True
                slot.hide()
                hidden.append(WIDGETS[wid]["name"])
            if hidden and hidden != getattr(self, "_last_squeeze", None):
                print(f"{datetime.now():%H:%M:%S} not enough room on the bar - tucked away: {', '.join(hidden)}")
            self._last_squeeze = hidden
        finally:
            self.setUpdatesEnabled(True)
            self._fitting = False

    # ---- flicker watchdog: if anything keeps redoing itself, say so in the log (once a minute)
    def note_activity(self, what):
        now = time.monotonic()
        log = self.__dict__.setdefault("_activity", {})
        times = [t for t in log.get(what, []) if now - t < 10] + [now]
        log[what] = times
        last = self.__dict__.setdefault("_activity_warned", {})
        if len(times) >= 8 and now - last.get(what, -1e9) > 60:
            last[what] = now
            print(f"{datetime.now():%H:%M:%S} flicker watchdog: '{what}' ran {len(times)}x in 10 s "
                  f"(bar {self.width()}x{self.height()}, needs {self.needed_width()}, "
                  f"native {getattr(self, 'native_rect', None)}, dpr {self.dpr()})")

    def check_geometry(self):
        rect = getattr(self, "native_rect", None)
        if not rect or not self.isVisible() or self.fs_hidden or self.ah_hidden:
            return
        r = wt.RECT()
        user32.GetWindowRect(int(self.winId()), ctypes.byref(r))
        got = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        if max(abs(a - b) for a, b in zip(got, rect)) > 2:
            self.note_activity("size fight")
            if not getattr(self, "_geom_warned", False):
                self._geom_warned = True
                print(f"{datetime.now():%H:%M:%S} bar window is {got}, expected {tuple(rect)}")
            now = time.monotonic()
            if now - getattr(self, "_geom_fixed", -1e9) > 10:       # gentle: at most once per 10 s
                self._geom_fixed = now
                self.apply_rect()

    def layout_changed(self):
        """Hook for the gallery to refresh its buttons."""
        if hasattr(self, "fit_timer"):
            self.fit_timer.start()
        g = getattr(self, "gallery", None)
        if g is not None and g.isVisible():
            g.sync()

    def open_gallery(self):
        if getattr(self, "gallery", None) is None:
            self.gallery = Gallery(self)
        self.gallery.open()

    def open_system_card(self, anchor):
        if self.system_card is None:
            self.system_card = SystemCard(self)
        self.system_card.open_from(anchor)

    def open_calendar(self, anchor):
        if self.calendar_card is None:
            self.calendar_card = CalendarCard(self)
        self.calendar_card.open_from(anchor)

    # ---- styling ----
    def restyle(self, w):
        """The look comes from one app-wide style sheet now (apply_stylesheet). Before, every card, toast and
        the bar each parsed their own copy, and adding/moving a widget re-styled the whole bar."""

    def check_updates(self, manual=False):
        if not GITHUB_REPO:
            if manual:
                self.toast.notify("Update check is off", "This copy doesn't know where its releases are published.")
            return
        if not manual and not self.cfg.get("check_updates", True):
            return
        BgCall(fetch_latest_release, lambda info: self.on_release(info, manual))

    def on_release(self, info, manual=False):
        if not info:
            if manual:
                self.toast.notify("Couldn't check for updates", "GitHub didn't answer. Try again later.")
            return
        if version_tuple(info["version"]) > version_tuple(APP_VERSION):
            self.update_info = info
            ready = self.update_ready and self.update_ready["version"] == info["version"]
            if ready:
                if manual:
                    self.tell_update_ready()
            elif self.cfg.get("auto_update", True) and can_self_update():
                self.fetch_update(info, install_after=False, quiet=not manual)   # tells you once it's ready
            elif manual or self._update_told != info["version"]:
                self._update_told = info["version"]
                one_click = can_self_update()
                self.toast.notify(f"⬆ OrbitBar {info['version']} is available",
                                  f"You have {APP_VERSION}. Click here to "
                                  + ("update now (OrbitBar restarts by itself)." if one_click
                                     else "open the download page."),
                                  on_click=self.update_now, linger=True)
        else:
            self.update_info = None
            if manual:
                self.toast.notify("OrbitBar is up to date", f"You have the latest version ({APP_VERSION}).")

    def update_now(self):
        """Clicked "update": install it if it's already downloaded, else download, check and install it.
        Running from source (or a folder we can't write to): open the release page instead."""
        info = self.update_info
        if not info:
            return
        if not can_self_update():
            os.startfile(info["url"])
            return
        if self.update_ready and self.update_ready["version"] == info["version"]:
            self.install_update(self.update_ready)
        else:
            self.fetch_update(info, install_after=True)

    def fetch_update(self, info, install_after, quiet=False):
        if self._updating:
            return
        self._updating = True
        if not quiet:
            self.toast.notify(f"⬇ Downloading OrbitBar {info['version']}…", "The bar keeps working meanwhile.")

        def done(res):
            self._updating = False
            if not res or res.get("error"):
                why = (res or {}).get("error", "unknown error")
                print(f"{datetime.now():%H:%M:%S} update {info['version']}: {why}")
                if not quiet:
                    self.toast.notify("Couldn't update OrbitBar", f"{why[:1].upper()}{why[1:]}. "
                                      "Click to get it from the download page.",
                                      on_click=lambda: os.startfile(info["url"]))
                return
            print(f"{datetime.now():%H:%M:%S} update {res['version']} downloaded and verified")
            self.update_ready = res
            if install_after:
                self.install_update(res)
            else:
                self.tell_update_ready()
        BgCall(lambda: download_update(info), done)

    def tell_update_ready(self):
        v = self.update_ready["version"]
        self._update_told = v
        self.toast.notify(f"⬆ OrbitBar {v} is ready", "Click to restart and update now, or it installs "
                          "itself the next time OrbitBar starts.",
                          on_click=lambda: self.install_update(self.update_ready), linger=True)

    def install_update(self, p):
        if not p:
            return
        try:
            run_updater(p)
        except Exception as ex:
            self.toast.notify("Couldn't start the update", str(ex)[:120])
            return
        print(f"{datetime.now():%H:%M:%S} restarting to install OrbitBar {p['version']}")
        QApplication.quit()                     # normal quit: the taskbar comes back for a second, then the new bar

    @property
    def search_card(self):
        if self._search_card is None:
            self._search_card = SearchCard(self)
        return self._search_card

    def apply_stylesheet(self):
        css = self.stylesheet()
        app = QApplication.instance()
        if app is not None and app.styleSheet() != css:
            app.setStyleSheet(css)

    def stylesheet(self):
        a = self.cfg["accent"]
        on_accent = "#0d0d14"
        return (f"""
            * {{ font-family: "Segoe UI Variable Text", "Segoe UI"; color: {TEXT}; }}
            QFrame#pill {{ background: {rgba(T["pill"])}; border-radius: 12px; }}
            QFrame#card {{ background: {rgba(T["card"])}; border: 1px solid {rgba(T["border"])};
                           border-radius: 14px; }}
            QPushButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 8px; }}
            QPushButton:hover {{ background: {rgba(T["hover"])}; }}
            QPushButton:disabled {{ color: {DIM}; }}
            QPushButton[class="glyph"] {{ font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; }}
            QPushButton[class="seek"] {{ padding: 8px 8px; border-radius: 18px; border: none;
                                         background: transparent; font-weight: 600; color: {DIM}; }}
            QPushButton[class="seek"]:hover {{ background: {rgba(T["hover"])}; color: {TEXT}; }}
            QPushButton[class="seek"]:disabled {{ color: {rgba(T["border"])}; }}
            QPushButton[class="vol"] {{ font-family: "Segoe Fluent Icons", "Segoe UI"; padding: 6px 10px; }}
            QPushButton[class="chip"] {{ background: {rgba(T["pill"])}; padding: 6px 10px; }}
            QPushButton[class="chip"]:hover {{ background: {rgba(T["hover"])}; }}
            QPushButton[class="ws"] {{ color: {DIM}; padding: 4px 9px; }}
            QPushButton[class="ws-active"] {{ background: {a}; color: {on_accent}; font-weight: 600; padding: 4px 9px; }}
            QPushButton[class="link"] {{ color: {a}; padding: 2px 4px; }}
            QLabel[class="time"] {{ font-weight: 600; }}
            QLabel[class="date"], QLabel[class="dim"] {{ color: {DIM}; }}
            QLabel[class="media"] {{ padding-left: 4px; }}
            QLabel[class="h1"] {{ font-weight: 300; }}
            QLabel[class="h2"], QLabel[class="toast-title"] {{ font-weight: 600; }}
            QLabel[class="section"] {{ font-weight: 600; color: {DIM}; padding-top: 6px; }}
            QLabel[class="cal-head"] {{ color: {DIM}; }}
            QLabel[class="cal-out"] {{ color: {DIM}; }}
            QLabel[class="cal-today"] {{ background: {a}; color: {on_accent}; font-weight: 600; border-radius: 14px; }}
            QLineEdit, QSpinBox {{ background: {rgba(T["pill"])}; border: 1px solid {rgba(T["border"])};
                                   border-radius: 8px; padding: 6px 8px; selection-background-color: {a}; }}
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ padding: 5px; border-radius: 6px; }}
            QListWidget::item:selected {{ background: {rgba(T["hover"])}; color: {TEXT}; }}
            QProgressBar {{ background: {rgba(T["pill"])}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {a}; border-radius: 3px; }}
            QSlider::groove:horizontal {{ height: 4px; background: {rgba(T["hover"])}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0;
                                          border-radius: 7px; border: 1px solid {rgba(T["border"])}; }}
            QMenu {{ background: {rgba(T["card"])}; border: 1px solid {rgba(T["border"])}; padding: 4px; }}
            QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {rgba(T["hover"])}; }}
            QMenu::separator {{ height: 1px; background: {rgba(T["border"])}; margin: 4px 8px; }}
            QToolTip {{ background: {rgba(T["card"])}; color: {TEXT}; border: 1px solid {rgba(T["border"])}; }}
        """ + self.size_rules())

    def size_rules(self):
        """Font sizes: one set for the bar (Bar widgets size), one for anything inside a card or
        notification (Cards & notifications size). Changing either re-styles live - no restart."""
        sized = [("*", 12), ('QPushButton[class="glyph"]', 13), ('QPushButton[class="vol"]', 14),
                 ('QLabel[class="time"]', 13), ('QLabel[class="date"]', 11), ('QLabel[class="dim"]', 11),
                 ('QLabel[class="h1"]', 30), ('QLabel[class="h2"]', 13), ('QLabel[class="section"]', 10),
                 ('QLabel[class="cal-head"]', 11), ('QLabel[class="cal"]', 13), ('QLabel[class="cal-out"]', 13),
                 ('QLabel[class="cal-today"]', 13), ('QLabel[class="toast-title"]', 14), ('QLabel[class="toast"]', 13)]
        out = []
        for ctx, f in (("", SIZES["w"]), ("QFrame#card ", SIZES["c"])):
            for sel, n in sized:
                out.append(f"{ctx}{sel} {{ font-size: {max(7, round(n * f))}px; }}")
        out.append(f'QFrame#card QLabel[class="cal-today"] {{ border-radius: {uc(30) // 2 - 1}px; }}')   # pill
        return "\n" + "\n".join(out) + "\n"

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.cfg.get("radius", 16)
        if T.get("blur"):
            rad = min(rad, 8)     # DWM rounds glass windows with ~8px corners
        p.setPen(QPen(QColor(*T["border"]), 1))
        p.setBrush(QColor(*T["bg"]))
        p.drawRoundedRect(r, rad, rad)

    # ---- placement ----
    def dpr(self):
        return QApplication.primaryScreen().devicePixelRatio()

    def bar_height(self):
        """Configured height, but never too short for the app icons."""
        icon = int(self.cfg.get("icon_size", 24))
        return max(int(self.cfg["height"]), icon + 20 + 2 * int(self.cfg.get("margin", 0)))

    def place(self):
        hwnd = int(self.winId())
        dpr = self.dpr()
        top = self.cfg["position"] == "top"
        h = int(self.bar_height() * dpr)
        m = int(self.cfg.get("margin", 0) * dpr)
        if self.cfg.get("autohide"):
            if self.appbar:
                self.appbar.remove()
                self.appbar = None
            mon = primary_monitor_rect()
            l, r = mon.left, mon.right
            t = mon.top if top else mon.bottom - h
            b = t + h
        else:
            if self.appbar is None:
                self.appbar = AppBar(hwnd)
            rc = self.appbar.place(top, h)
            l, t, r, b = rc.left, rc.top, rc.right, rc.bottom
        self.native_rect = (l + m, t + m, r - l - 2 * m, b - t - 2 * m)     # physical pixels
        self.apply_rect()
        self.apply_effects()

    def apply_effects(self):
        if not self.effects_done:
            self.effects_done = True
            hwnd = int(self.winId())
            if T.get("blur"):
                set_acrylic(hwnd, (T["bg"][0], T["bg"][1], T["bg"][2], 90))
                set_corner_round(hwnd, self.cfg.get("radius", 16) > 0)
                self._blur_on = True
            elif getattr(self, "_blur_on", False):        # switched away from Glass: turn the blur off again
                clear_acrylic(hwnd)
                set_corner_default(hwnd)
                self._blur_on = False

    def pre_show(self):
        """Reload hand-over: appear right away, exactly over the old bar (which still holds the screen
        space), so the swap is invisible. Screen space is registered once the old copy has left."""
        dpr = self.dpr()
        top = self.cfg["position"] == "top"
        h = int(self.bar_height() * dpr)
        m = int(self.cfg.get("margin", 0) * dpr)
        mon = primary_monitor_rect()
        t = mon.top if top else mon.bottom - h
        self.native_rect = (mon.left + m, t + m, mon.right - mon.left - 2 * m, h - 2 * m)
        self.apply_rect()
        self.apply_effects()
        self.setWindowOpacity(0.0)        # invisible until the old bar starts fading: a cross-fade, never two bars
        self.show()
        self.apply_rect()
        self.repaint()

    def fade_to(self, value, ms=170, done=None):
        self.fade.stop()
        prev = getattr(self, "_fade_done", None)
        if prev is not None:              # disconnect only what we connected (else PySide prints a warning)
            try:
                self.fade.finished.disconnect(prev)
            except (RuntimeError, TypeError):
                pass
        self._fade_done = done
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(value)
        self.fade.setDuration(ms)
        if done:
            self.fade.finished.connect(done)
        self.fade.start()

    def popup_pos(self, size, anchor, center=False):
        """Where to put a popup so it opens away from the screen edge the bar sits on."""
        x = anchor.x() - (size.width() // 2 if center else 0)
        geo = self.screen().geometry() if self.screen() else QApplication.primaryScreen().geometry()
        x = max(geo.left() + 4, min(x, geo.right() - size.width() - 4))
        if self.cfg["position"] == "top":
            y = self.geometry().bottom() + 6
        else:
            y = self.geometry().top() - size.height() - 6
        return QPoint(x, y)

    # ---- auto-hide ----
    def something_open(self):
        return bool(QApplication.activePopupWidget()
                    or (self.previews and self.previews.isVisible())
                    or (self.media and self.media.card.isVisible())
                    or (self._search_card is not None and self._search_card.isVisible()))

    def reveal(self):
        if not self.ah_hidden:
            return
        self.ah_hidden = False
        self.setWindowOpacity(0.0)
        self.show()
        self.place()
        self.fade.stop()
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def conceal(self):
        if self.ah_hidden:
            return
        self.ah_hidden = True
        if self.previews:
            self.previews.close_now()
        self.hide()

    def tick_autohide(self):
        if self.fs_hidden:
            return
        x, y = cursor_pos()
        mon = primary_monitor_rect()
        on_x = mon.left <= x < mon.right
        if self.ah_hidden:
            at_edge = (y <= mon.top + 1) if self.cfg["position"] == "top" else (y >= mon.bottom - 2)
            if on_x and at_edge:
                self.reveal()
            return
        r = wt.RECT()
        user32.GetWindowRect(int(self.winId()), ctypes.byref(r))
        pad = 12
        inside = r.left - pad <= x <= r.right + pad and r.top - pad <= y <= r.bottom + pad
        if inside or self.something_open():
            self.ah_away = 0
        else:
            self.ah_away += 1
            if self.ah_away >= 5:          # ~0.6 s away
                self.ah_away = 0
                self.conceal()

    # ---- timers ----
    _fs_off_since = None

    def check_fullscreen(self):
        """Hide for fullscreen video/games, come back the instant they end. Cheap: a few Win32 calls."""
        if not self.cfg.get("hide_on_fullscreen", True):
            return
        fs = foreground_is_fullscreen()
        fg = user32.GetForegroundWindow()
        fs_win = getattr(self, "_fs_hwnd", None)
        if not fs and self.fs_hidden and foreground_looks_fullscreen():
            fs = True                         # still the same borderless video, just squeezed for a moment
        if not fs and self.fs_hidden and fs_win and fg != fs_win and fg \
                and window_pid(fg) == window_pid(fs_win) and window_still_fullscreen(fs_win):
            # the player's own controls took focus (VLC's seek bar, a menu, a subtitle dialog): the video is
            # still fullscreen behind them, so stay out of the way
            fs = True
        ended_by_video = (not fs and self.fs_hidden and fs_win and fg == fs_win)   # Esc / double-click
        if fs:
            self._fs_off_since = None
        elif self.fs_hidden and not ended_by_video:
            # Come back only once fullscreen has really ended (2+ checks in a row, ~0.3 s). A single blip -
            # a pop-up, the foreground briefly being nobody - used to flip the bar above/below the video.
            now = time.monotonic()
            if self._fs_off_since is None:
                self._fs_off_since = now
            if now - self._fs_off_since < 0.2:
                QTimer.singleShot(220, self.check_fullscreen)
                return
        if fs and not self.fs_hidden:
            self.fs_hidden = True
            self._fs_hwnd = fg
            self.fs_timer.setInterval(60)     # watch closely while hidden: back within a blink when it ends
            try:
                h = user32.GetForegroundWindow()
                key = (proc_name(window_pid(h)), class_name(h))
                if key != getattr(self, "_fs_logged", None):
                    self._fs_logged = key
                    print(f"{datetime.now():%H:%M:%S} hidden for fullscreen: {key[0]} ({key[1]}, "
                          f"exstyle {user32.GetWindowLongW(h, -20) & 0xFFFFFFFF:#x})")
            except Exception:
                pass
            if self.previews:
                self.previews.close_now()
            if self.media:
                self.media.card.close_now()
            self.hide()
        elif not fs and self.fs_hidden:
            self.fs_hidden = False
            self._fs_hwnd = None
            self._fs_off_since = None
            self.fs_timer.setInterval(250)
            if not self.ah_hidden:
                self.reappear()

    def apply_rect(self):
        """Put the window exactly where Windows reserved space for it (physical pixels).
        Qt's own idea of the size can be off with display scaling (125/150/200 %) after a hide,
        so this is re-applied every time the bar comes back."""
        rect = getattr(self, "native_rect", None)
        if not rect:
            return
        x, y, w, h = rect
        dpr = self.dpr() or 1
        self.setGeometry(round(x / dpr), round(y / dpr), round(w / dpr), round(h / dpr))
        user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)

    def reappear(self):
        """Show again without re-registering screen space (that re-lays-out every window: slow)."""
        self.apply_rect()
        self.show()
        self.apply_rect()
        self.refresh_windows()

    def nativeEvent(self, event_type, message):
        # Windows tells registered taskbars directly when a fullscreen app opens/closes (ABN_FULLSCREENAPP)
        try:
            if event_type == b"windows_generic_MSG":
                msg = wt.MSG.from_address(int(message))
                if getattr(self, "shell_msg", 0) and msg.message == self.shell_msg:
                    self.on_shell_event(int(msg.wParam), int(msg.lParam or 0))
                elif ((msg.message == 0x0005 and msg.wParam == 1)                 # WM_SIZE: minimized
                      or (msg.message == 0x0018 and not msg.wParam)):              # WM_SHOWWINDOW: hidden
                    self.keep_shown_soon()
                elif self.appbar and msg.message == self.appbar.abd.uCallbackMessage and msg.wParam == 2:
                    QTimer.singleShot(0, self.check_fullscreen)
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def reserved_ok(self):
        x, y, w, h = self.native_rect
        work = monitor_info(user32.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)).rcWork
        top = self.cfg["position"] == "top"
        return not ((work.top < y + h) if top else (work.bottom > y))

    def ensure_reserved(self):
        """Make sure Windows still keeps the bar's strip free, so maximized windows stop at its edge.
        First ask politely (AppBar); if Windows ignores that, set the work area directly. If Windows keeps
        undoing even that, back off instead of fighting every second (that fight = the whole screen flickering)."""
        if self.cfg.get("autohide") or self.fs_hidden or not getattr(self, "native_rect", None):
            return
        if self.reserved_ok():
            return
        if foreground_looks_fullscreen():
            # Never touch the screen space while something is fullscreen: changing it makes Windows shrink
            # a browser that was maximized before its video went fullscreen, the browser grows back, and the
            # video bounces between "whole screen" and "stops at the bar". Fix it once fullscreen ends.
            return
        now = time.monotonic()
        if getattr(self, "work_area_forced", False):
            # AppBar is already known not to work here: don't re-register it (that reflows every window)
            if now < getattr(self, "_wa_pause_until", 0) or now - getattr(self, "_wa_last", -1e9) < 3:
                return
            hits = [t for t in getattr(self, "_wa_hits", []) if now - t < 60] + [now]
            self._wa_hits, self._wa_last = hits, now
            if len(hits) > 4:
                self._wa_pause_until = now + 120
                self._wa_hits = []
                print(f"{datetime.now():%H:%M:%S} Windows keeps resetting the screen space - pausing for 2 min "
                      f"(tray widget {'on' if self.tray else 'off'})")
                return
            self.force_work_area()
            return
        self.note_activity("re-reserve space")
        self.place()
        if self.reserved_ok():
            return
        self.force_work_area()
        self.work_area_forced = True
        self._wa_last = now
        if not getattr(self, "_warned_workarea", False):
            self._warned_workarea = True
            print(f"{datetime.now():%H:%M:%S} AppBar space not honoured by Windows - set work area directly "
                  f"(tray widget {'on' if self.tray else 'off'})")

    def force_work_area(self):
        mon = primary_monitor_rect()
        hpx = int(self.bar_height() * self.dpr())
        work = wt.RECT(mon.left, mon.top, mon.right, mon.bottom)
        if self.cfg["position"] == "top":
            work.top = mon.top + hpx
        else:
            work.bottom = mon.bottom - hpx
        user32.SystemParametersInfoW(0x002F, 0, ctypes.byref(work), 0x2)   # SPI_SETWORKAREA, SPIF_SENDCHANGE

    def keep_shown(self):
        """Win+D / Show desktop: depending on the Windows build it minimizes or hides every window, or it
        raises the desktop above every window that isn't "always on top". The real taskbar is spared because
        it belongs to Explorer; the bar is not. And over time other programs (Explorer re-asserting its own
        taskbar, games, overlays) can strip the bar's always-on-top flag, so the next Win+D buries it.
        Whenever the bar should be on screen, check all of that and put it straight back (on top, no focus)."""
        if (self.fs_hidden or self.ah_hidden or getattr(self, "_handing_over", False)
                or not getattr(self, "_live", False)):     # hidden on purpose (or not started yet): leave it
            return
        h = int(self.winId())
        why = None
        if user32.IsIconic(h) or not user32.IsWindowVisible(h):
            why = "minimized/hidden"
        elif is_cloaked(h):
            why = "cloaked"
        elif not user32.GetWindowLongW(h, -20) & 0x8:              # WS_EX_TOPMOST lost
            why = "lost always-on-top"
        else:
            # is the desktop window above the bar in the z-order? (walking up from the bar: few windows,
            # since the bar is always-on-top. Hit-testing a point instead fails on see-through bar pixels.)
            above, steps = user32.GetWindow(h, 3), 0               # GW_HWNDPREV
            while above and steps < 300:
                if user32.IsWindowVisible(above) and class_name(above) in ("Progman", "WorkerW"):
                    why = "covered by the desktop"
                    break
                above, steps = user32.GetWindow(above, 3), steps + 1
        if not why:
            return
        if self.isMinimized():
            self.setWindowState(Qt.WindowNoState)
        user32.ShowWindow(h, 4)                                    # SW_SHOWNOACTIVATE
        self.apply_rect()                                          # back in its strip, HWND_TOPMOST, no focus
        n = getattr(self, "_kept_count", 0) + 1
        self._kept_count = n
        if n <= 3 or n % 50 == 0:
            print(f"{datetime.now():%H:%M:%S} bar was {why} (Show desktop?) - brought it back"
                  + (f" [{n} times so far]" if n > 3 else ""))

    def keep_shown_soon(self):
        """Show desktop animates for a moment: check now and again once it has settled."""
        for ms in (0, 150, 600):
            QTimer.singleShot(ms, self.keep_shown)

    def tick_fast(self):
        self.keep_shown()
        self.taskbar.enforce()
        self.ensure_reserved()
        self.check_geometry()
        if self.tray:
            self.tray.host.keep_on_top()
        if self.fs_hidden or self.ah_hidden:
            return
        self.fast_ticks += 1
        # with WinEvent hooks the bar refreshes the moment something changes; the timer is just a safety net
        if not (self.win_events and self.win_events.active) or self.fast_ticks % 5 == 0:
            self.refresh_windows()

    def start_shell_hook(self):
        """The messages Windows sends to taskbars: an app flashing for attention, windows created,
        destroyed and activated. Same feed the real taskbar uses."""
        try:
            self.shell_msg = user32.RegisterWindowMessageW("SHELLHOOK")
            if not user32.RegisterShellHookWindow(int(self.winId())):
                self.shell_msg = 0
        except Exception as ex:
            print("shell hook unavailable:", ex)
            self.shell_msg = 0

    def on_shell_event(self, code, hwnd):
        if code == 0x8006:                                    # HSHELL_FLASH
            self.apps.flash(hwnd)
        elif code in (4, 0x8004):                             # activated
            self.apps.set_foreground(hwnd)
        elif code in (1, 2):                                  # window created / destroyed
            self.schedule_refresh(30)
        elif code == 6 and hwnd in self.apps.known:           # redrawn (title or icon changed)
            if not self.titles_timer.isActive():
                self.titles_timer.start()

    def start_window_events(self):
        self.start_shell_hook()
        try:
            self.win_events = WindowEvents(self.on_window_event)
            if self.win_events.active:
                self.fast.setInterval(1000)       # only fullscreen checks + safety net now
        except Exception as ex:
            print("window events unavailable, polling instead:", ex)
            self.win_events = None

    def schedule_refresh(self, ms):
        """Rescan windows within `ms`. A pending sooner rescan is kept, and a stream of events can't keep
        pushing it back (before, a chatty window - a loading tab, a terminal - could delay updates for seconds)."""
        t = self.refresh_soon
        if t.isActive() and t.remainingTime() <= ms:
            return
        t.start(ms)

    def on_window_event(self, event=None, hwnd=None):
        if event is None:
            self.schedule_refresh(30)
            return
        if event == 0x0003:                                   # focus moved
            self.check_fullscreen()
            if class_name(hwnd) in ("Progman", "WorkerW"):     # Win+D / Show desktop / clicked the desktop
                self.keep_shown_soon()
            if self.fs_hidden or self.ah_hidden:
                return
            self.apps.set_foreground(hwnd)                    # highlight it this instant
            if hwnd not in self.apps.known and could_be_app_window(hwnd):
                self.schedule_refresh(30)
            return
        if self.fs_hidden or self.ah_hidden:
            return
        known = hwnd in self.apps.known
        if event == 0x800C:                                   # a title changed
            if known:
                if not self.titles_timer.isActive():          # tooltips only, a few times a second at most
                    self.titles_timer.start()
            elif could_be_app_window(hwnd):                   # a window that just got its first title
                self.schedule_refresh(150)
            return
        # opened / closed / shown / hidden / minimized / restored / cloaked (desktop switch)
        if known or could_be_app_window(hwnd):
            self.schedule_refresh(30)

    def refresh_windows(self):
        if self.fs_hidden or self.ah_hidden:
            return
        self.apps.refresh()
        if self.workspaces:
            self.workspaces.refresh()

    def tick_slow(self):
        self.slow_ticks += 1
        if self.timer_w:
            self.timer_w.refresh()        # keep counting even while hidden
        if self.fs_hidden or self.ah_hidden:
            return
        self.sys_source.refresh()
        if self.worldclock and self.slow_ticks % 20 == 0:
            self.worldclock.refresh()
        if self.clock:
            self.clock.refresh()
        if self.volume_btn:
            self.volume_btn.refresh()
        if self.recycle and self.slow_ticks % 5 == 0:
            self.recycle.refresh()
        if self.study and self.slow_ticks % 30 == 0:
            if self.study_chips:
                self.study_chips.refresh()
            self.study.reminders(self.study_chips or self.timer_w or self)
        if self.study and self.slow_ticks % 60 == 0:
            self.study.state.save()

    # ---- actions ----
    def set_pinned(self, path, pin):
        pins = self.cfg.setdefault("pinned", [])
        pins[:] = [p for p in pins if norm(p) != norm(path)]
        if pin:
            pins.append(path)
        save_config(self.cfg)
        self.apps.order = []      # force rebuild
        self.apps.refresh()

    def set_pins(self, paths):
        self.cfg["pinned"] = paths
        save_config(self.cfg)
        self.apps.order = []
        self.apps.refresh()

    def open_settings(self):
        if self.settings_win is None or not self.settings_win.isVisible():
            self.settings_win = SettingsWindow(self)
        self.settings_win.open()

    def set_cfg(self, **changes):
        """Save settings and apply them in place - no restart, so music, timers, the tray and your open
        cards keep running. Only "Size of everything" restarts the bar (Qt fixes its scale at start-up)."""
        old = json.loads(json.dumps(self.cfg))
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(self.cfg.get(k), dict):
                self.cfg[k].update(v)
            else:
                self.cfg[k] = v
        save_config(self.cfg)
        changed = {k for k in changes if old.get(k) != self.cfg.get(k)}
        if not changed:
            return
        if "scale" in changed:
            self.reload()
            return
        t0 = time.perf_counter()
        try:
            self.apply_changes(changed, old)
        except Exception as ex:          # never leave a half-applied bar: fall back to a clean restart
            import traceback
            traceback.print_exc()
            print("live settings change failed, reloading instead:", ex)
            self.reload()
            return
        print(f"{datetime.now():%H:%M:%S} settings applied live in {1000 * (time.perf_counter() - t0):.0f} ms: "
              + ", ".join(sorted(changed)))

    def apply_changes(self, changed, old):
        cfg = self.cfg
        if changed & {"theme", "background", "accent"}:
            apply_theme(cfg)
            self.apply_stylesheet()
            self.effects_done = False
            self.apply_effects()
            g = getattr(self, "gallery", None)            # the gallery has colours of its own: rebuild it
            if g is not None:
                was = g.isVisible()
                if was:
                    g.close()
                g.deleteLater()
                self.gallery = None
                if was:
                    self.open_gallery()
            if self.media:
                self.media._sig = 0
                self.media.on_update(self.media.data)
            if self.timer_w and self.timer_w.mode:
                self.timer_w.ticks -= 1                   # (a re-colour, not an extra second)
                self.timer_w.refresh()
        if changed & {"widget_size", "card_size"}:
            apply_sizes(cfg)
            self.apply_stylesheet()
            for w in QApplication.allWidgets():
                if hasattr(w, "rescale"):
                    w.rescale()
            g = getattr(self, "gallery", None)
            if g is not None and not g.isVisible():
                g.deleteLater()
                self.gallery = None
        if "hide_windows_taskbar" in changed:
            if cfg.get("hide_windows_taskbar", True):
                self.taskbar.hide()
            else:
                self.taskbar.show()
        if "previews" in changed:
            if cfg.get("previews", True) and self.previews is None:
                self.previews = Previews(self)
            elif not cfg.get("previews", True) and self.previews is not None:
                self.previews.close_now()
                self.previews.deleteLater()
                self.previews = None
        if "icon_size" in changed:
            for b in self.apps.buttons.values():
                b.set_icon_size(int(cfg.get("icon_size", 24)))
        if "autohide" in changed:
            self.set_autohide(bool(cfg.get("autohide")))
        elif changed & {"position", "height", "margin", "radius", "icon_size"}:
            if not (self.ah_hidden or self.fs_hidden):
                self.place()
            if getattr(self, "work_area_forced", False):
                self.force_work_area()
        if changed & {"position", "radius", "margin"}:
            self.effects_done = False
            self.apply_effects()
            if self.tray:
                self.tray.set_chevron()
            self.toast.restack()
        if "weather" in changed and self.weather:
            self.weather.refetch()
        if changed & {"clock_24h", "world_clocks"}:
            if self.clock:
                self.clock.refresh()
            if self.worldclock:
                self.worldclock.refresh()
        if "battery" in changed:
            cfg.setdefault("widget_variants", {})["battery"] = cfg.get("battery", {}).get("style", "icon")
            if self.battery:
                self.battery.update_status()
        if "study" in changed:
            self.apply_study(old.get("study") or {})
        if changed & {"study", "timer_presets"} and self.timer_w:
            self.timer_w.rebuild_card()
        if changed & {"widget_size", "card_size"} and not (self.ah_hidden or self.fs_hidden):
            self.place()
        if changed & {"theme", "background", "accent", "position", "height", "margin", "radius", "icon_size",
                      "widget_size", "card_size"}:
            for w in QApplication.allWidgets():          # custom-drawn widgets pick up the new look
                w.update()
        self.layout_changed()

    def apply_study(self, old_st):
        st = self.cfg.setdefault("study", dict(DEFAULT_STUDY))
        links = ("lms_links", "class_links")
        if self.study:
            if any((st.get(k) or "") != (old_st.get(k) or "") for k in links):
                if not any((st.get(k) or "").strip() for k in links):
                    self.study.synced, self.study.errors = [], []
                self.study.sync()
            self.study.rebuild()
        if self.tracker:
            t = self.tracker.timer
            if st.get("track_time", True) and not t.isActive():
                t.start()
            elif not st.get("track_time", True) and t.isActive():
                t.stop()

    def set_autohide(self, on):
        if on:
            if self.ah_timer is None:
                self.ah_timer = QTimer(self, interval=120, timeout=self.tick_autohide)
            self.ah_away = 0
            self.ah_timer.start()
            if getattr(self, "work_area_forced", False):          # give the reserved strip back
                mon = primary_monitor_rect()
                user32.SystemParametersInfoW(0x002F, 0, ctypes.byref(wt.RECT(mon.left, mon.top, mon.right, mon.bottom)),
                                             0x2)
                self.work_area_forced = False
            if not self.fs_hidden and not self.ah_hidden:
                self.place()                                       # releases the screen space
        else:
            if self.ah_timer is not None:
                self.ah_timer.stop()
            if self.ah_hidden:
                self.ah_hidden = False
                self.fade.stop()
                self.setWindowOpacity(1.0)
                if not self.fs_hidden:
                    self.show()
            if not self.fs_hidden:
                self.place()                                       # reserves the screen space again

    def contextMenuEvent(self, e):
        m = QMenu(self)
        if self.update_info:
            info = self.update_info
            ready = self.update_ready and self.update_ready["version"] == info["version"]
            m.addAction(f"⬆  Update to OrbitBar {info['version']} now" if can_self_update()
                        else f"⬆  Get OrbitBar {info['version']}…").triggered.connect(
                lambda: self.install_update(self.update_ready) if ready else self.update_now())
        m.addAction("⚙  Settings…").triggered.connect(self.open_settings)
        m.addSeparator()
        themes = m.addMenu("Style")
        for name in ("glass", "midnight", "black", "white"):
            a = themes.addAction(name.capitalize())
            a.setCheckable(True)
            a.setChecked(self.cfg.get("theme", "midnight") == name)
            a.triggered.connect(lambda _=False, n=name: self.set_cfg(theme=n))
        shape = m.addMenu("Shape")
        pill = self.cfg.get("margin", 0) > 0
        a = shape.addAction("Floating pill")
        a.setCheckable(True)
        a.setChecked(pill)
        a.triggered.connect(lambda: self.set_cfg(margin=5, radius=16, height=56))
        a = shape.addAction("Full width")
        a.setCheckable(True)
        a.setChecked(not pill)
        a.triggered.connect(lambda: self.set_cfg(margin=0, radius=0, height=48))
        shape.addSeparator()
        for pos in ("bottom", "top"):
            a = shape.addAction(f"Dock at {pos}")
            a.setCheckable(True)
            a.setChecked(self.cfg["position"] == pos)
            a.triggered.connect(lambda _=False, p=pos: self.set_cfg(position=p))
        m.addAction("🧩  Customize widgets…").triggered.connect(self.open_gallery)
        size = m.addMenu("Size")
        cur = float(self.cfg.get("scale", 1.0))
        for label, val in (("Compact", 0.9), ("Normal", 1.0), ("Large", 1.15), ("Extra large", 1.3), ("Huge", 1.5)):
            a = size.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(cur - val) < 0.01)
            a.triggered.connect(lambda _=False, v=val: self.set_cfg(scale=v))
        size.addSeparator()
        for key, title in (("widget_size", "Bar widgets"), ("card_size", "Cards && notifications")):
            sub = size.addMenu(title)
            for label, val in SIZE_CHOICES:
                a = sub.addAction(label)
                a.setCheckable(True)
                a.setChecked(abs(float(self.cfg.get(key, 1.15)) - val) < 0.01)
                a.triggered.connect(lambda _=False, k=key, v=val: self.set_cfg(**{k: v}))
        icons = size.addMenu("App icons")
        for px in (20, 24, 28, 32, 40):
            a = icons.addAction(f"{px} px")
            a.setCheckable(True)
            a.setChecked(int(self.cfg.get("icon_size", 24)) == px)
            a.triggered.connect(lambda _=False, v=px: self.set_cfg(icon_size=v))
        for key, label in (("autohide", "Auto-hide"), ("previews", "Window previews"),
                           ("hide_on_fullscreen", "Step aside for fullscreen")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(bool(self.cfg.get(key, key != "autohide")))
            a.triggered.connect(lambda on, k=key: self.set_cfg(**{k: on}))
        m.addSeparator()
        sw = QAction("Start with Windows", m, checkable=True, checked=starts_with_windows())
        sw.toggled.connect(lambda on: set_start_with_windows(on))
        m.addAction(sw)
        t = QAction("Hide Windows taskbar", m, checkable=True, checked=self.taskbar.hidden)
        t.toggled.connect(lambda on: self.taskbar.hide() if on else self.taskbar.show())
        m.addAction(t)
        m.addAction("Task Manager").triggered.connect(lambda: os.startfile("taskmgr.exe"))
        m.addAction("Edit config…").triggered.connect(lambda: os.startfile(CONFIG_PATH))
        m.addAction("Open log").triggered.connect(lambda: os.startfile(LOG_PATH) if os.path.exists(LOG_PATH) else None)
        m.addAction("Reload OrbitBar").triggered.connect(self.reload)
        if GITHUB_REPO:
            m.addAction(f"Check for updates (you have {APP_VERSION})").triggered.connect(
                lambda: self.check_updates(manual=True))
        m.addSeparator()
        m.addAction("Quit OrbitBar").triggered.connect(QApplication.quit)
        m.exec(self.popup_pos(m.sizeHint(), e.globalPos()))

    def reload(self):
        """Seamless restart: start the new copy in the background, keep this one on screen until the new
        one is ready, then swap. The Windows taskbar is never brought back in between."""
        import subprocess
        global _mutex
        if getattr(self, "_handing_over", False):
            return
        self._handing_over = True
        if self.study:
            self.study.state.save(force=True)
        if self.settings_win:
            self.settings_win.close()
        self.close_popups()
        if _mutex:                       # let the new copy take the single-instance lock
            kernel32.CloseHandle(_mutex)
            _mutex = None
        args = [a for a in sys.argv[1:] if a not in ("--detached",) and not a.startswith("--takeover")]
        args += [f"--takeover={os.getpid()}"]
        if "--console" in sys.argv:
            exe_args = [sys.executable] + ([] if getattr(sys, "frozen", False) else [SCRIPT_PATH])
            subprocess.Popen(exe_args + args)
        else:
            launch_detached(args + ["--detached"])
        self._handover_started = time.monotonic()
        self._handover_event = None
        self._handover_timer = QTimer(self, interval=15, timeout=self._handover_poll)
        self._handover_timer.start()

    def close_popups(self):
        """Close every card, preview and menu (before a hand-over, so nothing is left floating)."""
        pop = QApplication.activePopupWidget()
        while pop is not None:
            pop.hide()
            nxt = QApplication.activePopupWidget()
            pop = None if nxt is pop else nxt
        if self.previews:
            self.previews.close_now()
        if self.media:
            self.media.card.close_now()
        for w in QApplication.topLevelWidgets():
            if w is not self and w.isVisible() and isinstance(w, (Card, Toast, SearchCard, FocusPrompt)):
                w.hide()

    def _handover_poll(self):
        if self._handover_event is None:
            self._handover_event = kernel32.OpenEventW(0x00100000, False, handover_event_name(os.getpid()))
        if signaled(self._handover_event):
            # the new bar sits on top, see-through; both fade at once, so the change looks like one smooth step
            self._handover_timer.stop()
            self.close_popups()
            self.fade_to(0.0, 170, handover_exit)
            QTimer.singleShot(600, handover_exit)            # in case the animation never finishes
        elif time.monotonic() - self._handover_started > 25:       # new copy never came up: keep running
            self._handover_timer.stop()
            self._handing_over = False
            print("reload failed: the new copy didn't start in time; staying on this one")
            take_instance_lock()

    def cleanup(self):
        if getattr(self, "work_area_forced", False):         # give the whole screen back
            mon = primary_monitor_rect()
            user32.SystemParametersInfoW(0x002F, 0, ctypes.byref(wt.RECT(mon.left, mon.top, mon.right, mon.bottom)), 0x2)
        if getattr(self, "study", None):
            self.study.state.save(force=True)
        if self.win_events:
            self.win_events.stop()
        if getattr(self, "shell_msg", 0):
            user32.DeregisterShellHookWindow(int(self.winId()))
        if self.privacy:
            self.privacy.stop()
        if self.netguard:
            self.netguard.stop()
        if self.previews:
            self.previews.close_now()
        if self.appbar:
            self.appbar.remove()
            self.appbar = None
        if self.media:
            self.media.card.close_now()
            self.media.worker.requestInterruption()
        if self.tray:
            self.tray.host.stop()
            self.tray = None
        if self.system_card:
            self.system_card.stop()
        self.sys_source.stop()



# --------------------------------------------------------------------------------------
# Running in the background, logging, start with Windows
# --------------------------------------------------------------------------------------
LOG_PATH = os.path.join(CONFIG_DIR, "orbitbar.log")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\OrbitBar.lnk")
SCRIPT_PATH = os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__)


def background_python():
    """pythonw.exe next to the current interpreter (no console window), if it exists."""
    if getattr(sys, "frozen", False):
        return sys.executable
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def launch_detached(extra_args=()):
    """Start OrbitBar as an independent background process (survives closing the terminal)."""
    import subprocess
    exe = background_python()
    args = [exe] + ([] if getattr(sys, "frozen", False) else [SCRIPT_PATH]) + list(extra_args)
    flags = 0x00000008 | 0x00000200 | 0x08000000      # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen(args, creationflags=flags, close_fds=True, cwd=os.path.dirname(SCRIPT_PATH))


_sig(kernel32.CreateEventW, wt.HANDLE, ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR)
_sig(kernel32.OpenEventW, wt.HANDLE, wt.DWORD, wt.BOOL, wt.LPCWSTR)
_sig(kernel32.SetEvent, wt.BOOL, wt.HANDLE)
_sig(kernel32.WaitForSingleObject, wt.DWORD, wt.HANDLE, wt.DWORD)


def handover_event_name(pid):
    return f"Local\\OrbitBar.Ready.{pid}"


def signaled(handle):
    return bool(handle) and kernel32.WaitForSingleObject(handle, 0) == 0      # WAIT_OBJECT_0


def already_running():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = wt.HANDLE
    k32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
    h = k32.OpenMutexW(0x00100000, False, "Local\\OrbitBar.SingleInstance")      # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def setup_logging():
    """With pythonw there is no console: send prints and crashes to %APPDATA%\\OrbitBar\\orbitbar.log."""
    if sys.stdout is not None and sys.stderr is not None and "--detached" not in sys.argv:
        return
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        mode = "w" if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024 else "a"
        f = open(LOG_PATH, mode, encoding="utf-8", buffering=1)
        f.write(f"\n--- {APP_NAME} {APP_VERSION} started {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
        sys.stdout = sys.stderr = f
    except OSError:
        pass

    def hook(t, v, tb):
        import traceback
        traceback.print_exception(t, v, tb)
    sys.excepthook = hook


def starts_with_windows():
    return os.path.exists(STARTUP_LNK)


def set_start_with_windows(on):
    if not on:
        try:
            os.remove(STARTUP_LNK)
        except OSError:
            pass
        return True
    target = background_python()
    args = "" if getattr(sys, "frozen", False) else f'"{SCRIPT_PATH}"'
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');"
          f"$s.TargetPath='{target}';$s.Arguments='{args}';"
          f"$s.WorkingDirectory='{os.path.dirname(SCRIPT_PATH)}';$s.Description='OrbitBar taskbar';$s.Save()")
    try:
        run_powershell(ps, timeout=20)
    except Exception as ex:
        print("couldn't create startup shortcut:", ex)
    return starts_with_windows()

# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
_bar = None
_taskbar = None
_mutex = None


def take_instance_lock():
    """Grab the single-instance lock. Returns False if another copy holds it."""
    global _mutex
    if _mutex:
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wt.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    h = k32.CreateMutexW(None, False, "Local\\OrbitBar.SingleInstance")
    if ctypes.get_last_error() == 183:                     # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(h)
        return False
    _mutex = h
    return True


def handover_exit():
    """Old copy leaving after a hand-over: tidy up but leave the Windows taskbar hidden (and keep the
    saved original taskbar state for the new copy)."""
    global _bar
    if _bar is not None:
        b, _bar = _bar, None
        b.hide()
        b.cleanup()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def cleanup():
    global _bar
    if _bar is not None:
        _bar.cleanup()
        _bar = None
    if _taskbar is not None and _taskbar.hidden:
        _taskbar.show()


def main():
    global _bar, _taskbar
    if "--install-update" in sys.argv:
        install_update_here()
        return
    if "--version" in sys.argv:
        print(f"{APP_NAME} {APP_VERSION} by {APP_PUBLISHER}  ({SCRIPT_PATH})")
        return
    if "--check-security" in sys.argv:
        t0 = time.perf_counter()
        try:
            com_init()
            raw = security_raw_direct()
        except Exception as ex:
            print("direct read failed, trying PowerShell:", ex)
            out = run_powershell(SECURITY_PS)
            raw = json.loads(out) if out else None
            if raw:
                raw["method"] = "powershell"
        v = evaluate_security(raw)
        print(f"OrbitBar {APP_VERSION} security check via {raw.get('method') if raw else '?'} "
              f"in {1000 * (time.perf_counter() - t0):.0f} ms\n=> {v['level'].upper()}: {v['summary']}")
        for name, value, good in v["items"]:
            print(f"   {'✓' if good else '✗'} {name:24} {value}")
        return
    if "--check-calendar" in sys.argv:
        calendar_report()
        return
    if "--check-media" in sys.argv:
        media_report()
        return
    if "--check-tray" in sys.argv:
        check_tray()
        return
    if "--check-privacy" in sys.argv:
        privacy_report()
        return
    if "--restore" in sys.argv:
        NativeTaskbar().show()
        print("Windows taskbar restored.")
        return

    # Started from a terminal with python.exe? Hand over to a background copy and free the terminal,
    # so closing the terminal doesn't close the bar. Use --console to keep it attached (for debugging).
    in_console = sys.stdout is not None and not getattr(sys, "frozen", False) \
        and os.path.basename(sys.executable).lower() == "python.exe"
    if in_console and "--console" not in sys.argv and "--detached" not in sys.argv:
        if already_running():
            print(f"{APP_NAME} is already running. Right-click the bar > Reload or Quit OrbitBar.")
            return
        launch_detached([a for a in sys.argv[1:]] + ["--detached"])
        print(f"{APP_NAME} {APP_VERSION} is running in the background - you can close this window.")
        print(f"Quit it from the bar: right-click > Quit OrbitBar.  Log: {LOG_PATH}")
        return
    setup_logging()
    # Background helpers (GPU, media, privacy, weather) are Python threads sharing one lock with the UI.
    # Hand it back to the UI every 2 ms instead of every 5 ms, so clicks and redraws never wait on them.
    sys.setswitchinterval(0.002)

    takeover = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--takeover=")
                     and a.split("=", 1)[1].isdigit()), None)
    # only one bar at a time: a second launch just exits (a reload hand-over is allowed through)
    if not take_instance_lock() and not takeover:
        print("OrbitBar is already running. Right-click the bar > Quit OrbitBar first, or use Reload.")
        return
    if getattr(sys, "frozen", False) and not takeover:
        p = pending_update()                  # downloaded last time: install it before the bar even appears
        if p and p.get("attempted"):
            # installing it already failed once: start normally (the next update check offers it again)
            setup_logging()
            print(f"update {p['version']}: the last install didn't finish - starting {APP_VERSION} instead")
            try:
                os.remove(PENDING_PATH)
            except OSError:
                pass
        elif p and load_config().get("auto_update", True) and can_self_update():
            setup_logging()
            print(f"--- installing OrbitBar {p['version']} (downloaded earlier) ---")
            run_updater(p)
            return

    cfg = load_config()
    scale = float(cfg.get("scale", 1.0) or 1.0)
    if abs(scale - 1.0) > 0.01:                       # Qt scales every widget, font and card evenly
        os.environ["QT_SCALE_FACTOR"] = f"{max(0.75, min(scale, 2.0)):.2f}"
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    apply_theme(cfg)

    _taskbar = NativeTaskbar()
    if cfg.get("hide_windows_taskbar", True):
        _taskbar.hide()
    atexit.register(cleanup)
    app.aboutToQuit.connect(cleanup)
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    QTimer(app, interval=300, timeout=lambda: None).start()   # let Python handle Ctrl+C

    _bar = Bar(cfg, _taskbar)

    def go_live():
        _bar._live = True
        if _bar.tray and _bar.tray.host.active:
            QTimer.singleShot(500, _bar.tray.host.after_takeover)
        if "--no-guard" not in sys.argv:
            try:
                launch_detached([f"--guard={os.getpid()}"])
            except Exception as ex:
                print("watchdog unavailable:", ex)
        _bar.show()
        _bar.place()
        _bar.start_window_events()
        _bar.tick_fast()
        _bar.tick_slow()
        if cfg.get("autohide"):
            _bar.conceal()

    if takeover:
        # Reload hand-over: everything is built. Appear on top of the old bar first, then tell it to
        # leave, then take over the screen space - the bar never blinks.
        _bar.winId()
        if not cfg.get("autohide"):
            _bar.pre_show()
            app.processEvents()
        ev = kernel32.CreateEventW(None, True, False, handover_event_name(takeover))
        kernel32.SetEvent(ev)
        if not cfg.get("autohide"):
            _bar.fade_to(1.0, 170)                           # fades in while the old bar fades out
        old = kernel32.OpenProcess(0x00100000, False, takeover)             # SYNCHRONIZE
        t0 = time.monotonic()

        def wait_old():
            if not old or signaled(old) or time.monotonic() - t0 > 5:
                poll.stop()
                take_instance_lock()
                _bar.fade.stop()
                _bar.setWindowOpacity(1.0)
                go_live()
        poll = QTimer(app, interval=15, timeout=wait_old)
        poll.start()
    else:
        go_live()
    app.primaryScreen().geometryChanged.connect(lambda *_: _bar and _bar.place())
    rc = app.exec()
    # Background helpers (a PowerShell security check, a weather request, the camera monitor) may still
    # be mid-call. Restore the taskbar, then exit at once instead of waiting on them; this also avoids
    # Qt's "QThread: Destroyed while thread is still running" message.
    cleanup()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    main()

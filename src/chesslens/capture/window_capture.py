"""Capture the client area of an external mirroring app's window (spec §4.2).

This reads visible desktop pixels. A covered, minimized, or off-screen window
cannot be captured, so the source reports that state and returns no frame
rather than handing on pixels from whatever is in front of it.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass

import mss
import numpy as np
import win32con
import win32gui
import win32process

from chesslens.capture.base import SourceLostError, SourceState, SourceStatus

_DWMWA_CLOAKED = 14
_GA_ROOT = 2
_MONITOR_DEFAULTTONULL = 0
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_MIN_CLIENT_PX = 16

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
_user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = wintypes.HMONITOR

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


def ensure_dpi_awareness() -> None:
    """Work in physical pixels so Win32 rectangles line up with mss on scaled displays.

    Qt already does this for the GUI, in which case the call fails harmlessly.
    """
    _user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    process_name: str
    pid: int

    @property
    def label(self) -> str:
        return f"{self.title}  —  {self.process_name or f'pid {self.pid}'}"

    def match_rank(self, text: str) -> int:
        """0: process name contains text, 1: title contains it, 2: no match.

        Process name wins so the receiver app beats, say, a browser tab
        that merely has the app's name in its title.
        """
        text = text.lower()
        if text and text in self.process_name.lower():
            return 0
        if text and text in self.title.lower():
            return 1
        return 2


def list_windows(hint: str = "") -> list[WindowInfo]:
    """Top-level windows a user could pick as the mirroring source, best `hint` matches first.

    Within each rank the order is Z-order (front to back). Windows belonging
    to this process are left out so ChessLens cannot capture itself.
    """
    ensure_dpi_awareness()
    own_pid = os.getpid()
    found: list[WindowInfo] = []

    def visit(hwnd: int, _: object) -> bool:
        if _is_candidate(hwnd):
            info = describe_window(hwnd)
            if info.pid != own_pid:
                found.append(info)
        return True

    win32gui.EnumWindows(visit, None)
    return sorted(found, key=lambda w: w.match_rank(hint))


def find_window(match: str) -> WindowInfo | None:
    """The best window whose process name or title contains `match` (case-insensitive)."""
    best = next(iter(list_windows(match)), None)
    return best if best is not None and best.match_rank(match) < 2 else None


def describe_window(hwnd: int) -> WindowInfo:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        title=win32gui.GetWindowText(hwnd),
        class_name=win32gui.GetClassName(hwnd),
        process_name=_process_name(pid),
        pid=pid,
    )


def client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the client area in screen pixels, excluding title bar and borders."""
    _, _, width, height = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    return left, top, width, height


class WindowCaptureSource:
    """FrameSource that reads a window's client area from the desktop with mss."""

    def __init__(self, window: WindowInfo, check_occlusion: bool = True, probe_grid: int = 6) -> None:
        self.window = window
        self.check_occlusion = check_occlusion
        self.probe_grid = probe_grid
        self.source_id = f"window:{window.process_name or window.pid}:{window.class_name}"
        self.status = SourceStatus(SourceState.IDLE)
        self._sct: mss.base.MSS | None = None

    def start(self) -> None:
        ensure_dpi_awareness()
        if not self._alive():
            replacement = self._reacquire()
            if replacement is None:
                self.status = SourceStatus(SourceState.LOST, f"'{self.window.title}' is not open")
                raise SourceLostError(self.status.detail)
            self.window = replacement
        # mss holds thread-bound GDI handles, so it is created on the capture thread.
        self._sct = mss.MSS()

    def read(self) -> np.ndarray | None:
        if self._sct is None:
            raise SourceLostError("window source not started")
        if not self._alive():
            self.status = SourceStatus(SourceState.LOST, f"'{self.window.title}' was closed")
            raise SourceLostError(self.status.detail)

        hwnd = self.window.hwnd
        if win32gui.IsIconic(hwnd):
            self.status = SourceStatus(SourceState.MINIMIZED, "the mirroring window is minimized")
            return None
        if not win32gui.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            self.status = SourceStatus(SourceState.HIDDEN, "the mirroring window is hidden or on another desktop")
            return None
        left, top, width, height = client_rect_on_screen(hwnd)
        if width < _MIN_CLIENT_PX or height < _MIN_CLIENT_PX:
            self.status = SourceStatus(SourceState.HIDDEN, "the mirroring window is too small to capture")
            return None
        problem = self._visibility_problem(left, top, width, height)
        if problem is not None:
            self.status = problem
            return None

        try:
            shot = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        except mss.ScreenShotError as exc:  # e.g. the secure desktop (UAC, lock screen) is showing
            self.status = SourceStatus(SourceState.HIDDEN, f"the desktop could not be read ({exc})")
            return None
        image = np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR
        if _is_blank(image):
            self.status = SourceStatus(SourceState.BLANK, "the mirroring window shows a blank picture")
            return None
        self.status = SourceStatus(SourceState.OK)
        return image

    def stop(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def _alive(self) -> bool:
        """The handle still names the same process's window (handles can be reused after close)."""
        hwnd = self.window.hwnd
        return bool(win32gui.IsWindow(hwnd)) and win32process.GetWindowThreadProcessId(hwnd)[1] == self.window.pid

    def _reacquire(self) -> WindowInfo | None:
        """Find the same app's window again after it was closed and reopened; its title may have changed."""
        old = self.window
        same_app = [
            w
            for w in list_windows()
            if w.class_name == old.class_name
            and (w.process_name == old.process_name if old.process_name else w.title == old.title)
        ]
        return next((w for w in same_app if w.title == old.title), same_app[0] if same_app else None)

    def _visibility_problem(self, left: int, top: int, width: int, height: int) -> SourceStatus | None:
        """Sample a grid inside the client area: every point must be on a monitor and show this window."""
        for x, y in _probe_points(left, top, width, height, self.probe_grid):
            if not _user32.MonitorFromPoint(wintypes.POINT(x, y), _MONITOR_DEFAULTTONULL):
                return SourceStatus(SourceState.HIDDEN, "the mirroring window is partly off-screen")
            if self.check_occlusion:
                hit = win32gui.WindowFromPoint((x, y))
                root = win32gui.GetAncestor(hit, _GA_ROOT) if hit else 0
                if root != self.window.hwnd:
                    blocker = win32gui.GetWindowText(root) if root else ""
                    return SourceStatus(SourceState.OCCLUDED, f"covered by '{blocker or 'another window'}'")
        return None


def _probe_points(left: int, top: int, width: int, height: int, n: int) -> Iterator[tuple[int, int]]:
    # Cell centres of an n×n grid, which stay clear of rounded corners and resize borders.
    for i in range(n):
        for j in range(n):
            yield left + int(width * (i + 0.5) / n), top + int(height * (j + 0.5) / n)


def _is_candidate(hwnd: int) -> bool:
    if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
        return False
    if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
        return False
    return not _is_cloaked(hwnd)


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    result = _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return result == 0 and cloaked.value != 0


def _is_blank(image: np.ndarray) -> bool:
    """A single flat colour (black, or any solid fill) carries no picture."""
    sample = image[::8, ::8].reshape(-1, image.shape[2])
    return float(sample.std(axis=0).max()) < 2.0


def _process_name(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(handle)

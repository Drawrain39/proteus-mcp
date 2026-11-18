"""Read-only Win32 window/process metadata; input is delegated to Windows-MCP."""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def physical_desktop():
    user = ctypes.WinDLL("user32", use_last_error=True)
    set_awareness = user.SetThreadDpiAwarenessContext
    set_awareness.argtypes = [ctypes.c_void_p]
    set_awareness.restype = ctypes.c_void_p
    previous = set_awareness(ctypes.c_void_p(-4))  # Per-monitor aware v2
    if not previous:
        raise OSError(ctypes.get_last_error(), "Could not query physical desktop coordinates")
    try:
        yield user
    finally:
        set_awareness(previous)


def proteus_windows(executable: Path | None) -> list[dict]:
    if os.name != "nt":
        return []
    with physical_desktop() as user:
        return _enumerate_proteus(user, executable)


def _enumerate_proteus(user, executable: Path | None) -> list[dict]:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetForegroundWindow.restype = wintypes.HWND
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    results = []
    foreground = user.GetForegroundWindow()
    left, top, width, height = (user.GetSystemMetrics(index) for index in (76, 77, 78, 79))
    expected = str(executable.resolve()).casefold() if executable else None
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            name = ctypes.create_unicode_buffer(32768)
            count = wintypes.DWORD(len(name))
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(count)):
                return True
            image = name.value
        finally:
            kernel.CloseHandle(handle)
        if (expected and image.casefold() != expected) or (not expected and Path(image).name.lower() != "pds.exe"):
            return True
        title = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
        user.GetWindowTextW(hwnd, title, len(title))
        rect = wintypes.RECT()
        if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        results.append({"hwnd": int(hwnd), "pid": pid.value, "title": title.value, "exe": image,
                        "minimized": bool(user.IsIconic(hwnd)), "foreground": hwnd == foreground,
                        "rect": [max(left, rect.left), max(top, rect.top),
                                 min(left + width, rect.right), min(top + height, rect.bottom)]})
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not user.EnumWindows(callback, 0):
        raise OSError(ctypes.get_last_error(), "EnumWindows failed")
    return results


def assert_target_point(target: dict, x: int, y: int) -> None:
    from ctypes import wintypes

    with physical_desktop() as user:
        user.WindowFromPoint.argtypes = [wintypes.POINT]
        user.WindowFromPoint.restype = wintypes.HWND
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        hwnd = user.WindowFromPoint(wintypes.POINT(x, y))
        if hwnd and user.GetAncestor(hwnd, 2) == target["hwnd"]:  # GA_ROOT
            return
        if hwnd:
            # Native popup menus are top-level windows, but share their owner's GUI thread.
            user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            classname = ctypes.create_unicode_buffer(32)
            user.GetClassNameW(hwnd, classname, len(classname))
            if (classname.value == "#32768"
                    and user.GetWindowThreadProcessId(hwnd, None)
                    == user.GetWindowThreadProcessId(target["hwnd"], None)):
                return
        raise ValueError("The input point is covered or outside the observed Proteus window")


def unique_target(executable: Path, *, require_foreground: bool = False) -> dict:
    windows = proteus_windows(executable)
    pids = {w["pid"] for w in windows}
    if len(pids) != 1:
        raise ValueError(f"Expected one Proteus process with windows; found {len(pids)}")
    focused = [w for w in windows if w["foreground"]]
    if require_foreground and len(focused) != 1:
        raise ValueError("Proteus is not foreground; focus it and observe again")
    if focused:
        target = focused[0]
    else:
        # A foreground modal wins; otherwise require one main window, not arbitrary dialogs.
        mains = [w for w in windows if "Proteus" in w["title"]]
        if len(mains) != 1:
            raise ValueError("Ambiguous Proteus windows; select a unique target manually")
        target = mains[0]
    if target["minimized"]:
        raise ValueError("Proteus window is minimized; restore it before observing")
    if target["rect"][2] <= target["rect"][0] or target["rect"][3] <= target["rect"][1]:
        raise ValueError("Invalid target window bounds")
    return target


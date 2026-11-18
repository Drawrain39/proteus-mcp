"""Read-only environment discovery. Does not launch apps or edit configuration."""

from __future__ import annotations

import ctypes
import importlib.metadata
import os
import platform
import shutil
from pathlib import Path

from .config import Settings
from .evidence import utc_now


def file_version(path: Path) -> dict:
    if os.name != "nt":
        return {"status": "UNVERIFIED", "reason": "Windows version resource unavailable"}
    from ctypes import wintypes

    dll = ctypes.WinDLL("version", use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                 ctypes.POINTER(wintypes.UINT)]
    handle = wintypes.DWORD()
    size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size:
        return {"status": "UNVERIFIED", "reason": "No version resource"}
    buffer = ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
        return {"status": "UNVERIFIED", "reason": "Version resource read failed"}
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not dll.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return {"status": "UNVERIFIED", "reason": "No fixed file version"}
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    ms, ls = words[2], words[3]
    version = f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"
    return {"status": "PASS", "file_version": version}


def discover_proteus(settings: Settings) -> Path | None:
    if settings.proteus_exe:
        return settings.proteus_exe if settings.proteus_exe.is_file() else None
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        folder = os.environ.get(env)
        if folder:
            candidate = Path(folder) / "Labcenter Electronics/Proteus 8 Professional/BIN/PDS.EXE"
            if candidate.is_file():
                return candidate
    return None


def doctor(settings: Settings) -> dict:
    exe = discover_proteus(settings)
    commands = {name: shutil.which(name) for name in ("uv", "uvx", "node", "git", "codex", "UV4", "C51")}
    versions = {}
    for package in ("mcp", "pydantic", "proteus-mcp"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "NOT_INSTALLED"
    windows = []
    desktop_error = None
    if os.name == "nt":
        try:
            from .winread import proteus_windows
            windows = proteus_windows(exe)
        except (OSError, ValueError) as exc:
            desktop_error = str(exc)
    config = Path.home() / ".codex/config.toml"
    mcp_names = []
    if config.is_file():
        import tomllib
        with config.open("rb") as stream:
            mcp_names = list(tomllib.load(stream).get("mcp_servers", {}))
    return {"timestamp": utc_now(), "status": "PASS" if os.name == "nt" and exe else "BLOCKED",
            "os": platform.platform(), "local_windows": os.name == "nt", "python": platform.python_version(),
            "workspace": str(settings.workspace), "commands": commands, "packages": versions,
            "proteus": {"path": str(exe) if exe else None, "version": file_version(exe) if exe else None,
                        "windows": windows, "diagnostic_error": desktop_error},
            "keil": {"path": str(settings.keil_exe) if settings.keil_exe else commands["UV4"],
                     "compiler": commands["C51"], "status": "UNVERIFIED"},
            "codex": {"config_exists": config.is_file(), "mcp_server_names": mcp_names},
            "windows_backend": {"python": str(settings.windows_python) if settings.windows_python else None,
                                "installed": bool(settings.windows_python and settings.windows_python.is_file()),
                                "gui_enabled": settings.gui_enabled},
            "notes": ["Executable detection is not license, desktop, drawing or simulation acceptance."]}




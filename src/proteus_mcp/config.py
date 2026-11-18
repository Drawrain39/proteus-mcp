from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: Path
    proteus_exe: Path | None = None
    keil_exe: Path | None = None
    windows_python: Path | None = None
    gui_enabled: bool = False
    canvas_click_settle_ms: int = Field(default=125, ge=100, le=500, strict=True)
    timeout_seconds: float = Field(default=45, ge=1, le=120)

    def within_workspace(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError(f"Path is outside configured workspace: {path}")
        return path


def load_settings(workspace: Path | None = None) -> Settings:
    root = (workspace or Path.cwd()).resolve()
    config_path = root / "config.local.json"
    data = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    # The command-line root is authoritative; config cannot widen the workspace.
    data["workspace"] = root
    for key in ("proteus_exe", "keil_exe", "windows_python"):
        if data.get(key):
            value = Path(data[key]).expanduser()
            data[key] = value if value.is_absolute() else (root / value).resolve()
    return Settings.model_validate(data)


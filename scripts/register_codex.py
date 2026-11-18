"""Add this workspace's MCP entries without reserializing the user's TOML."""

import json
import os
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from proteus_mcp.config import load_settings


def main() -> None:
    settings = load_settings(Path(__file__).resolve().parents[1])
    windows_python = settings.windows_python
    python = Path(sys.executable).absolute()
    if not windows_python or not windows_python.is_file():
        raise SystemExit("Set windows_python to the installed Windows-MCP interpreter first.")
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    config = home / "config.toml"
    if config.is_symlink():
        raise SystemExit("Resolve the Codex config symbolic link before registration.")
    if not config.is_file():
        raise SystemExit(f"Codex config not found: {config}")
    original = config.read_bytes()
    current = tomllib.loads(original.decode("utf-8"))
    entries = {
        "windows_gui": {
            "command": str(windows_python),
            "args": ["-m", "windows_mcp", "serve", "--tools", "ControlStatus"],
            "startup_timeout_sec": 45,
            "tool_timeout_sec": 120,
            "env": {"ANONYMIZED_TELEMETRY": "false", "PYTHONIOENCODING": "utf-8"},
        },
        "proteus": {
            "command": str(python),
            "args": ["-m", "proteus_mcp", "--workspace", str(settings.workspace), "serve"],
            "startup_timeout_sec": 45,
            "tool_timeout_sec": 120,
        },
    }
    added = {}
    sections = []
    for name, entry in entries.items():
        existing = current.get("mcp_servers", {}).get(name)
        if existing is not None:
            if existing != entry:
                raise SystemExit(f"Existing {name} entry differs. Config left untouched.")
            continue
        added[name] = entry
        lines = [f"[mcp_servers.{name}]"]
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}"
                     for key, value in entry.items() if key != "env")
        if "env" in entry:
            lines.append(f"[mcp_servers.{name}.env]")
            lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry["env"].items())
        sections.append("\n".join(lines))
    if not added:
        print("Already registered: windows_gui, proteus")
        return
    updated = original + ("\n\n" + "\n\n".join(sections) + "\n").encode("utf-8")
    expected = {**current, "mcp_servers": {**current.get("mcp_servers", {}), **added}}
    if tomllib.loads(updated.decode("utf-8")) != expected:
        raise SystemExit("Proposed TOML changes additional settings. Config left untouched.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = config.with_name(f"config.toml.proteus-{stamp}.bak")
    with backup.open("xb") as stream:
        stream.write(original)
    with tempfile.NamedTemporaryFile(mode="wb", dir=home, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(updated)
    try:
        if config.read_bytes() != original:
            raise SystemExit("Codex config changed during registration. Retry from the new config.")
        temporary.replace(config)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Added {', '.join(added)}. Backup: {backup}")


if __name__ == "__main__":
    main()

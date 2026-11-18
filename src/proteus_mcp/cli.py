from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.shared.exceptions import McpError

from .backend import WindowsMcpBackend
from .config import load_settings
from .doctor import doctor
from .evidence import sha256
from .netlist import verify_netlist
from .planner import plan_circuit
from .runner import run_circuit
from .spec import CircuitSpec, read_spec, validate_spec


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="proteus-mcp")
    result.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run stdio MCP server")
    commands.add_parser("doctor", help="Read-only environment report")
    commands.add_parser("schema", help="Print CircuitSpec JSON Schema")
    commands.add_parser("windows-probe", help="MCP initialize + tools/list, no GUI input")
    commands.add_parser("windows-observe", help="Capture foreground Proteus, no GUI input")
    compare = commands.add_parser("verify-netlist", help="Compare a Proteus logical SDF export with CircuitSpec")
    compare.add_argument("--spec", required=True)
    compare.add_argument("--netlist", required=True)
    for name in ("validate", "dry-run", "run"):
        command = commands.add_parser(name)
        command.add_argument("--spec", required=True)
        if name == "run":
            command.add_argument("--backend", choices=("mock", "windows"), default="mock")
            command.add_argument("--output", default="artifacts/runs")
    return result


async def probe(settings, *, capture: bool = False) -> dict:
    backend = WindowsMcpBackend(settings)
    try:
        if capture:
            metadata, _ = await backend.observe()
            metadata.pop("observation_id")
            metadata.pop("expires_seconds")
            metadata.pop("note")
            metadata["sha256"] = sha256(Path(metadata["screenshot"]))
            return metadata
        return await backend.capabilities()
    finally:
        await backend.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        settings = load_settings(args.workspace)
        if args.command == "serve":
            from .server import make_server
            make_server(settings).run(transport="stdio")
            return
        if args.command == "doctor":
            value = doctor(settings)
        elif args.command == "schema":
            value = CircuitSpec.model_json_schema()
        elif args.command in {"windows-probe", "windows-observe"}:
            value = asyncio.run(probe(settings, capture=args.command == "windows-observe"))
        else:
            spec = read_spec(settings.within_workspace(args.spec))
            if args.command == "validate":
                value = validate_spec(spec)
            elif args.command == "dry-run":
                value = plan_circuit(spec)
            elif args.command == "verify-netlist":
                value = verify_netlist(spec, settings.within_workspace(args.netlist))
            else:
                value = run_circuit(settings, spec, args.output, args.backend)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        if value.get("status") in {"FAIL", "BLOCKED"}:
            sys.exit(2)
    except (ExceptionGroup, McpError, ValueError, OSError, TimeoutError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        sys.exit(2)


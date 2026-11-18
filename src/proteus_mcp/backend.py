"""Windows-MCP transport plus fresh, single-use, target-scoped observations."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from .config import Settings
from .doctor import discover_proteus
from .evidence import EvidenceStore, sha256
from .netlist import verify_netlist
from .runner import prepare_run
from .spec import read_spec
from .winread import assert_target_point, unique_target

WINDOWS_TOOLS = ("Screenshot", "Click", "Type", "Shortcut")
WINDOWS_STATUS = "ControlStatus"  # Always surfaced by Windows-MCP 0.8.9.
SHORTCUTS = {"escape", "enter", "tab", "shift+tab", "ctrl+a", "ctrl+n", "ctrl+o", "ctrl+s",
             "ctrl+shift+s", "ctrl+z", "ctrl+y", "p", "r", "l", "m", "e", "space", "f8",
             "up", "down", "left", "right", "home", "end"}


class WindowsMcpBackend:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: dict = {}
        self.queue: asyncio.Queue[tuple[Callable[[], Awaitable], asyncio.Future] | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.observations: dict = {}
        self.store: EvidenceStore | None = None
        self.project_destination: Path | None = None
        self.run_screenshots: set[str] = set()

    async def _request(self, operation: Callable[[], Awaitable]):
        if self.worker is None:
            self.worker = asyncio.create_task(self._serve_requests())
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((operation, future))
        return await future

    async def _serve_requests(self) -> None:
        # MCP's AnyIO contexts must be opened and closed by the same task.
        try:
            while (request := await self.queue.get()) is not None:
                operation, future = request
                if future.cancelled():
                    continue
                try:
                    result = await operation()
                except Exception as exc:  # noqa: BLE001 - forward the original tool error to its caller
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if not future.done():
                        future.set_result(result)
                if future.cancelled():
                    await self._release()
        finally:
            try:
                await self._release()
            finally:
                if self.stack:
                    await self.stack.aclose()
                self.stack = self.session = None

    async def _connect(self) -> None:
        if self.session:
            return
        if os.name != "nt":
            raise ValueError("Windows-MCP requires local Windows")
        interpreter = self.settings.windows_python
        if not interpreter or not interpreter.is_file():
            raise ValueError("Set windows_python to the isolated Windows-MCP Python executable")
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=str(interpreter), args=["-m", "windows_mcp", "serve", "--tools", ",".join(WINDOWS_TOOLS)],
                env={"ANONYMIZED_TELEMETRY": "false", "PYTHONIOENCODING": "utf-8"},
                cwd=str(self.settings.workspace))))
            session = await stack.enter_async_context(ClientSession(
                *streams, read_timeout_seconds=timedelta(seconds=self.settings.timeout_seconds)))
            await session.initialize()
            listing = await session.list_tools()
            tools = {tool.name: tool.model_dump(mode="json") for tool in listing.tools}
            if set(tools) != {*WINDOWS_TOOLS, WINDOWS_STATUS}:
                raise ValueError(f"Unexpected Windows-MCP tool set: {sorted(tools)}")
        except BaseException:
            await stack.aclose()
            raise
        self.stack, self.session, self.tools = stack, session, tools

    async def close(self) -> None:
        if self.worker:
            self.queue.put_nowait(None)
            await asyncio.shield(self.worker)
            self.worker = None

    async def _release(self):
        self.observations.clear()
        if self.session:
            return await self._call(WINDOWS_STATUS, {"release": True})
        return None

    async def release(self) -> dict:
        async def stop():
            await self._release()
            if self.project_destination and self.store:
                self.store.event("run_released", project_destination=str(self.project_destination))
            self.store = self.project_destination = None
            self.run_screenshots.clear()
            return {"status": "PASS", "observations_cleared": True}
        return await self._request(stop)

    async def start_circuit(self, spec: dict, output: str) -> dict:
        async def start():
            if self.project_destination:
                return {"status": "BLOCKED", "reason": "Record or release the active circuit first."}
            result, store = prepare_run(self.settings, spec, output, "windows")
            if result["status"] != "READY":
                return result
            assert store is not None
            try:
                await self._connect()
            except (ExceptionGroup, McpError, OSError, ValueError, TimeoutError) as exc:
                result = {**result, "status": "BLOCKED", "reason": str(exc)}
                store.write("result.json", result)
                return result
            self.store = store
            self.project_destination = Path(result["project_destination"])
            self.run_screenshots.clear()
            self.observations.clear()
            self.store.event("run_started", project_destination=str(self.project_destination))
            return result
        return await self._request(start)

    async def record_circuit(self, checks: dict[str, dict[str, str]]) -> dict:
        async def record():
            if not self.store or self.project_destination is None:
                raise ValueError("No active Windows circuit run")
            path = self.settings.within_workspace(self.project_destination)
            if path != self.project_destination or not path.is_file() or path.stat().st_size == 0:
                raise ValueError("Save a nonempty Proteus project at the assigned run destination first")
            if set(checks) != {"pins_and_properties", "connectivity", "reopened", "simulation"}:
                raise ValueError("Provide evidence for pins_and_properties, connectivity, reopened and simulation")
            pictures = set()
            for check in checks.values():
                filename = check.get("screenshot", "")
                shot = self.store.path / filename
                if filename not in self.run_screenshots or not shot.is_file() or not check.get("notes", "").strip():
                    raise ValueError("Each check needs a captured screen-*.png basename and observation notes")
                pictures.add(filename)
            if len(pictures) < 2 or checks["reopened"]["screenshot"] == checks["simulation"]["screenshot"]:
                raise ValueError("Provide separate screenshots for the reopened project and simulation")
            result = {"status": "RECORDED", "backend": "windows", "review": "agent_observations",
                      "project": str(path), "project_sha256": sha256(path),
                      "evidence_directory": str(self.store.path), "checks": checks}
            netlist = path.with_suffix(".SDF")
            if netlist.is_file():
                actual = verify_netlist(read_spec(self.store.path / "spec.json"),
                                        self.settings.within_workspace(netlist))
                if actual["status"] != "PASS":
                    raise ValueError(f"Exported netlist differs from the run specification: {actual['errors']}")
                result["netlist_verification"] = actual
            await self._release()
            self.store.write("result.json", result)
            self.store = self.project_destination = None
            self.run_screenshots.clear()
            return result
        return await self._request(record)

    async def capabilities(self) -> dict:
        async def read():
            await self._connect()
            return {"status": "PASS", "transport": "stdio", "tools": list(self.tools.values()),
                    "note": "Handshake only; no GUI or simulation result is implied."}
        return await self._request(read)

    async def _call(self, name: str, args: dict):
        if not self.session:
            raise ValueError("Backend not connected")
        result = await self.session.call_tool(name, args)
        messages = [item.text for item in result.content if item.type == "text"]
        if result.isError or any(text.lstrip().lower().startswith(("error", "failed")) for text in messages):
            raise ValueError("Windows-MCP failed: " + "\n".join(messages)[:1500])
        return result

    def _proteus_exe(self) -> Path:
        exe = discover_proteus(self.settings)
        if not exe:
            raise ValueError("Proteus executable not found")
        return exe

    def _store(self) -> EvidenceStore:
        if not self.store:
            self.store = EvidenceStore(self.settings.workspace / "artifacts/gui")
        return self.store

    async def _observe(self):
        self.observations.clear()
        exe = self._proteus_exe()
        target = unique_target(exe, require_foreground=True)
        store = self._store()
        shot = store.path / f"screen-{uuid4().hex}.png"
        result = await self._call("Screenshot", {"region": target["rect"]})
        images = [item for item in result.content if item.type == "image" and item.mimeType == "image/png"]
        if len(images) != 1:
            raise ValueError("Screenshot did not return exactly one PNG")
        if unique_target(exe, require_foreground=True) != target:
            raise ValueError("Target changed during capture; observe again")
        with shot.open("xb") as stream:
            stream.write(base64.b64decode(images[0].data, validate=True))
        if self.project_destination:
            self.run_screenshots.add(shot.name)
        token = uuid4().hex
        self.observations[token] = {"target": target, "time": time.monotonic()}
        metadata = {"status": "PASS", "observation_id": token, "expires_seconds": 30,
                    "target": target, "screenshot": str(shot),
                    "note": "Inspect the returned image; token permits exactly one input."}
        store.event("observe", **metadata)
        return metadata, images

    async def observe(self):
        async def read():
            self._proteus_exe()
            await self._connect()
            return await self._observe()
        return await self._request(read)

    async def action(self, observation_id: str, action: str, *, x: float | None = None,
                     y: float | None = None, text: str | None = None, key: str | None = None,
                     button: str = "left", double: bool = False, clear: bool = False):
        async def send():
            if not self.settings.gui_enabled:
                raise ValueError("GUI input is disabled in config.local.json")
            exe = self._proteus_exe()
            snapshot = self.observations.pop(observation_id, None)
            if not snapshot or time.monotonic() - snapshot["time"] > 30:
                raise ValueError("Missing, consumed or stale observation; observe again")
            target = unique_target(exe, require_foreground=True)
            if target != snapshot["target"]:
                raise ValueError("Window/focus/bounds changed; observe again")
            if action in {"click", "type", "hover"}:
                if (x is None or y is None or isinstance(x, bool) or isinstance(y, bool)
                        or not 0 <= x < 1 or not 0 <= y < 1):
                    raise ValueError("x/y must be normalized target-window coordinates in [0, 1)")
                left, top, right, bottom = target["rect"]
                loc = [left + int(x * (right - left)), top + int(y * (bottom - top))]
                assert_target_point(target, *loc)
            if action == "hover":
                name, args = "Click", {"loc": loc, "button": "left", "clicks": 0}
            elif action == "click":
                if button not in {"left", "right"}:
                    raise ValueError("Unsupported button")
                name, args = "Click", {"loc": loc, "button": button, "clicks": 2 if double else 1}
            elif action == "type":
                if not text or len(text) > 4096 or any(ord(c) < 32 for c in text):
                    raise ValueError("Use 1..4096 printable characters; shortcuts are separate inputs")
                name, args = "Type", {"text": text, "loc": loc, "clear": clear}
            elif action == "key":
                if key not in SHORTCUTS:
                    raise ValueError(f"Unsupported Proteus shortcut; allowed: {sorted(SHORTCUTS)}")
                name, args = "Shortcut", {"shortcut": key}
            else:
                raise ValueError("Supported actions: click, type, hover, key")
            self._store().event("input_attempt", observation_id=observation_id, tool=name, arguments=args)
            # No blind retries: a failed call can already have caused an input.
            try:
                await self._call(name, args)
                return await self._observe()
            except BaseException as exc:
                self.observations.clear()
                self._store().event("input_stopped", error=str(exc))
                raise
        return await self._request(send)


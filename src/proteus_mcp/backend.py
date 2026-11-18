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
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Settings
from .doctor import discover_proteus
from .evidence import EvidenceStore, sha256
from .netlist import verify_netlist
from .runner import prepare_run
from .spec import read_spec
from .windows_worker import CANVAS_CLICK, WINDOWS_STATUS, WINDOWS_TOOLS, is_schematic
from .winread import assert_target_point, unique_target

SHORTCUTS = {"escape", "enter", "tab", "shift+tab", "ctrl+a", "ctrl+n", "ctrl+o", "ctrl+s",
             "ctrl+shift+s", "ctrl+z", "ctrl+y", "p", "r", "l", "m", "e", "space", "f8",
             "up", "down", "left", "right", "home", "end"}
MAX_BATCH_CLICKS = 16


class CanvasPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)


class ViewRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    left: float = Field(ge=0, lt=1)
    top: float = Field(ge=0, lt=1)
    right: float = Field(gt=0, le=1)
    bottom: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def check_bounds(self):
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("Area must have positive width and height")
        return self

    def pixels(self, rect: list[int]) -> list[int]:
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        region = [left + int(self.left * width), top + int(self.top * height),
                  left + int(self.right * width), top + int(self.bottom * height)]
        if region[2] <= region[0] or region[3] <= region[1]:
            raise ValueError("Area rounds to an empty pixel rectangle")
        return region


def point_pixels(rect: list[int], x: float, y: float) -> list[int]:
    left, top, right, bottom = rect
    return [left + int(x * (right - left)), top + int(y * (bottom - top))]


def ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


class WindowsMcpBackend:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tools: dict = {}
        self._release_due = False
        self.queue: asyncio.Queue[tuple[Callable[[], Awaitable], asyncio.Future] | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.active_request: asyncio.Future | None = None
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
                self.active_request = future
                try:
                    result = await operation()
                except Exception as exc:  # noqa: BLE001 - forward the original tool error to its caller
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if not future.done():
                        future.set_result(result)
                finally:
                    self.active_request = None
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
        tools_expected = {*WINDOWS_TOOLS, WINDOWS_STATUS}
        exe = discover_proteus(self.settings)
        if exe is not None and self.settings.canvas_click_settle_ms < 500:
            args = ["-B", str(Path(__file__).with_name("windows_worker.py")), str(exe),
                    str(self.settings.canvas_click_settle_ms)]
            tools_expected.add(CANVAS_CLICK)
        else:
            args = ["-B", "-m", "windows_mcp", "serve", "--transport", "stdio",
                    "--tools", ",".join(WINDOWS_TOOLS)]
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=str(interpreter), args=args,
                env={"ANONYMIZED_TELEMETRY": "false", "PYTHONIOENCODING": "utf-8"},
                cwd=str(self.settings.workspace))))
            session = await stack.enter_async_context(ClientSession(
                *streams, read_timeout_seconds=timedelta(seconds=self.settings.timeout_seconds)))
            await session.initialize()
            listing = await session.list_tools()
            tools = {tool.name: tool.model_dump(mode="json") for tool in listing.tools}
            if set(tools) != tools_expected:
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
        if self.session and self._release_due:
            result = await self._call(WINDOWS_STATUS, {"release": True})
            self._release_due = False
            return result
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
                    "canvas_click_settle_ms": self.settings.canvas_click_settle_ms if CANVAS_CLICK in self.tools else 500,
                    "note": "Handshake only; no GUI or simulation result is implied."}
        return await self._request(read)

    async def _call(self, name: str, args: dict):
        if not self.session:
            raise ValueError("Backend not connected")
        if name != WINDOWS_STATUS:
            self._release_due = True
        result = await self.session.call_tool(name, args)
        messages = [item.text for item in result.content if item.type == "text"]
        blocked_click = name == "Click" and any("so the click went nowhere" in text for text in messages)
        if result.isError or blocked_click or any(
            text.lstrip().lower().startswith(("error", "failed")) for text in messages
        ):
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

    async def _observe(
        self, area: ViewRegion | None = None, *, previous: dict | None = None,
        same_target: bool = False, input_ms: float | None = None, issue_token: bool = True,
    ):
        self.observations.clear()
        exe = self._proteus_exe()
        target = unique_target(exe, require_foreground=True)
        if previous is not None:
            if target == previous["target"]:
                area = previous.get("area")
            elif same_target:
                raise ValueError("Window/focus/bounds changed during batch; observe again")
        frame = area.pixels(target["rect"]) if area else target["rect"]
        store = self._store()
        shot = store.path / f"screen-{uuid4().hex}.png"
        started = time.perf_counter()
        result = await self._call("Screenshot", {"region": frame})
        capture_ms = ms(time.perf_counter() - started)
        images = [item for item in result.content if item.type == "image" and item.mimeType == "image/png"]
        if len(images) != 1:
            raise ValueError("Screenshot did not return exactly one PNG")
        if unique_target(exe, require_foreground=True) != target:
            raise ValueError("Target changed during capture; observe again")
        image_bytes = base64.b64decode(images[0].data, validate=True)
        with shot.open("xb") as stream:
            stream.write(image_bytes)
        if self.project_destination:
            self.run_screenshots.add(shot.name)
        timing = {"capture_ms": capture_ms}
        if input_ms is not None:
            timing["input_ms"] = input_ms
        metadata = {"status": "PASS", "target": target, "screenshot": str(shot),
                    "frame_rect": frame, "image_bytes": len(image_bytes), "timing": timing,
                    "note": "Diagnostic image only; observe again before input."}
        if issue_token:
            token = uuid4().hex
            self.observations[token] = {
                "target": target, "time": time.monotonic(), "frame": frame, "area": area,
            }
            metadata.update(observation_id=token, expires_seconds=30,
                            note="Coordinates are relative to this image. The observation token is single-use.")
        store.event("observe", **metadata)
        return metadata, images

    async def observe(self, area: ViewRegion | None = None):
        async def read():
            self._proteus_exe()
            await self._connect()
            return await self._observe(area)
        return await self._request(read)

    def _take_observation(self, observation_id: str):
        if not self.settings.gui_enabled:
            raise ValueError("GUI input is disabled in config.local.json")
        exe = self._proteus_exe()
        snapshot = self.observations.pop(observation_id, None)
        if not snapshot or time.monotonic() - snapshot["time"] > 30:
            raise ValueError("Missing, consumed or stale observation; observe again")
        return exe, snapshot

    def _check_target(
        self, exe: Path, snapshot: dict, loc: list[int] | None = None, *, allow_menu: bool = True,
    ) -> None:
        target = unique_target(exe, require_foreground=True)
        if target != snapshot["target"]:
            raise ValueError("Window/focus/bounds changed; observe again")
        if loc is not None:
            assert_target_point(target, *loc, allow_menu=allow_menu)

    async def canvas_batch(self, observation_id: str, points: list[CanvasPoint], *, standard_speed: bool = False):
        async def send():
            if not 2 <= len(points) <= MAX_BATCH_CLICKS:
                raise ValueError(f"Provide 2 to {MAX_BATCH_CLICKS} canvas points")
            exe, snapshot = self._take_observation(observation_id)
            if snapshot.get("area") is None or snapshot["frame"] == snapshot["target"]["rect"]:
                raise ValueError("Observe the cropped schematic canvas before batching")
            if not is_schematic(snapshot["target"]["title"]):
                raise ValueError("Batch clicks require the Proteus schematic canvas")
            locs = [point_pixels(snapshot["frame"], point.x, point.y) for point in points]
            store = self._store()
            attempted = completed = 0
            input_seconds = 0.0
            batch_id = uuid4().hex
            fast = not standard_speed and CANVAS_CLICK in self.tools
            tool = CANVAS_CLICK if fast else "Click"
            settle_ms = self.settings.canvas_click_settle_ms if fast else 500
            try:
                for loc in locs:
                    if self.active_request is not None and self.active_request.cancelled():
                        raise ValueError("Batch request cancelled")
                    self._check_target(exe, snapshot, loc, allow_menu=False)
                    args = ({"loc": loc, "target": snapshot["target"], "frame": snapshot["frame"]} if fast
                            else {"loc": loc, "button": "left", "clicks": 1})
                    store.event("input_attempt", observation_id=observation_id, tool=tool, arguments=args,
                                batch_id=batch_id, step=attempted + 1, settle_ms=settle_ms)
                    attempted += 1
                    started = time.perf_counter()
                    await self._call(tool, args)
                    input_seconds += time.perf_counter() - started
                    completed += 1
                metadata, images = await self._observe(previous=snapshot, same_target=True, input_ms=ms(input_seconds))
            except (ExceptionGroup, McpError, OSError, ValueError, TimeoutError) as exc:
                self.observations.clear()
                result = {"status": "STOPPED", "batch_id": batch_id,
                          "attempted_clicks": attempted, "acknowledged_clicks": completed,
                          "reason": str(exc),
                          "note": "Observe again and verify the canvas before continuing. Do not replay blindly."}
                images = []
                if attempted and not (self.active_request and self.active_request.cancelled()):
                    try:
                        async with asyncio.timeout(1.5):
                            diagnostic, images = await self._observe(previous=snapshot, same_target=True,
                                                                      issue_token=False)
                        result["diagnostic_screenshot"] = diagnostic["screenshot"]
                    except (ExceptionGroup, McpError, OSError, ValueError, TimeoutError) as capture_error:
                        result["diagnostic_error"] = str(capture_error)
                store.event("batch_stopped", **result)
                return result, images
            batch = {"batch_id": batch_id, "attempted_clicks": attempted, "acknowledged_clicks": completed,
                     "click_tool": tool, "click_settle_ms": settle_ms}
            store.event("batch_completed", **batch, timing=metadata.get("timing", {}))
            return {**metadata, **batch}, images
        return await self._request(send)

    async def action(self, observation_id: str, action: str, *, x: float | None = None,
                     y: float | None = None, text: str | None = None, key: str | None = None,
                     button: str = "left", double: bool = False, clear: bool = False):
        async def send():
            exe, snapshot = self._take_observation(observation_id)
            loc = None
            if action in {"click", "type", "hover"}:
                if (x is None or y is None or isinstance(x, bool) or isinstance(y, bool)
                        or not 0 <= x < 1 or not 0 <= y < 1):
                    raise ValueError("x/y must be normalized target-window coordinates in [0, 1)")
                loc = point_pixels(snapshot.get("frame", snapshot["target"]["rect"]), x, y)
            self._check_target(exe, snapshot, loc)
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
                started = time.perf_counter()
                await self._call(name, args)
                return await self._observe(previous=snapshot, input_ms=ms(time.perf_counter() - started))
            except BaseException as exc:
                self.observations.clear()
                self._store().event("input_stopped", error=str(exc))
                raise
        return await self._request(send)


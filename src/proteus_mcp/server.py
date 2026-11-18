from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Annotated

from anyio import CancelScope
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import Field

from .backend import MAX_BATCH_CLICKS, CanvasPoint, ViewRegion, WindowsMcpBackend
from .config import Settings
from .doctor import doctor
from .planner import plan_circuit
from .runner import run_circuit
from .spec import validate_spec


def make_server(settings: Settings) -> FastMCP:
    windows = WindowsMcpBackend(settings)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield {}
        finally:
            with CancelScope(shield=True):
                await windows.close()

    mcp = FastMCP("proteus-mcp", lifespan=lifespan, instructions=(
        "Use circuit_validate then circuit_plan. Mock PASS is not real Proteus success. "
        "Inspect fresh screenshots before locating GUI input. For confirmed points on one stable schematic "
        "canvas, use proteus_canvas_batch and inspect the returned image before continuing. Use single actions "
        "for dialogs, menus, zoom and uncertain points. Save new projects only in the configured workspace. "
        "Never overwrite "
        "existing user projects. A real run must independently verify pins, wires, properties, save/reopen "
        "and observable simulation behavior. Do not treat the play button as functional evidence."))

    @mcp.tool()
    def proteus_doctor() -> dict:
        """Read-only local OS, executable version, window and dependency detection."""
        return doctor(settings)

    @mcp.tool()
    def circuit_validate(spec: dict) -> dict:
        """Validate CircuitSpec structure, logical pins, connectivity, quantities and LED polarity."""
        return validate_spec(spec)

    @mcp.tool()
    def circuit_plan(spec: dict) -> dict:
        """Generate logical grid placements and semantic actions; never guesses real screen pins."""
        return plan_circuit(spec)

    @mcp.tool()
    async def circuit_run(spec: dict, output: str = "artifacts/runs", backend: str = "mock") -> dict:
        """Record a mock run or reserve a new Windows project and return the steps for live agent execution."""
        if backend == "windows":
            return await windows.start_circuit(spec, output)
        return run_circuit(settings, spec, output, backend)

    @mcp.tool()
    async def circuit_record(checks: dict[str, dict[str, str]]) -> dict:
        """Record agent-observed pins_and_properties, connectivity, reopened and simulation evidence.

        Each check needs a screenshot basename from the active run and notes with the observed result.
        Recording releases desktop control. Only report observations actually verified in Proteus.
        """
        return await windows.record_circuit(checks)

    @mcp.tool()
    async def windows_capabilities() -> dict:
        """Initialize isolated Windows-MCP and list the actual restricted tool schemas, without GUI input."""
        return await windows.capabilities()

    @mcp.tool()
    async def proteus_observe(area: ViewRegion | None = None) -> list:
        """Capture foreground Proteus. Optionally crop with window-relative bounds from 0 to 1.

        Returned action coordinates are relative to the returned image, including after a crop.
        An observation token is single-use and expires after 30 seconds.
        """
        metadata, content = await windows.observe(area)
        return [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)), *content]

    @mcp.tool()
    async def proteus_canvas_batch(
        observation_id: str, points: Annotated[list[CanvasPoint], Field(min_length=2, max_length=MAX_BATCH_CLICKS)],
        standard_speed: bool = False,
    ) -> list:
        """Left-click confirmed points on the same schematic canvas and capture only the final frame.

        First observe a crop containing only the relevant canvas. Supply normalized points relative to
        that crop, in order, for known wire endpoints/bends, placements or simulation switches.
        Do not batch actions that can move the canvas or open a dialog. Inspect the result before continuing.
        Use standard_speed for a batch that needs the original 500 ms wait.
        STOPPED gives partial counts and requires a new observation; do not replay the entire batch.
        """
        metadata, content = await windows.canvas_batch(observation_id, points, standard_speed=standard_speed)
        return [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)), *content]

    @mcp.tool()
    async def proteus_action(observation_id: str, action: str, x: float | None = None,
                             y: float | None = None, text: str | None = None, key: str | None = None,
                             button: str = "left", double: bool = False, clear: bool = False) -> list:
        """One input to observed Proteus, followed by fresh evidence. Click and type require x/y in [0,1).

        Inspect the screenshot before selecting a point. Observe again after focus or window changes.
        No blind retries; unsupported keys, other processes and stale observations are refused.
        """
        metadata, content = await windows.action(observation_id, action, x=x, y=y, text=text, key=key,
                                                button=button, double=double, clear=clear)
        return [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)), *content]

    @mcp.tool()
    async def proteus_release() -> dict:
        """Hand back desktop control and invalidate outstanding observation tokens."""
        return await windows.release()

    return mcp


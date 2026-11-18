"""Private stdio launcher for a guarded canvas batch in Windows-MCP 0.8.9."""

from __future__ import annotations

import argparse
import time
from importlib.metadata import version
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

WINDOWS_TOOLS = ("Screenshot", "Click", "Type", "Shortcut")
WINDOWS_STATUS = "ControlStatus"
CANVAS_BATCH = "ProteusCanvasBatch"
MAX_BATCH_CLICKS = 16
# The parent writes cancellation; the child writes the two progress counters.
CANCELLED, ATTEMPTED, ACKNOWLEDGED = range(3)
BATCH_STATE_SIZE = 3
SUPPORTED_VERSION = "0.8.9"


def is_schematic(title: str) -> bool:
    return title.casefold().endswith(("schematic capture", "原理图绘制"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("settle_ms", type=int, choices=range(100, 500))
    args = parser.parse_args()
    if version("windows-mcp") != SUPPORTED_VERSION:
        raise SystemExit(f"Fast canvas input requires Windows-MCP {SUPPORTED_VERSION}. "
                         "Set canvas_click_settle_ms to 500 for standard input.")

    from windows_mcp import __main__ as upstream
    from windows_mcp.desktop import control_overlay
    from windows_mcp.desktop.control import get_controller
    from windows_mcp.desktop.control_context import current_token
    from windows_mcp.tools.control_policy import requires_control
    from windows_mcp.uia import core as uia
    from winread import assert_target_point, unique_target

    if not all(requires_control(policy, CANVAS_BATCH) for policy in ("relaxed", "balanced", "strict")):
        raise SystemExit("Windows-MCP does not guard the canvas input tool")
    server = upstream._build_mcp()

    @server.tool(name=CANVAS_BATCH)
    def canvas_batch(locs: list[tuple[int, int]], target: dict, frame: tuple[int, int, int, int],
                     state_name: str, deadline: float) -> dict:
        """Execute one guarded sequence on the foreground Proteus canvas."""
        if not 2 <= len(locs) <= MAX_BATCH_CLICKS:
            raise ValueError(f"Provide 2 to {MAX_BATCH_CLICKS} canvas points")
        owner = get_controller()
        if not owner.enabled or current_token.get() is None:
            raise ValueError("Fast canvas input requires active Windows-MCP desktop control")
        left, top, right, bottom = frame
        desktop = upstream._get_desktop()
        state = SharedMemory(name=state_name)
        if state.size < BATCH_STATE_SIZE:
            state.close()
            raise ValueError("Invalid canvas batch state")
        try:
            def checkpoint() -> None:
                if state.buf[CANCELLED]:
                    raise ValueError("Batch request cancelled")
                if time.monotonic() >= deadline:
                    raise ValueError("Batch input deadline expired")
                owner.checkpoint_current()

            for step, (x, y) in enumerate(locs, start=1):
                checkpoint()
                actual = unique_target(args.executable, require_foreground=True)
                if actual != target or not is_schematic(actual["title"]):
                    raise ValueError("Proteus canvas changed before input")
                wx, wy, wr, wb = actual["rect"]
                if not (wx <= left <= x < right <= wr and wy <= top <= y < bottom <= wb):
                    raise ValueError("Canvas click is outside the observed area")
                assert_target_point(actual, x, y, allow_menu=False)
                desktop._validate_screen_point(x, y)
                if warning := desktop._modal_dialog_warning(x, y):
                    raise ValueError(warning)
                control_overlay.ripple(x, y)
                checkpoint()
                state.buf[ATTEMPTED] = step
                uia.Click(x, y, waitTime=args.settle_ms / 1000)
                state.buf[ACKNOWLEDGED] = step
                owner.record_step_current()
            status = {"status": "PASS"}
        except (OSError, RuntimeError, ValueError) as exc:
            status = {"status": "STOPPED", "reason": str(exc)}
        finally:
            attempted, acknowledged = state.buf[ATTEMPTED], state.buf[ACKNOWLEDGED]
            state.close()
        return {**status, "attempted_clicks": attempted, "acknowledged_clicks": acknowledged}

    # Reuse the standard CLI configuration, ownership middleware and stdio lifecycle.
    upstream.main(args=["serve", "--transport", "stdio", "--tools", ",".join((*WINDOWS_TOOLS, CANVAS_BATCH))])


if __name__ == "__main__":
    main()

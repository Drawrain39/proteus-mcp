"""Private stdio launcher for Windows-MCP 0.8.9's guarded canvas click."""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path

WINDOWS_TOOLS = ("Screenshot", "Click", "Type", "Shortcut")
WINDOWS_STATUS = "ControlStatus"
CANVAS_CLICK = "ProteusCanvasClick"
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

    if not all(requires_control(policy, CANVAS_CLICK) for policy in ("relaxed", "balanced", "strict")):
        raise SystemExit("Windows-MCP does not guard the canvas input tool")
    server = upstream._build_mcp()

    @server.tool(name=CANVAS_CLICK)
    def click(loc: tuple[int, int], target: dict, frame: tuple[int, int, int, int]) -> str:
        """One left click on the foreground Proteus canvas; internal to the Proteus MCP child."""
        owner = get_controller()
        if not owner.enabled or current_token.get() is None:
            raise ValueError("Fast canvas input requires active Windows-MCP desktop control")
        owner.checkpoint_current()
        actual = unique_target(args.executable, require_foreground=True)
        if actual != target or not is_schematic(actual["title"]):
            raise ValueError("Proteus canvas changed before input")
        wx, wy, wr, wb = actual["rect"]
        left, top, right, bottom = frame
        x, y = loc
        if not (wx <= left <= x < right <= wr and wy <= top <= y < bottom <= wb):
            raise ValueError("Canvas click is outside the observed area")
        assert_target_point(actual, x, y, allow_menu=False)
        desktop = upstream._get_desktop()
        desktop._validate_screen_point(x, y)
        if warning := desktop._modal_dialog_warning(x, y):
            raise ValueError(warning)
        control_overlay.ripple(x, y)
        owner.checkpoint_current()
        uia.Click(x, y, waitTime=args.settle_ms / 1000)
        owner.record_step_current()
        return "Confirmed single left click."

    # Reuse the standard CLI configuration, ownership middleware and stdio lifecycle.
    upstream.main(args=["serve", "--transport", "stdio", "--tools", ",".join((*WINDOWS_TOOLS, CANVAS_CLICK))])


if __name__ == "__main__":
    main()

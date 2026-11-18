import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_and_tool_calls():
    root = Path(__file__).parents[1]

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "proteus_mcp",
            "--workspace", str(root), "serve"], env={**os.environ, "PYTHONPATH": str(root / "src")})
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "proteus-mcp"
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"proteus_doctor", "circuit_validate", "circuit_plan",
                "circuit_run", "circuit_record", "windows_capabilities", "proteus_observe",
                "proteus_action", "proteus_canvas_batch", "proteus_release"}
            spec = json.loads((root / "examples/led_dc.json").read_text(encoding="utf-8-sig"))
            for name in ("circuit_validate", "circuit_plan"):
                result = await session.call_tool(name, {"spec": spec})
                assert not result.isError
                value = json.loads(result.content[0].text)
                assert value["status"] == "PASS"
            spec["nets"][0]["pins"][0] = "WRONG.1"
            result = await session.call_tool("circuit_validate", {"spec": spec})
            assert json.loads(result.content[0].text)["status"] == "FAIL"
    asyncio.run(run())


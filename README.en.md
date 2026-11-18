# Proteus MCP

English · [简体中文](README.md)

Proteus MCP is a local Model Context Protocol server that helps AI agents plan circuits and work with Proteus 8 schematics on Windows. It validates circuit specifications, generates logical placement and wiring plans, and lets an agent act on screenshots of the Proteus window. Agents can batch confirmed clicks to place components, route wires, or toggle simulation inputs on a stable canvas.

The project started with a digital electronics lab where asking an agent to inspect the full window and click one point at a time took too long. Proteus MCP keeps the visual feedback while using smaller screenshots and batching clicks whose positions are already known.

## Try the LED example

Install `uv` and run these commands from the repository root. This example uses a 9 V battery, a 1 kΩ resistor, and a red LED. You can validate and plan the circuit without opening Proteus.

```powershell
uv sync --locked --python 3.13
uv run --no-sync proteus-mcp validate --spec examples/led_dc.json
uv run --no-sync proteus-mcp dry-run --spec examples/led_dc.json
uv run --no-sync proteus-mcp run --spec examples/led_dc.json --backend mock
```

The mock run saves its plan and execution record. Its simulation status remains `UNVERIFIED`. To try a circuit verified in Proteus, open the [editable LED project](examples/led_dc_minimal.pdsprj) and start the simulation. You can also compare the circuit spec against the [exported SDF netlist](examples/led_dc_minimal.sdf):

```powershell
uv run --no-sync proteus-mcp verify-netlist --spec examples/led_dc.json --netlist examples/led_dc_minimal.sdf
```

## Connect to Proteus on Windows

The live workflow was tested with **Proteus 8.9 SP2** and **Windows-MCP 0.8.9**. Run Codex in the same Windows desktop session as Proteus. The live setup uses Python 3.13 for Proteus MCP and an isolated Python 3.14 environment for Windows-MCP.

From the repository root, set up the two environments:

```powershell
uv sync --locked --python 3.13
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.runtime\python'
$env:UV_CACHE_DIR = Join-Path $PWD '.cache\uv'
uv python install 3.14.6
uv venv --python 3.14.6 .venv-windows
uv pip sync --python .venv-windows/Scripts/python.exe requirements-windows.lock.txt
if (-not (Test-Path -LiteralPath 'config.local.json')) {
    Copy-Item -LiteralPath 'config.example.json' -Destination 'config.local.json'
}
```

1. Check `proteus_exe` and `windows_python` in `config.local.json`. Open Proteus, bring it to the foreground, and run these read-only checks:

   ```powershell
   uv run --no-sync proteus-mcp doctor
   uv run --no-sync proteus-mcp windows-probe
   uv run --no-sync proteus-mcp windows-observe
   ```

2. Launch Codex once so it creates its user configuration, then register the servers:

   ```powershell
   & .\.venv\Scripts\python.exe scripts/register_codex.py
   codex mcp list
   ```

3. GUI input is disabled by default. Once read-only capture works, set `"gui_enabled": true` in `config.local.json` and start a new Codex session. Use `proteus_doctor` to check for `api_revision: 4`. If Windows-MCP prompts for desktop control, respond in the Windows session.

## Work with a schematic

Try this prompt in a Codex session with the `proteus` MCP server loaded:

> Use Proteus MCP and `examples/led_dc.json` to plan the LED circuit. Open the editable example, save it as a new project in the run directory, and verify the component values, connections, and LED behavior after reopening it.

| Tool | What it does |
| --- | --- |
| `circuit_validate`, `circuit_plan` | Check the circuit spec and generate logical placements and connections for the supported LED circuit. The plan does not contain screen coordinates. |
| `circuit_run` | Record a mock run, or reserve an output directory and return steps for a live run with `backend: "windows"`. The MCP client carries out the live steps. |
| `proteus_observe`, `proteus_action` | Inspect the foreground Proteus window and send one input using a fresh observation. Use single actions for menus, dialogs, and zoom changes. |
| `proteus_canvas_batch` | Click 2–16 confirmed points in order and return a fresh image of the canvas on success. |
| `circuit_record`, `proteus_release` | Record observed evidence or return desktop control. Live runs can record pin and property checks, connectivity, reopening, and observed simulation behavior. |

To batch clicks, call `proteus_observe` with an `area` containing just the relevant canvas. Inspect the crop, then send `proteus_canvas_batch` its `observation_id` and the ordered `points`. Each point's `x` and `y` are fractions of the **cropped** image's width and height, in `[0, 1)`. Observation tokens expire after 30 seconds and can be used once.

At the default speed, one request sends the full batch to the Windows worker, which waits 125 ms after each click. The worker checks the foreground window, target point, desktop control, and cancellation before each click. If a batch returns `STOPPED`, `stopped_at_point` identifies the first unconfirmed point, counting from 1. A diagnostic screenshot is included when the original view is still readable. Take a new observation and check what changed before continuing. Client cancellation records partial progress in the local event log. If clicks are missed at the default speed, `standard_speed: true` uses the original 500 ms wait for that batch.

## Measured performance and scope

On a Proteus 8.9 SP2 mod-24 adder, moving the batch into the worker reduced input requests across the process boundary to one per batch. With the same 125 ms wait in both versions, the end-to-end median was about 3% lower for 16 clicks. The 2- and 5-click batches were slightly slower. A separate test of the wait setting measured a drop from 3.45 s to 1.58 s for five clicks when the wait was shortened from 500 ms to 125 ms.

Live verification covered reopening and simulating the LED example, plus component placement, wiring, and input switching on an existing adder schematic. Fully automatic schematic creation from a blank sheet, MCU firmware workflows, and Proteus 9 are outside the verified scope.

- [Windows installation guide (Chinese)](docs/INSTALL_WINDOWS.md)
- [Operating guide (Chinese)](docs/USAGE_zh.md)
- [Architecture and result states (Chinese)](docs/ARCHITECTURE.md)
- [Live verification and benchmark details (Chinese)](reports/acceptance.md)

Licensed under the [MIT License](LICENSE).

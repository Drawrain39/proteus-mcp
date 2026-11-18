import asyncio
import json
import time
from multiprocessing.shared_memory import SharedMemory
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult, ImageContent

from proteus_mcp import backend as backend_module
from proteus_mcp.backend import CanvasPoint, ViewRegion, WindowsMcpBackend
from proteus_mcp.config import Settings
from proteus_mcp.windows_worker import ACKNOWLEDGED, ATTEMPTED, CANCELLED, CANVAS_BATCH


@pytest.fixture
def gui(tmp_path, monkeypatch):
    executable = tmp_path / "PDS.EXE"
    executable.touch()
    backend = WindowsMcpBackend(Settings(workspace=tmp_path, proteus_exe=executable, gui_enabled=True))
    target = {"hwnd": 10, "rect": [-200, 50, 800, 550]}
    monkeypatch.setattr(backend_module, "unique_target", Mock(return_value=target))
    monkeypatch.setattr(backend_module, "assert_target_point", Mock())
    backend._call = AsyncMock()
    backend._observe = AsyncMock(return_value=({"observation_id": "next"}, []))
    backend.observations["fresh"] = {"target": target.copy(), "time": time.monotonic()}
    return backend


def test_type_targets_normalized_field_and_consumes_token(gui):
    async def run():
        try:
            result = await gui.action("fresh", "type", x=0.25, y=0.6, text="1k", clear=True)
            assert result[0]["observation_id"] == "next"
            gui._call.assert_awaited_once_with("Type", {"loc": [50, 350], "text": "1k", "clear": True})
            backend_module.assert_target_point.assert_called_once_with(
                {"hwnd": 10, "rect": [-200, 50, 800, 550]}, 50, 350, allow_menu=True)
            with pytest.raises(ValueError, match="consumed"):
                await gui.action("fresh", "key", key="ctrl+s")
        finally:
            await gui.close()

    asyncio.run(run())


@pytest.mark.parametrize(("failure", "message"), [
    ("expired", "stale"),
    ("changed", "bounds changed"),
    ("covered", "covered"),
    ("out_of_range", "normalized"),
    ("no_coordinates", "normalized"),
])
def test_invalid_observation_or_target_does_not_send_input(gui, failure, message):
    x = 0.5
    if failure == "expired":
        gui.observations["fresh"]["time"] -= 31
    elif failure == "changed":
        backend_module.unique_target.return_value = {"hwnd": 20, "rect": [0, 0, 400, 400]}
    elif failure == "covered":
        backend_module.assert_target_point.side_effect = ValueError("Point is covered")
    elif failure == "out_of_range":
        x = 1
    else:
        x = None

    async def run():
        try:
            with pytest.raises(ValueError, match=message):
                await gui.action("fresh", "type", x=x, y=0.5, text="1k")
            gui._call.assert_not_awaited()
            assert not gui.observations
        finally:
            await gui.close()

    asyncio.run(run())


def test_input_failure_is_not_retried(gui):
    gui._call.side_effect = TimeoutError("unknown input state")

    async def run():
        try:
            with pytest.raises(TimeoutError, match="unknown input state"):
                await gui.action("fresh", "click", x=0.5, y=0.5)
            gui._call.assert_awaited_once()
            gui._observe.assert_not_awaited()
            assert not gui.observations
        finally:
            await gui.close()

    asyncio.run(run())


def batch_points(gui):
    target = backend_module.unique_target.return_value
    target["title"] = "mod24 - Proteus 8 Professional - 原理图绘制"
    region = ViewRegion(left=0.1, top=0.2, right=0.5, bottom=0.8)
    gui.observations["fresh"].update(target=target.copy(), area=region, frame=region.pixels(target["rect"]))
    return [CanvasPoint(x=0.25, y=0.5), CanvasPoint(x=0.75, y=0.5), CanvasPoint(x=0.5, y=0.5)]


@pytest.mark.parametrize(("available", "standard_speed"), [(False, False), (True, False), (True, True)])
def test_canvas_batch_uses_crop_coordinates_and_takes_one_final_screenshot(gui, available, standard_speed):
    points = batch_points(gui)
    if available:
        gui.tools[CANVAS_BATCH] = {}
    fast = available and not standard_speed

    async def batch(_, args):
        state = SharedMemory(name=args["state_name"])
        try:
            state.buf[ATTEMPTED] = state.buf[ACKNOWLEDGED] = len(args["locs"])
        finally:
            state.close()
        return CallToolResult(content=[], structuredContent={"status": "PASS"})

    if fast:
        gui._call.side_effect = batch

    async def run():
        try:
            metadata, _ = await gui.canvas_batch("fresh", points, standard_speed=standard_speed)
            assert metadata["acknowledged_clicks"] == 3
            assert metadata["click_tool"] == (CANVAS_BATCH if fast else "Click")
            locs = [[0, 300], [200, 300], [100, 300]]
            if fast:
                gui._call.assert_awaited_once()
                assert gui._call.await_args.args[1]["locs"] == locs
                assert gui._call.await_args.args[1]["frame"] == [-100, 150, 300, 450]
            else:
                assert [c.args[1]["loc"] for c in gui._call.await_args_list] == locs
            assert metadata["input_calls"] == gui._call.await_count
            assert gui._observe.await_count == 1
            assert gui._observe.await_args.kwargs["same_target"]
            with pytest.raises(ValueError, match="consumed"):
                await gui.canvas_batch("fresh", points)
        finally:
            await gui.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["focus", "input", "worker", "worker_timeout"])
def test_batch_stops_without_retry_when_partially_executed(gui, failure):
    points = batch_points(gui)
    if failure == "focus":
        backend_module.unique_target.side_effect = [
            backend_module.unique_target.return_value, {"hwnd": 20, "rect": [0, 0, 400, 400]}]
        gui._observe.side_effect = ValueError("Target changed during diagnostic")
    elif failure == "input":
        gui._call.side_effect = [None, TimeoutError("Uncertain click")]
        gui._observe.return_value = ({"screenshot": "diagnostic.png"}, [])
    else:
        gui.tools[CANVAS_BATCH] = {}
        gui._observe.return_value = ({"screenshot": "diagnostic.png"}, [])

        async def batch(_, args):
            state = SharedMemory(name=args["state_name"])
            try:
                state.buf[ATTEMPTED] = 2
                state.buf[ACKNOWLEDGED] = 1
                if failure == "worker_timeout":
                    raise TimeoutError("No worker reply")
                return CallToolResult(content=[], structuredContent={"status": "STOPPED", "reason": "Preempted"})
            finally:
                state.close()

        gui._call.side_effect = batch

    async def run():
        try:
            metadata, images = await gui.canvas_batch("fresh", points)
            assert not images
            assert metadata["status"] == "STOPPED" and metadata["acknowledged_clicks"] == 1
            assert metadata["attempted_clicks"] == (1 if failure == "focus" else 2)
            assert metadata["stopped_at_point"] == 2
            assert gui._call.await_count == (2 if failure == "input" else 1)
            assert not gui.observations
            assert gui._observe.await_args.kwargs["issue_token"] is False
            if failure == "focus":
                assert "diagnostic_screenshot" not in metadata
            else:
                assert metadata["diagnostic_screenshot"] == "diagnostic.png"
        finally:
            await gui.close()

    asyncio.run(run())


def test_explicit_release_is_not_repeated_on_shutdown(gui):
    gui.session = Mock()
    gui._release_due = True

    async def run():
        await gui.release()
        await gui.close()
        gui._call.assert_awaited_once_with("ControlStatus", {"release": True})

    asyncio.run(run())


def test_crop_does_not_carry_to_a_new_dialog(gui):
    batch_points(gui)
    previous = gui.observations["fresh"]
    target = {"hwnd": 20, "rect": [40, 50, 480, 550]}
    backend_module.unique_target.return_value = target
    gui._call.return_value = CallToolResult(content=[
        ImageContent(type="image", mimeType="image/png", data=(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVQIHWP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
        )),
    ])

    async def run():
        metadata, _ = await WindowsMcpBackend._observe(gui, previous=previous)
        assert metadata["frame_rect"] == target["rect"]
        assert gui.observations[metadata["observation_id"]]["area"] is None
        gui._call.assert_awaited_once_with("Screenshot", {"region": target["rect"]})

    asyncio.run(run())


def test_cancelling_batch_prevents_the_next_click(gui):
    points = batch_points(gui)

    async def run():
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def click(*_):
            entered.set()
            await resume.wait()

        gui._call.side_effect = click
        running = asyncio.create_task(gui.canvas_batch("fresh", points))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        finally:
            resume.set()
            await gui.close()
        gui._call.assert_awaited_once()
        gui._observe.assert_not_awaited()

    asyncio.run(run())


def test_cancelling_batch_signals_the_worker_and_records_progress(gui):
    points = batch_points(gui)
    gui.tools[CANVAS_BATCH] = {}

    async def run():
        entered = asyncio.Event()

        async def batch(_, args):
            state = SharedMemory(name=args["state_name"])
            try:
                state.buf[ATTEMPTED] = state.buf[ACKNOWLEDGED] = 1
                entered.set()
                while not state.buf[CANCELLED]:
                    await asyncio.sleep(0.001)
                return CallToolResult(content=[], structuredContent={
                    "status": "STOPPED", "reason": "Batch request cancelled",
                })
            finally:
                state.close()

        gui._call.side_effect = batch
        running = asyncio.create_task(gui.canvas_batch("fresh", points))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        finally:
            await asyncio.wait_for(gui.close(), timeout=2)
        events = (gui.store.path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        stopped = json.loads(events[-1])
        assert stopped["op"] == "batch_stopped"
        assert (stopped["attempted_clicks"], stopped["acknowledged_clicks"], stopped["stopped_at_point"]) == (1, 1, 2)
        gui._call.assert_awaited_once()
        gui._observe.assert_not_awaited()

    asyncio.run(run())

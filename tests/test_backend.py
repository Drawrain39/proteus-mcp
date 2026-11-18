import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from proteus_mcp import backend as backend_module
from proteus_mcp.backend import WindowsMcpBackend
from proteus_mcp.config import Settings


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
                {"hwnd": 10, "rect": [-200, 50, 800, 550]}, 50, 350)
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

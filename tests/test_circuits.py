import copy
import json
from pathlib import Path

import pytest

from proteus_mcp.config import Settings, load_settings
from proteus_mcp.planner import plan_circuit
from proteus_mcp.runner import run_circuit
from proteus_mcp.spec import quantity, read_spec, validate_spec


@pytest.fixture
def led():
    return read_spec(Path(__file__).parents[1] / "examples/led_dc.json")


def test_draft_led_is_logically_valid_but_library_is_unverified(led):
    result = validate_spec(led)
    assert result["valid"] and result["library_pins"] == "UNVERIFIED"


@pytest.mark.parametrize("change", ["reverse", "short", "dangling", "unknown_pin", "duplicate", "zero", "extra"])
def test_bad_electrical_inputs_are_rejected(led, change):
    if change == "reverse":
        led["nets"][1]["pins"][1] = "D1.K"
        led["nets"][2]["pins"][0] = "D1.A"
    elif change == "short":
        led["nets"][0]["pins"].append("R1.2")
        led["nets"][1]["pins"].remove("R1.2")
    elif change == "dangling":
        led["nets"][2]["pins"].remove("BT1.negative")
    elif change == "unknown_pin":
        led["nets"][0]["pins"][0] = "BT1.9"
    elif change == "duplicate":
        led["components"].append(copy.deepcopy(led["components"][0]))
    elif change == "zero":
        led["components"][1]["properties"]["resistance"] = "0"
    elif change == "extra":
        led["components"][0]["x"] = 100
    assert not validate_spec(led)["valid"]


def test_plan_contains_no_screen_coordinates_or_guessed_pins(led):
    plan = plan_circuit(led)
    assert not plan["gui_execution_ready"]
    assert [step["op"] for step in plan["steps"]].count("wire_net") == 3
    assert all("x" not in placement and "y" not in placement for placement in plan["placements"])


def test_mock_twice_preserves_original_and_does_not_create_project(tmp_path, led):
    settings = Settings(workspace=tmp_path.resolve())
    results = [run_circuit(settings, led, "runs", "mock") for _ in range(2)]
    assert results[0]["evidence_directory"] != results[1]["evidence_directory"]
    for result in results:
        assert result["status"] == "PASS" and result["project"] is None
        assert result["simulation"] == "UNVERIFIED"
        assert (Path(result["evidence_directory"]) / "events.jsonl").is_file()
    assert not list(tmp_path.rglob("*.pdsprj"))


def test_real_run_never_claims_unverified_simulation_success(tmp_path, led):
    settings = Settings(workspace=tmp_path.resolve())
    result = run_circuit(settings, led, "runs", "windows")
    assert result["status"] == "BLOCKED" and result["project"] is None
    assert json.loads((Path(result["evidence_directory"]) / "result.json").read_text())["status"] == "BLOCKED"


def test_path_escape_and_config_cannot_widen_workspace(tmp_path):
    (tmp_path / "config.local.json").write_text(json.dumps({"workspace": str(tmp_path.parent)}))
    settings = load_settings(tmp_path)
    assert settings.workspace == tmp_path.resolve()
    with pytest.raises(ValueError, match="outside"):
        settings.within_workspace("../escape")


def test_symlink_escape_is_rejected(tmp_path):
    target = tmp_path.parent / (tmp_path.name + "-outside")
    target.mkdir()
    try:
        (tmp_path / "link").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Session cannot create symlinks")
    with pytest.raises(ValueError, match="outside"):
        Settings(workspace=tmp_path.resolve()).within_workspace("link/file")


def test_mcu_requirements_draft_is_explicitly_blocked():
    data = read_spec(Path(__file__).parent / "fixtures/at89c51_led8_draft.json")
    assert validate_spec(data)["status"] == "BLOCKED"


@pytest.mark.parametrize("value", ["-1k", "0", "nan", "1k;start", "1e9", "9A"])
def test_invalid_quantities(value):
    with pytest.raises(ValueError):
        quantity(value, "resistance")


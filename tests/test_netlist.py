from pathlib import Path

from proteus_mcp.netlist import verify_netlist
from proteus_mcp.spec import read_spec

ROOT = Path(__file__).parents[1]
EXPORT = ROOT / "examples/led_dc_minimal.sdf"


def test_native_sdf_matches_values_pin_polarity_and_ground():
    result = verify_netlist(read_spec(ROOT / "examples/led_dc.json"), EXPORT)
    assert result["status"] == "PASS"
    assert ["BT1.negative", "D1.K", "GND.terminal"] in result["nets"]


def test_check_rejects_export_with_reversed_led_and_wrong_resistance(tmp_path):
    wrong = tmp_path / "reversed.SDF"
    wrong.write_text(EXPORT.read_text(encoding="utf-8").replace("D1,PS,A", "D1,PS,K")
                     .replace("R1,RES,1k,", "R1,RES,10k,"), encoding="utf-8")
    result = verify_netlist(read_spec(ROOT / "examples/led_dc.json"), wrong)
    assert result["status"] == "FAIL"
    assert "R1: unexpected resistance 10k" in result["errors"]
    assert "SDF endpoints do not match the specified nets" in result["errors"]

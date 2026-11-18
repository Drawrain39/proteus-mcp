"""Screen-independent logical placement and wire topology."""

from .spec import validate_spec


def plan_circuit(data: dict) -> dict:
    checked = validate_spec(data)
    if not checked["valid"]:
        return checked
    spec = checked["spec"]
    slots = {"BATTERY": (0, 2), "RES": (4, 0), "LED-RED": (8, 2), "GROUND": (0, 5)}
    placements = [
        {"ref": c["ref"], "device_query": c["device_query"],
         "grid": list(slots[c["device_query"].upper()]), "properties": c["properties"]}
        for c in spec["components"]
    ]
    steps = [{"op": "new_project", "basename": spec["output"]["project_basename"]}]
    for c in placements:
        steps.extend([{"op": "resolve_device_pins", "ref": c["ref"], "query": c["device_query"]},
                      {"op": "place_component", **c}])
    for net in spec["nets"]:
        steps.append({"op": "wire_net", "name": net["name"], "pins": net["pins"],
                      "routing": "orthogonal; resolve endpoints from fresh canvas observation"})
    steps.extend({"op": op} for op in (
        "verify_properties_and_connectivity", "save_project", "reopen_project", "simulate", "verify_behavior"))
    return {"status": "PASS", "mode": "logical_plan", "name": spec["name"],
            "placements": placements, "nets": spec["nets"], "steps": steps,
            "gui_execution_ready": False, "library_pins": "UNVERIFIED",
            "notes": ["Grid units are relative design units, not screen pixels.",
                      "Wire vertices and real device pin positions require live calibration."]}


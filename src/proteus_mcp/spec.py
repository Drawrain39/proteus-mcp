"""Logical electrical checks. Logical aliases are NOT verified Proteus pin IDs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Component(StrictModel):
    ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=40)
    device_query: str = Field(min_length=1, max_length=80)
    properties: dict[str, str] = Field(default_factory=dict)


class Net(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    pins: list[str] = Field(min_length=2, max_length=100)


class Simulation(StrictModel):
    type: Literal["interactive"] = "interactive"
    expected: str = Field(min_length=1)


class Output(StrictModel):
    project_basename: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=80)
    must_be_real_proteus_project: Literal[True] = True


class CircuitSpec(StrictModel):
    schema_version: Literal["0.1-draft", "0.1"]
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", max_length=80)
    purpose: str = ""
    components: list[Component] = Field(min_length=1, max_length=100)
    nets: list[Net] = Field(min_length=1, max_length=200)
    simulation: Simulation
    output: Output
    pin_resolution_policy: str = "Resolve logical aliases against the installed library before GUI execution."
    meta: dict[str, Any] = Field(default_factory=dict)


# Explicitly a LOGICAL catalog, never an assertion about a vendor library.
LOGICAL_PINS = {
    "BATTERY": ("positive", "negative"),
    "RES": ("1", "2"),
    "LED-RED": ("A", "K"),
    "GROUND": ("terminal",),
}
PROPERTY_KEYS = {"BATTERY": {"voltage"}, "RES": {"resistance"}, "LED-RED": set(), "GROUND": set()}


def quantity(value: str, kind: str) -> float:
    suffix = r"(?:V)?" if kind == "voltage" else r"(?:[oO]hm|Ω)?"
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kMmuµ]?)\s*" + suffix + r"\s*", value)
    if not match:
        raise ValueError(f"Invalid {kind}: {value!r}")
    scale = {"": 1, "k": 1e3, "M": 1e6, "m": 1e-3, "u": 1e-6, "µ": 1e-6}[match[2]]
    number = float(match[1]) * scale
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{kind} must be finite and positive")
    return number


def read_spec(path: Path) -> dict:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Spec exceeds 1 MB")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_spec(data: dict) -> dict:
    if not isinstance(data, dict):
        return {"status": "FAIL", "valid": False, "errors": ["CircuitSpec must be an object"], "warnings": []}
    if "components_required" in data:
        return {"status": "BLOCKED", "valid": False, "errors": [
            ("This MCU example is a requirements draft, not an executable CircuitSpec. "
             "Resolve components, power/reset/clock, pin metadata and Keil first.")
        ], "warnings": []}
    try:
        spec = CircuitSpec.model_validate(data)
    except ValidationError as exc:
        return {"status": "FAIL", "valid": False, "errors": [
            {"path": ".".join(map(str, err["loc"])), "message": err["msg"]}
            for err in exc.errors(include_input=False)
        ], "warnings": []}
    errors: list[str] = []
    warnings = ["Logical pin checks only; installed Proteus device pins remain UNVERIFIED."]
    refs = [c.ref for c in spec.components]
    devices = {c.ref: c.device_query.upper() for c in spec.components}
    if len(set(refs)) != len(refs):
        errors.append("Duplicate component references")
    if len({n.name for n in spec.nets}) != len(spec.nets):
        errors.append("Duplicate net names")
    if len({r.casefold() for r in refs}) != len(refs):
        errors.append("References must also be unique ignoring case")
    endpoint_nets: dict[str, str] = {}
    adjacency: dict[str, set[str]] = {ref: set() for ref in refs}
    for net in spec.nets:
        net_refs = []
        if len(set(net.pins)) != len(net.pins):
            errors.append(f"{net.name}: duplicate pin")
        for endpoint in net.pins:
            ref, sep, pin = endpoint.partition(".")
            if not sep or ref not in devices:
                errors.append(f"{net.name}: unknown endpoint {endpoint}")
                continue
            device = devices[ref]
            if device not in LOGICAL_PINS or pin not in LOGICAL_PINS[device]:
                errors.append(f"{endpoint}: unsupported logical pin for {device}")
            if endpoint in endpoint_nets:
                errors.append(f"{endpoint}: appears more than once (nets {endpoint_nets[endpoint]}, {net.name})")
            endpoint_nets[endpoint] = net.name
            net_refs.append(ref)
        if len(set(net_refs)) < 2:
            errors.append(f"{net.name}: net must connect at least two distinct components")
        for ref in net_refs:
            adjacency[ref].update(other for other in net_refs if other != ref)
    for comp in spec.components:
        device = devices[comp.ref]
        if device not in LOGICAL_PINS:
            errors.append(f"{comp.ref}: unsupported device {device}; add reviewed logical metadata first")
            continue
        for pin in LOGICAL_PINS[device]:
            if f"{comp.ref}.{pin}" not in endpoint_nets:
                errors.append(f"{comp.ref}.{pin}: dangling pin")
        extra = comp.properties.keys() - PROPERTY_KEYS[device]
        if extra:
            errors.append(f"{comp.ref}: unknown properties {sorted(extra)}")
        for key in PROPERTY_KEYS[device]:
            if key not in comp.properties:
                errors.append(f"{comp.ref}: missing {key}")
            else:
                try:
                    quantity(comp.properties[key], key)
                except ValueError as exc:
                    errors.append(f"{comp.ref}: {exc}")
        pins = [endpoint_nets.get(f"{comp.ref}.{p}") for p in LOGICAL_PINS[device]]
        if len(pins) == 2 and pins[0] is not None and pins[0] == pins[1]:
            errors.append(f"{comp.ref}: both pins shorted onto the same net")
    visited: set[str] = set()
    pending = refs[:1]
    while pending:
        ref = pending.pop()
        if ref not in visited:
            visited.add(ref)
            pending.extend(adjacency[ref] - visited)
    if visited != set(refs):
        errors.append(f"Disconnected components: {sorted(set(refs) - visited)}")
    # The sole executable logical template currently supported is this series LED circuit.
    counts = Counter(devices.values())
    if counts == Counter({"BATTERY": 1, "RES": 1, "LED-RED": 1, "GROUND": 1}):
        by_device = {device: ref for ref, device in devices.items()}
        bt, res, led, gnd = (by_device[d] for d in ("BATTERY", "RES", "LED-RED", "GROUND"))
        ground_net = endpoint_nets.get(f"{gnd}.terminal")
        if ground_net != endpoint_nets.get(f"{bt}.negative") or ground_net != endpoint_nets.get(f"{led}.K"):
            errors.append("LED cathode and battery negative must share the ground net")
        resistor_nets = {endpoint_nets.get(f"{res}.1"), endpoint_nets.get(f"{res}.2")}
        supply_net, anode_net = endpoint_nets.get(f"{bt}.positive"), endpoint_nets.get(f"{led}.A")
        if supply_net == anode_net or resistor_nets != {supply_net, anode_net}:
            errors.append("The resistor must be in series between battery positive and LED anode")
    else:
        errors.append("Only the single battery/resistor/red LED/ground series template is supported in 0.1")
    return {"status": "PASS" if not errors else "FAIL", "valid": not errors,
            "errors": errors, "warnings": warnings, "library_pins": "UNVERIFIED",
            "spec": spec.model_dump() if not errors else None}


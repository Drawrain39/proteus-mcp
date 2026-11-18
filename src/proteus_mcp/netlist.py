"""Compare the logical SDF 8.0 export from Proteus with a CircuitSpec."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from .evidence import sha256
from .spec import quantity, validate_spec


def _section(lines: list[str], name: str) -> tuple[int, list[list[str]]]:
    start = next((i for i, line in enumerate(lines) if line.startswith(name + ",")), None)
    if start is None:
        raise ValueError(f"Missing {name} section")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("*")), len(lines))
    rows = [[field.strip() for field in row] for row in csv.reader(lines[start:end], strict=True)]
    try:
        count = int(rows[0][1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Invalid {name} count") from exc
    if count < 0:
        raise ValueError(f"Invalid {name} count")
    return count, rows[1:]


def _read_nets(rows: list[list[str]], count: int, ground_ref: str) -> list[frozenset[str]]:
    nets = []
    index = 0
    while index < len(rows):
        header = rows[index]
        if len(header) < 2 or not header[1].isdigit():
            raise ValueError("Invalid SDF net header")
        length = int(header[1])
        terminals = rows[index + 1 : index + length + 1]
        if len(terminals) != length or length < 2:
            raise ValueError("Invalid SDF endpoint count")
        endpoints = set()
        for row in terminals:
            if len(row) == 3 and row[1] == "PS":
                pin = {"+": "positive", "-": "negative"}.get(row[2], row[2])
                endpoints.add(f"{row[0]}.{pin}")
            elif row == ["GND", "PR"] and header[0] == "GND" and "CLASS=POWER" in header[2:]:
                endpoints.add(f"{ground_ref}.terminal")
            else:
                raise ValueError(f"Unsupported SDF endpoint: {row}")
        if len(endpoints) != length:
            raise ValueError("Duplicate endpoint in SDF net")
        nets.append(frozenset(endpoints))
        index += length + 1
    if len(nets) != count:
        raise ValueError("SDF net count mismatch")
    return nets


def verify_netlist(data: dict, path: Path) -> dict:
    validated = validate_spec(data)
    if not validated["valid"]:
        return validated
    spec = validated["spec"]
    errors = []
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError("Netlist exceeds 1 MB")
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if not lines or lines[0] != "ISIS SCHEMATIC DESCRIPTION FORMAT 8.0":
            raise ValueError("Expected a Proteus logical SDF 8.0 export")
        part_count, parts = _section(lines, "*PARTLIST")
        components = {c["ref"]: c for c in spec["components"] if c["device_query"].upper() != "GROUND"}
        part_by_ref = {row[0]: row for row in parts if len(row) >= 3}
        if len(part_by_ref) != part_count or len(parts) != part_count or set(part_by_ref) != set(components):
            errors.append("SDF part list differs from CircuitSpec")
        for ref in sorted(components.keys() & part_by_ref.keys()):
            expected = components[ref]
            device, value = part_by_ref[ref][1:3]
            if device.upper() != expected["device_query"].upper():
                errors.append(f"{ref}: unexpected device {device}")
            for key, expected_value in expected["properties"].items():
                if quantity(value, key) != quantity(expected_value, key):
                    errors.append(f"{ref}: unexpected {key} {value}")
        ground_ref = next(c["ref"] for c in spec["components"] if c["device_query"].upper() == "GROUND")
        net_count, rows = _section(lines, "*NETLIST")
        actual = _read_nets(rows, net_count, ground_ref)
        expected_nets = [frozenset(net["pins"]) for net in spec["nets"]]
        if Counter(actual) != Counter(expected_nets):
            errors.append("SDF endpoints do not match the specified nets")
        return {"status": "FAIL" if errors else "PASS", "errors": errors,
                "netlist": str(path), "sha256": sha256(path),
                "nets": [sorted(net) for net in actual]}
    except (OSError, ValueError, csv.Error) as exc:
        return {"status": "FAIL", "errors": [str(exc)], "netlist": str(path)}

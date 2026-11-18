from __future__ import annotations

from .config import Settings
from .doctor import discover_proteus
from .evidence import EvidenceStore
from .planner import plan_circuit
from .winread import unique_target


def run_circuit(settings: Settings, spec: dict, output: str, backend: str) -> dict:
    result, _ = prepare_run(settings, spec, output, backend)
    return result


def prepare_run(settings: Settings, spec: dict, output: str, backend: str) -> tuple[dict, EvidenceStore | None]:
    plan = plan_circuit(spec)
    if plan["status"] != "PASS":
        return plan, None
    if backend not in {"mock", "windows"}:
        raise ValueError("backend must be mock or windows")
    store = EvidenceStore(settings.within_workspace(output))
    store.write("spec.json", spec)
    store.write("plan.json", plan)
    if backend == "mock":
        for step in plan["steps"]:
            store.event("mock_step", step=step)
        result = {"status": "PASS", "backend": "mock", "simulation": "UNVERIFIED",
                  "project": None, "evidence_directory": str(store.path),
                  "note": "Logical action simulation only. No GUI inputs or Proteus project were created."}
    else:
        result = prepare_windows(settings, plan, store)
        if result["status"] == "READY":
            store.write("request.json", result)
            return result, store
    store.write("result.json", result)
    return result, store


def prepare_windows(settings: Settings, plan: dict, store: EvidenceStore) -> dict:
    result = {"backend": "windows", "simulation": "UNVERIFIED", "project": None,
              "evidence_directory": str(store.path)}
    try:
        if not settings.gui_enabled:
            raise ValueError("GUI is disabled in config.local.json")
        exe = discover_proteus(settings)
        if exe is None:
            raise ValueError("Proteus executable not found")
        if not settings.windows_python or not settings.windows_python.is_file():
            raise ValueError("Windows-MCP interpreter is unavailable")
        target = unique_target(exe, require_foreground=True)
    except (OSError, ValueError) as exc:
        return {**result, "status": "BLOCKED", "reason": str(exc)}

    basename = plan["steps"][0]["basename"]
    return {**result, "status": "READY", "target": target,
            "project_destination": str(store.path / f"{basename}.pdsprj"), "steps": plan["steps"],
            "instructions": "Use proteus_observe and proteus_action to execute each step against fresh "
                            "evidence. Create a new project at project_destination. Verify the reopened "
                            "project and simulation, then record observations with circuit_record."}


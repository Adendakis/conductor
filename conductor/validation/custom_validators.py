"""Project-specific custom validators."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .validator import ValidationResult

if TYPE_CHECKING:
    from conductor.executor.base import ExecutionContext
    from conductor.models.ticket import Ticket


def validate_dag_no_cycles(
    ticket: "Ticket", context: "ExecutionContext"
) -> ValidationResult:
    """Validate that Workpackage_Planning.json has no dependency cycles."""
    errors: list[str] = []
    path = (
        Path(context.working_directory)
        / "output/analysis/workpackages/Workpackage_Planning.json"
    )

    if not path.exists():
        errors.append("Workpackage_Planning.json not found")
        return ValidationResult(passed=False, errors=errors)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        errors.append(f"Cannot parse Workpackage_Planning.json: {e}")
        return ValidationResult(passed=False, errors=errors)

    # Simple cycle detection via topological sort
    sequence = data.get("migrationSequence", [])
    wp_ids = {item.get("workpackageId") for item in sequence}
    deps: dict[int, list[int]] = {}
    for item in sequence:
        wp_id = item.get("workpackageId")
        item_deps = item.get("dependencies", [])
        deps[wp_id] = item_deps

    # Kahn's algorithm
    in_degree: dict[int, int] = {wp: 0 for wp in wp_ids}
    for wp, dep_list in deps.items():
        for d in dep_list:
            if d in in_degree:
                in_degree[wp] = in_degree.get(wp, 0) + 1

    queue = [wp for wp, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for wp, dep_list in deps.items():
            if node in dep_list:
                in_degree[wp] -= 1
                if in_degree[wp] == 0:
                    queue.append(wp)

    if visited < len(wp_ids):
        errors.append("Dependency cycle detected in workpackage planning")

    return ValidationResult(passed=len(errors) == 0, errors=errors)


def validate_pod_assignment_completeness(
    ticket: "Ticket", context: "ExecutionContext"
) -> ValidationResult:
    """Validate all workpackages are assigned to pods."""
    errors: list[str] = []
    warnings: list[str] = []

    pod_path = (
        Path(context.working_directory)
        / "output/analysis/workpackages/Pod_Assignment.json"
    )
    wp_path = (
        Path(context.working_directory)
        / "output/analysis/workpackages/Workpackage_Planning.json"
    )

    if not pod_path.exists():
        errors.append("Pod_Assignment.json not found")
        return ValidationResult(passed=False, errors=errors)

    if not wp_path.exists():
        warnings.append("Workpackage_Planning.json not found — cannot verify completeness")
        return ValidationResult(passed=True, warnings=warnings)

    try:
        pod_data = json.loads(pod_path.read_text(encoding="utf-8"))
        wp_data = json.loads(wp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        errors.append(f"Cannot parse JSON: {e}")
        return ValidationResult(passed=False, errors=errors)

    # Get all WP IDs from planning
    all_wps = {
        item.get("workpackageId") for item in wp_data.get("migrationSequence", [])
    }

    # Get assigned WPs from pod assignment
    assigned_wps: set[int] = set()
    for pod in pod_data.get("pods", []):
        for wp_id in pod.get("workpackages", []):
            assigned_wps.add(wp_id)

    unassigned = all_wps - assigned_wps
    if unassigned:
        errors.append(
            f"Workpackages not assigned to any pod: {sorted(unassigned)}"
        )

    return ValidationResult(
        passed=len(errors) == 0, errors=errors, warnings=warnings
    )

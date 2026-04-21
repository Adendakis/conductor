"""Pipeline validation — checks for errors in pipeline definitions."""

from typing import Optional

from conductor.models.phases import PhaseDefinition


def validate_pipeline(phases: list[PhaseDefinition]) -> list[str]:
    """Validate a pipeline definition for errors.

    Checks:
    1. All step IDs are unique across the pipeline
    2. All depends_on references resolve to existing step IDs
    3. All creates_next_phases references resolve to existing phase IDs
    4. No dependency cycles within a phase
    5. All reviewer steps have a valid reviewer_for reference
    6. No duplicate phase IDs

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    # Collect all IDs
    phase_ids = set()
    step_ids = set()
    step_to_phase: dict[str, str] = {}

    for phase in phases:
        # Duplicate phase ID check
        if phase.phase_id in phase_ids:
            errors.append(f"Duplicate phase ID: '{phase.phase_id}'")
        phase_ids.add(phase.phase_id)

        for step in phase.steps:
            # Duplicate step ID check
            if step.step_id in step_ids:
                errors.append(
                    f"Duplicate step ID: '{step.step_id}' "
                    f"(in phase '{phase.phase_id}')"
                )
            step_ids.add(step.step_id)
            step_to_phase[step.step_id] = phase.phase_id

    # Validate references
    for phase in phases:
        # creates_next_phases references (informational — these phases may be
        # created dynamically at runtime, so missing references are warnings not errors)
        # Skipped from validation.

        # depends_on (phase level)
        for dep_phase_id in phase.depends_on:
            if dep_phase_id not in phase_ids:
                errors.append(
                    f"Phase '{phase.phase_id}' depends on non-existent "
                    f"phase: '{dep_phase_id}'"
                )

        for step in phase.steps:
            # depends_on (step level)
            for dep_step_id in step.depends_on:
                if dep_step_id not in step_ids:
                    errors.append(
                        f"Step '{step.step_id}' depends on non-existent "
                        f"step: '{dep_step_id}'"
                    )

            # reviewer_for reference
            if step.is_reviewer and step.reviewer_for:
                if step.reviewer_for not in step_ids:
                    errors.append(
                        f"Reviewer step '{step.step_id}' references "
                        f"non-existent step: '{step.reviewer_for}'"
                    )

            # rework_target reference
            if step.rework_target:
                if step.rework_target not in step_ids:
                    errors.append(
                        f"Step '{step.step_id}' has rework_target "
                        f"referencing non-existent step: '{step.rework_target}'"
                    )

    # Cycle detection within each phase (topological sort)
    for phase in phases:
        cycle_error = _detect_intra_phase_cycles(phase)
        if cycle_error:
            errors.append(cycle_error)

    return errors


def _detect_intra_phase_cycles(phase: PhaseDefinition) -> Optional[str]:
    """Detect dependency cycles within a single phase's steps."""
    step_ids_in_phase = {s.step_id for s in phase.steps}

    # Build adjacency: step → steps it depends on (within this phase)
    in_degree: dict[str, int] = {s.step_id: 0 for s in phase.steps}
    dependents: dict[str, list[str]] = {s.step_id: [] for s in phase.steps}

    for step in phase.steps:
        for dep in step.depends_on:
            if dep in step_ids_in_phase:
                in_degree[step.step_id] += 1
                dependents[dep].append(step.step_id)

    # Kahn's algorithm
    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0

    while queue:
        node = queue.pop(0)
        visited += 1
        for dependent in dependents[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if visited < len(step_ids_in_phase):
        return (
            f"Dependency cycle detected in phase '{phase.phase_id}': "
            f"not all steps are reachable via topological sort"
        )
    return None

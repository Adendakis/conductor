"""Board initializer — creates initial tickets from pipeline definition."""

import logging
from pathlib import Path
from typing import Optional

from conductor.git.manager import GitManager
from conductor.models.enums import TicketStatus, TicketType
from conductor.models.phases import PhaseDefinition
from conductor.models.ticket import Ticket, TicketMetadata
from conductor.pipeline.builder import build_pipeline
from conductor.tracker.backend import TrackerBackend
from conductor.watcher.ticket_creator import DynamicTicketCreator

logger = logging.getLogger(__name__)


def initialize_board(
    tracker: TrackerBackend,
    git: GitManager,
    pipeline_mode: str = "full",
    working_directory: Path = Path("."),
    all_phases: bool = False,
    workpackages_file: Optional[Path] = None,
) -> list[str]:
    """Create initial tickets for the migration.

    pipeline_mode can be:
    - "full", "minimal" — built-in Python pipelines
    - "yaml:/path/to/pipeline.yaml" — load from YAML file

    Returns list of created ticket IDs.
    """
    if pipeline_mode.startswith("yaml:"):
        from conductor.pipeline.loader import load_pipeline_yaml
        yaml_path = Path(pipeline_mode[5:])
        pipeline = load_pipeline_yaml(yaml_path)
    else:
        pipeline = build_pipeline(pipeline_mode)

    created_ids: list[str] = []
    # Track phase_id → list of ticket IDs created for that phase
    phase_ticket_map: dict[str, list[str]] = {}

    if all_phases or workpackages_file:
        # Create all tickets
        for phase in pipeline:
            if phase.execution_scope == "global" or all_phases:
                ids = _create_phase_tickets(phase, tracker)
                phase_ticket_map[phase.phase_id] = ids
                created_ids.extend(ids)

        if workpackages_file:
            per_wp_phases = [
                p for p in pipeline if p.execution_scope == "per_workpackage"
            ]
            if per_wp_phases:
                creator = DynamicTicketCreator(working_directory=working_directory)
                dummy_ticket = Ticket(title="init")
                ids = creator.create_scoped_tickets(
                    dummy_ticket, per_wp_phases, tracker
                )
                created_ids.extend(ids)

        # Wire inter-phase dependencies for all created phases
        _wire_phase_dependencies(pipeline, phase_ticket_map, tracker)

    else:
        # Progressive creation — only create initial phases
        # Build dependency map: which phases are created by other phases
        created_by: dict[str, str] = {}
        for phase in pipeline:
            for next_id in phase.creates_next_phases:
                created_by[next_id] = phase.phase_id

        # Also build explicit depends_on map
        has_dependency: set[str] = set()
        for phase in pipeline:
            for dep_id in phase.depends_on:
                has_dependency.add(phase.phase_id)

        # Initial phases: no explicit depends_on AND not created by another phase
        initial_phases = [
            p for p in pipeline
            if not p.depends_on
            and p.phase_id not in created_by
            and p.execution_scope == "global"
        ]

        # Also include phases that depend on initial phases (if depends_on is set)
        # We create all phases that can be resolved at init time
        resolvable = {p.phase_id for p in initial_phases}
        changed = True
        while changed:
            changed = False
            for phase in pipeline:
                if phase.phase_id in resolvable:
                    continue
                if phase.phase_id in created_by:
                    continue  # Created dynamically, not at init
                if phase.execution_scope != "global":
                    continue
                # Check if all depends_on are resolvable
                if phase.depends_on and all(
                    d in resolvable for d in phase.depends_on
                ):
                    resolvable.add(phase.phase_id)
                    changed = True

        # Create tickets for all resolvable phases
        phases_to_create = [
            p for p in pipeline if p.phase_id in resolvable
        ]
        for phase in phases_to_create:
            ids = _create_phase_tickets(phase, tracker)
            phase_ticket_map[phase.phase_id] = ids
            created_ids.extend(ids)

        # Wire inter-phase dependencies
        _wire_phase_dependencies(pipeline, phase_ticket_map, tracker)

    # Git tag
    git.tag("conductor/initialized", "Board initialized")

    print(f"✓ Board initialized with {len(created_ids)} tickets")
    return created_ids


def _wire_phase_dependencies(
    pipeline: list[PhaseDefinition],
    phase_ticket_map: dict[str, list[str]],
    tracker: TrackerBackend,
) -> None:
    """Wire phase-level depends_on into ticket blocked_by fields.

    For each phase with depends_on, the entry-point tickets (those with
    no intra-phase blockers) are blocked by the last ticket of each
    dependency phase.

    Also handles implicit dependencies from creates_next_phases.
    """
    # Build implicit depends_on from creates_next_phases
    implicit_deps: dict[str, list[str]] = {}  # phase_id → [dep_phase_ids]
    for phase in pipeline:
        for next_id in phase.creates_next_phases:
            if next_id not in implicit_deps:
                implicit_deps[next_id] = []
            implicit_deps[next_id].append(phase.phase_id)

    for phase in pipeline:
        # Combine explicit and implicit dependencies
        all_deps = list(phase.depends_on)
        for imp_dep in implicit_deps.get(phase.phase_id, []):
            if imp_dep not in all_deps:
                all_deps.append(imp_dep)

        if not all_deps:
            continue

        phase_tickets = phase_ticket_map.get(phase.phase_id, [])
        if not phase_tickets:
            continue

        # Collect the last ticket from each dependency phase
        blocker_ids: list[str] = []
        for dep_phase_id in all_deps:
            dep_tickets = phase_ticket_map.get(dep_phase_id, [])
            if dep_tickets:
                blocker_ids.append(dep_tickets[-1])  # last ticket in dep phase

        if not blocker_ids:
            continue

        # Find entry-point tickets in this phase (no intra-phase blockers)
        for ticket_id in phase_tickets:
            ticket = tracker.get_ticket(ticket_id)
            if not ticket.blocked_by:
                # This is a phase entry point — add inter-phase blockers
                new_blocked_by = list(blocker_ids)
                # Update the ticket
                tracker.update_status(ticket_id, TicketStatus.BACKLOG)
                # Create dependency links
                for blocker_id in new_blocked_by:
                    tracker.create_link(blocker_id, ticket_id, "blocks")

                logger.info(
                    f"Wired phase dependency: {ticket_id} blocked by {new_blocked_by}"
                )


def _create_phase_tickets(
    phase: PhaseDefinition, tracker: TrackerBackend
) -> list[str]:
    """Create tickets for a single phase."""
    created_ids: list[str] = []
    step_id_to_ticket_id: dict[str, str] = {}

    for step in phase.steps:
        deliverable_paths = [d.output_path for d in step.expected_deliverables]

        if step.is_reviewer:
            ticket_type = TicketType.REVIEWER_STEP
        else:
            ticket_type = TicketType.TASK

        description = (
            step.description.strip()
            if step.description
            else _build_description(step, phase)
        )

        metadata = TicketMetadata(
            phase=phase.phase_id,
            step=step.step_id,
            agent_name=step.agent_name,
            prompt_file=step.prompt_file,
            deliverable_paths=deliverable_paths,
            hitl_required=step.hitl_after,
            rework_target_step=step.rework_target,
            input_dependencies=step.input_dependencies,
            max_iterations=step.max_review_iterations,
        )

        # Resolve intra-phase dependencies
        blocked_by: list[str] = []
        for dep_step_id in step.depends_on:
            if dep_step_id in step_id_to_ticket_id:
                blocked_by.append(step_id_to_ticket_id[dep_step_id])

        if blocked_by:
            status = TicketStatus.BACKLOG
        else:
            status = TicketStatus.READY

        ticket = Ticket(
            title=step.display_name,
            description=description,
            status=status,
            ticket_type=ticket_type,
            metadata=metadata,
            blocked_by=blocked_by,
        )

        ticket_id = tracker.create_ticket(ticket)
        step_id_to_ticket_id[step.step_id] = ticket_id
        created_ids.append(ticket_id)
        logger.info(f"Created ticket {ticket_id}: {step.display_name}")

    return created_ids


def _build_description(step, phase) -> str:
    """Build ticket description."""
    parts = [
        f"## Task: {step.display_name}",
        "",
        f"**Phase**: {phase.phase_id}",
        f"**Step**: {step.step_id}",
        f"**Agent**: {step.agent_name}",
        f"**Prompt File**: {step.prompt_file}",
        "",
        "### Expected Deliverables",
    ]
    for d in step.expected_deliverables:
        parts.append(f"- {d.output_path}")

    if step.input_dependencies:
        parts.extend(["", "### Input Dependencies"])
        for dep in step.input_dependencies:
            parts.append(f"- {dep}")

    parts.extend([
        "",
        "### HITL Configuration",
        f"- Review required: {'yes' if step.hitl_after else 'no'}",
        f"- Max iterations: {step.max_review_iterations}",
    ])

    return "\n".join(parts)

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

    By default, only creates tickets for Phase 1 (knowable at init time).
    If all_phases=True, creates all global-scope tickets.
    If workpackages_file is provided, also creates per-WP tickets.

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

    if all_phases or workpackages_file:
        # Create all tickets
        creator = DynamicTicketCreator(working_directory=working_directory)
        for phase in pipeline:
            if phase.execution_scope == "global" or all_phases:
                ids = _create_phase_tickets(phase, tracker)
                created_ids.extend(ids)

        if workpackages_file:
            # Create per-WP tickets using provided workpackage data
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
    else:
        # Only create initial phases (progressive creation)
        # Infer depends_on from creates_next_phases if not explicitly set
        created_by: dict[str, str] = {}  # phase_id → parent phase_id
        for phase in pipeline:
            for next_id in phase.creates_next_phases:
                created_by[next_id] = phase.phase_id

        initial_phases = [
            p for p in pipeline
            if not p.depends_on and p.phase_id not in created_by
        ]
        for phase in initial_phases:
            if phase.execution_scope == "global":
                ids = _create_phase_tickets(phase, tracker)
                created_ids.extend(ids)

    # Git tag
    git.tag("conductor/initialized", "Board initialized")

    print(f"✓ Board initialized with {len(created_ids)} tickets")
    return created_ids


def _create_phase_tickets(
    phase: PhaseDefinition, tracker: TrackerBackend
) -> list[str]:
    """Create tickets for a single phase."""
    created_ids: list[str] = []
    step_id_to_ticket_id: dict[str, str] = {}

    for step in phase.steps:
        # Resolve deliverable paths
        deliverable_paths = [d.output_path for d in step.expected_deliverables]

        # Determine ticket type
        if step.is_reviewer:
            ticket_type = TicketType.REVIEWER_STEP
        else:
            ticket_type = TicketType.TASK

        # Build description
        description = _build_description(step, phase)

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

        # Set initial status
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

"""Dynamic ticket creator — creates scoped tickets when milestones complete."""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from conductor.models.enums import TicketStatus, TicketType
from conductor.models.phases import PhaseDefinition, StepDefinition
from conductor.models.ticket import Ticket, TicketMetadata
from conductor.tracker.backend import TrackerBackend

if TYPE_CHECKING:
    from conductor.git.worktree_manager import WorktreeManager
    from conductor.watcher.scope_discovery import ScopeDiscovery

logger = logging.getLogger(__name__)


class DynamicTicketCreator:
    """Creates scoped tickets when milestone phases complete.

    Triggered by the watcher when a phase with `creates_next_phases` completes.
    Uses a ScopeDiscovery instance to discover scope units (workpackages, pods, etc.).
    """

    def __init__(
        self,
        working_directory: Path = Path("."),
        scope_discovery: "ScopeDiscovery | None" = None,
    ):
        self.working_directory = working_directory
        if scope_discovery is None:
            from conductor.watcher.scope_discovery import DefaultScopeDiscovery
            self._scope_discovery = DefaultScopeDiscovery()
        else:
            self._scope_discovery = scope_discovery

    def create_scoped_tickets(
        self,
        completed_ticket: Ticket,
        next_phases: list[PhaseDefinition],
        tracker: TrackerBackend,
        worktree_manager: "WorktreeManager | None" = None,
    ) -> list[str]:
        """Create tickets for the next phases based on scope.

        Args:
            completed_ticket: The ticket that triggered phase completion
            next_phases: Phase definitions to create tickets for
            tracker: Ticket tracker backend
            worktree_manager: Optional worktree manager for pod-scoped phases

        Returns list of created ticket IDs.
        """
        created_ids: list[str] = []

        for phase in next_phases:
            if phase.execution_scope == "per_workpackage":
                if worktree_manager:
                    # Pod-scoped: create tickets per pod, WPs sequential within pod
                    ids = self._create_pod_scoped_tickets(
                        phase, tracker, worktree_manager
                    )
                    created_ids.extend(ids)
                else:
                    # No pods: create tickets per WP (original behavior)
                    workpackages = self._scope_discovery.discover_workpackages(
                        self.working_directory
                    )
                    for wp_id in workpackages:
                        ids = self._create_phase_tickets(
                            phase, tracker, scope_id=wp_id, scope_type="workpackage"
                        )
                        created_ids.extend(ids)

            elif phase.execution_scope == "per_domain":
                domains = self._scope_discovery.discover_domains(
                    self.working_directory
                )
                for domain in domains:
                    ids = self._create_phase_tickets(
                        phase, tracker, scope_id=domain, scope_type="domain"
                    )
                    created_ids.extend(ids)

            elif phase.execution_scope == "per_pod":
                pods = self._scope_discovery.discover_pods(
                    self.working_directory
                )
                for pod_id in pods:
                    ids = self._create_phase_tickets(
                        phase, tracker, scope_id=pod_id, scope_type="pod"
                    )
                    created_ids.extend(ids)

            else:  # global
                ids = self._create_phase_tickets(
                    phase, tracker, scope_id=None, scope_type="global"
                )
                created_ids.extend(ids)

        return created_ids

    def _create_phase_tickets(
        self,
        phase: PhaseDefinition,
        tracker: TrackerBackend,
        scope_id: Optional[str],
        scope_type: str,
    ) -> list[str]:
        """Create tickets for each step in a phase."""
        created_ids: list[str] = []
        step_id_to_ticket_id: dict[str, str] = {}

        for step in phase.steps:
            # Skip steps that don't match scope conditions
            if step.workpackage_type and scope_id:
                wp_type = self._scope_discovery.get_workpackage_type(
                    scope_id, self.working_directory
                )
                if wp_type and wp_type != step.workpackage_type:
                    continue

            # Build ticket
            ticket = self._build_ticket(step, phase, scope_id, scope_type)

            # Resolve intra-phase dependencies to ticket IDs
            blocked_by: list[str] = []
            for dep_step_id in step.depends_on:
                if dep_step_id in step_id_to_ticket_id:
                    blocked_by.append(step_id_to_ticket_id[dep_step_id])
            ticket.blocked_by = blocked_by

            # Set initial status
            if blocked_by:
                ticket.status = TicketStatus.BACKLOG
            else:
                ticket.status = TicketStatus.READY

            ticket_id = tracker.create_ticket(ticket)
            step_id_to_ticket_id[step.step_id] = ticket_id
            created_ids.append(ticket_id)

            logger.info(f"Created ticket {ticket_id}: {ticket.title}")

        return created_ids

    def _create_pod_scoped_tickets(
        self,
        phase: PhaseDefinition,
        tracker: TrackerBackend,
        worktree_manager: "WorktreeManager",
    ) -> list[str]:
        """Create per-WP tickets grouped by pod, sequential within each pod.

        For each pod:
        - WPs are processed in the order listed in Pod_Assignment.json
        - Each WP's first step is blocked by the previous WP's last step
        - working_directory is set to the pod's worktree path
        - metadata.pod is set to the pod ID
        """
        created_ids: list[str] = []

        for pod_id in worktree_manager.get_all_pod_ids():
            worktree_path = worktree_manager.get_worktree_path(pod_id)
            if not worktree_path:
                logger.warning(f"No worktree for pod {pod_id}, skipping")
                continue

            workpackages = worktree_manager.get_pod_workpackages(pod_id)
            prev_wp_last_ticket_id: str | None = None

            for wp_id in workpackages:
                # Create tickets for this WP's steps
                step_id_to_ticket_id: dict[str, str] = {}
                wp_ticket_ids: list[str] = []

                for step in phase.steps:
                    # Skip steps that don't match WP type
                    if step.workpackage_type and wp_id:
                        wp_type = self._scope_discovery.get_workpackage_type(
                            wp_id, self.working_directory
                        )
                        if wp_type and wp_type != step.workpackage_type:
                            continue

                    ticket = self._build_ticket(
                        step, phase, scope_id=wp_id, scope_type="workpackage"
                    )

                    # Set pod and worktree working directory
                    ticket.metadata.pod = pod_id
                    ticket.metadata.working_directory = str(worktree_path)

                    # Resolve intra-phase step dependencies
                    blocked_by: list[str] = []
                    for dep_step_id in step.depends_on:
                        if dep_step_id in step_id_to_ticket_id:
                            blocked_by.append(step_id_to_ticket_id[dep_step_id])

                    # If this is the first step and there's a previous WP,
                    # block on the previous WP's last ticket (sequential ordering)
                    if not blocked_by and prev_wp_last_ticket_id:
                        blocked_by.append(prev_wp_last_ticket_id)

                    ticket.blocked_by = blocked_by
                    ticket.status = (
                        TicketStatus.BACKLOG if blocked_by else TicketStatus.READY
                    )

                    ticket_id = tracker.create_ticket(ticket)
                    step_id_to_ticket_id[step.step_id] = ticket_id
                    wp_ticket_ids.append(ticket_id)
                    created_ids.append(ticket_id)

                    logger.info(
                        f"Created ticket {ticket_id}: {ticket.title} "
                        f"(pod={pod_id}, wp={wp_id})"
                    )

                # Track the last ticket of this WP for sequential chaining
                if wp_ticket_ids:
                    prev_wp_last_ticket_id = wp_ticket_ids[-1]

        return created_ids

    def _build_ticket(
        self,
        step: StepDefinition,
        phase: PhaseDefinition,
        scope_id: Optional[str],
        scope_type: str,
    ) -> Ticket:
        """Build a Ticket from a StepDefinition."""
        # Resolve deliverable path templates
        deliverable_paths = []
        for d in step.expected_deliverables:
            path = self._resolve_path_template(d.output_path, scope_id)
            deliverable_paths.append(path)

        # Resolve input dependency templates
        input_deps = [
            self._resolve_path_template(dep, scope_id)
            for dep in step.input_dependencies
        ]

        # Build title with scope
        scope_label = f" {scope_id}" if scope_id else ""
        title = f"{step.display_name}{scope_label}"

        # Determine ticket type
        if step.is_reviewer:
            ticket_type = TicketType.REVIEWER_STEP
        else:
            ticket_type = TicketType.TASK

        # Build description
        description = self._build_description(step, phase, scope_id)

        metadata = TicketMetadata(
            phase=phase.phase_id,
            step=step.step_id,
            workpackage=scope_id if scope_type == "workpackage" else None,
            pod=scope_id if scope_type == "pod" else None,
            agent_name=step.agent_name,
            prompt_file=step.prompt_file,
            deliverable_paths=deliverable_paths,
            hitl_required=step.hitl_after,
            rework_target_step=step.rework_target,
            input_dependencies=input_deps,
            max_iterations=step.max_review_iterations,
        )

        return Ticket(
            title=title,
            description=description,
            ticket_type=ticket_type,
            metadata=metadata,
        )

    def _build_description(
        self,
        step: StepDefinition,
        phase: PhaseDefinition,
        scope_id: Optional[str],
    ) -> str:
        """Build ticket description from step definition."""
        parts = [
            f"## Task: {step.display_name}",
            "",
            f"**Phase**: {phase.phase_id}",
            f"**Step**: {step.step_id}",
        ]
        if scope_id:
            parts.append(f"**Scope**: {scope_id}")
        parts.extend([
            f"**Agent**: {step.agent_name}",
            f"**Prompt File**: {step.prompt_file}",
            "",
            "### Expected Deliverables",
        ])
        for d in step.expected_deliverables:
            path = self._resolve_path_template(d.output_path, scope_id)
            parts.append(f"- {path}")

        if step.input_dependencies:
            parts.extend(["", "### Input Dependencies"])
            for dep in step.input_dependencies:
                parts.append(f"- {self._resolve_path_template(dep, scope_id)}")

        parts.extend([
            "",
            "### HITL Configuration",
            f"- Review required: {'yes' if step.hitl_after else 'no'}",
            f"- Max iterations: {step.max_review_iterations}",
        ])

        return "\n".join(parts)

    def _resolve_path_template(
        self, path: str, scope_id: Optional[str]
    ) -> str:
        """Replace {wp_id}, {pod_id}, {domain_name} in path templates."""
        if not scope_id:
            return path
        result = path.replace("{wp_id}", scope_id or "")
        result = result.replace("{pod_id}", scope_id or "")
        result = result.replace("{domain_name}", scope_id or "")
        result = result.replace("{workpackage_id}", scope_id or "")
        return result

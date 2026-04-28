"""Event watcher — stateless event loop reacting to tracker status changes."""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from conductor.executor.base import ExecutionContext, ExecutionResult
from conductor.executor.registry import AgentRegistry
from conductor.executor.reviewer_executor import ReviewerExecutor
from conductor.git.manager import GitManager
from conductor.models.config import ProjectConfig, WatcherConfig
from conductor.models.enums import TicketStatus, TicketType
from conductor.models.ticket import Ticket, TicketMetadata
from conductor.providers.base import LLMProvider
from conductor.tracker.backend import TrackerBackend
from conductor.validation.validator import DeliverableValidator, ValidationResult

logger = logging.getLogger(__name__)


class EventWatcher:
    """Stateless event loop that reacts to tracker status changes."""

    def __init__(
        self,
        tracker: TrackerBackend,
        registry: AgentRegistry,
        git: GitManager,
        config: WatcherConfig,
        project_config: ProjectConfig,
        llm_provider: Optional[LLMProvider] = None,
    ):
        self.tracker = tracker
        self.registry = registry
        self.git = git
        self.config = config
        self.project_config = project_config
        self.llm_provider = llm_provider
        self.validator = DeliverableValidator(
            custom_validators=registry.get_custom_validators()
        )
        self.last_poll = datetime.now(timezone.utc).isoformat()

    def run(self) -> None:
        """Main event loop. Runs forever until interrupted."""
        print(
            f"▶ Conductor Event Watcher started. "
            f"Polling every {self.config.poll_interval_seconds}s..."
        )

        while True:
            try:
                self.poll_and_react()
                time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("\n⏹ Watcher stopped.")
                break
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")
                time.sleep(self.config.poll_interval_seconds)

    def poll_and_react(self) -> None:
        """Single poll cycle: check for changes, react to each."""
        # 0. Detect stale tickets (IN_PROGRESS too long without update)
        self._handle_stale_tickets()

        # 1. Pick up READY tickets
        ready_tickets = self.tracker.get_tickets_by_status(TicketStatus.READY)
        for ticket in ready_tickets:
            self.handle_ready(ticket)

        # 2. Handle APPROVED tickets
        approved_tickets = self.tracker.get_tickets_by_status(TicketStatus.APPROVED)
        for ticket in approved_tickets:
            self.handle_approved(ticket)

        # 3. Handle REJECTED tickets
        rejected_tickets = self.tracker.get_tickets_by_status(TicketStatus.REJECTED)
        for ticket in rejected_tickets:
            self.handle_rejected(ticket)

    def handle_ready(self, ticket: Ticket) -> None:
        """A ticket is ready for agent execution."""
        from conductor.watcher.dependency_resolver import all_blockers_resolved

        # Verify all blockers are actually done
        if not all_blockers_resolved(ticket, self.tracker):
            return

        # Transition to IN_PROGRESS
        self.tracker.update_status(ticket.id, TicketStatus.IN_PROGRESS)
        print(f"▶ Ticket {ticket.id} ({ticket.title}) → IN_PROGRESS")

        # Git tag: started
        if self.config.git_tag_on_transitions:
            tag = f"conductor/{ticket.id}/started"
            self.git.tag(tag)
            ticket.metadata.git_tag_started = tag
            self.tracker.update_metadata(ticket.id, ticket.metadata)

        # Resolve executor
        agent_name = ticket.metadata.agent_name
        if agent_name not in self.registry:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(
                ticket.id, f"No executor registered for agent: {agent_name}"
            )
            return

        executor = self.registry.get(agent_name)

        # Build execution context
        context = self._build_context(ticket)

        # Execute
        try:
            result = executor.execute(ticket, context)
        except Exception as e:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(ticket.id, f"Execution error: {e}")
            print(f"  ✗ Agent failed with exception: {e}")
            return

        if result.success:
            self._handle_success(ticket, result, executor, context)
        else:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(
                ticket.id, f"Agent failed: {result.error or result.summary}"
            )
            print(f"  ✗ Agent failed: {result.error or result.summary}")

    def _handle_success(
        self,
        ticket: Ticket,
        result: ExecutionResult,
        executor,
        context: ExecutionContext,
    ) -> None:
        """Handle successful agent execution."""
        # Validate deliverables
        validation = self.validator.validate(ticket, context)

        if not validation.passed:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(
                ticket.id,
                f"Validation failed:\n" + "\n".join(f"- {e}" for e in validation.errors),
            )
            print(f"  ✗ Validation failed: {validation.errors}")
            return

        # Git commit deliverables
        if self.config.git_commit_on_completion and result.deliverables_produced:
            self.git.commit_deliverables(
                result.deliverables_produced,
                f"conductor/{ticket.id}: {ticket.title} completed",
            )

        # Git tag: completed
        if self.config.git_tag_on_transitions:
            tag = f"conductor/{ticket.id}/completed"
            self.git.tag(tag)
            ticket.metadata.git_tag_completed = tag
            self.tracker.update_metadata(ticket.id, ticket.metadata)

        # Add agent output as comment
        if result.summary:
            self.tracker.add_comment(ticket.id, result.summary[:2000])

        # Metrics logging
        if result.metrics:
            print(f"  📊 {result.metrics.to_log_line()}")

        # Handle reviewer verdict
        if isinstance(executor, ReviewerExecutor):
            self._handle_reviewer_result(ticket, executor)
            return

        # HITL decision
        hitl_required = self._is_hitl_required(ticket)
        if hitl_required:
            self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
            print(f"  → AWAITING_REVIEW (human approval required)")
        else:
            self._auto_approve(ticket)

    def _handle_reviewer_result(
        self, ticket: Ticket, executor: ReviewerExecutor
    ) -> None:
        """Handle reviewer agent verdict."""
        review_result = executor.get_review_result()

        if review_result.approved:
            hitl_required = self._is_hitl_required(ticket)
            if hitl_required:
                self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
                print(f"  → Reviewer APPROVED → AWAITING_REVIEW (human)")
            else:
                self._auto_approve(ticket)
                print(f"  → Reviewer APPROVED → auto-approved")
        else:
            # Reviewer rejected — trigger rework
            iteration = ticket.metadata.iteration
            if iteration >= ticket.metadata.max_iterations:
                self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
                self.tracker.add_comment(
                    ticket.id,
                    f"Max iterations ({ticket.metadata.max_iterations}) reached. "
                    f"Escalating to human.",
                )
                print(f"  ⚠ Max iterations reached — PAUSED for human")
            else:
                self._trigger_rework(ticket, review_result)
                print(f"  ↺ Reviewer REJECTED — triggering rework (iteration {iteration + 1})")

    def _trigger_rework(self, reviewer_ticket: Ticket, review_result) -> None:
        """Re-run the specialist step with rejection feedback."""
        # Determine which specialist to re-run
        rework_target_id = (
            review_result.rework_target
            or reviewer_ticket.metadata.rework_target_step
        )

        if not rework_target_id:
            # Fall back: look for the step this reviewer reviews
            # by checking blocked_by
            if reviewer_ticket.blocked_by:
                rework_target_id = reviewer_ticket.blocked_by[0]

        if not rework_target_id:
            self.tracker.add_comment(
                reviewer_ticket.id, "Cannot determine rework target"
            )
            self.tracker.update_status(reviewer_ticket.id, TicketStatus.PAUSED)
            return

        try:
            specialist_ticket = self.tracker.get_ticket(rework_target_id)
        except KeyError:
            self.tracker.add_comment(
                reviewer_ticket.id,
                f"Rework target ticket not found: {rework_target_id}",
            )
            self.tracker.update_status(reviewer_ticket.id, TicketStatus.PAUSED)
            return

        # Store feedback as comment on the specialist ticket
        feedback_comment = (
            f"## Rework Required (iteration {specialist_ticket.metadata.iteration + 1})\n\n"
            f"{review_result.feedback}\n\n"
        )
        if review_result.issues:
            feedback_comment += "### Issues\n" + "\n".join(
                f"- {i}" for i in review_result.issues
            )
        self.tracker.add_comment(rework_target_id, feedback_comment)

        # Increment iteration
        specialist_ticket.metadata.iteration += 1
        self.tracker.update_metadata(rework_target_id, specialist_ticket.metadata)

        # Move specialist back to READY
        self.tracker.update_status(rework_target_id, TicketStatus.READY)

        # Move reviewer back to BACKLOG
        self.tracker.update_status(reviewer_ticket.id, TicketStatus.BACKLOG)

    def handle_approved(self, ticket: Ticket) -> None:
        """Human approved a ticket. Unblock dependents."""
        from conductor.watcher.dependency_resolver import unblock_dependents

        # Git tag: approved
        if self.config.git_tag_on_transitions:
            tag = f"conductor/{ticket.id}/approved"
            self.git.tag(tag)
            ticket.metadata.git_tag_approved = tag
            self.tracker.update_metadata(ticket.id, ticket.metadata)

        # Move to DONE
        self.tracker.update_status(ticket.id, TicketStatus.DONE)
        print(f"▶ Ticket {ticket.id} ({ticket.title}) → DONE (approved)")

        # Unblock dependent tickets
        unblock_dependents(ticket, self.tracker)

    def handle_rejected(self, ticket: Ticket) -> None:
        """Human rejected a ticket. Create remediation or rework."""
        iteration = ticket.metadata.iteration

        if iteration >= ticket.metadata.max_iterations:
            self.tracker.add_comment(
                ticket.id,
                f"Max iterations ({ticket.metadata.max_iterations}) exceeded. "
                "Escalating to human supervisor.",
            )
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            print(f"  ⚠ Max iterations — PAUSED")
            return

        # Get rejection feedback from latest comment
        feedback = ticket.comments[-1] if ticket.comments else "No feedback provided"

        # In-place rework: increment iteration, add feedback, move back to READY
        rework_comment = (
            f"## Rework Required (iteration {iteration + 1})\n\n"
            f"### Rejection Feedback\n\n{feedback}"
        )
        self.tracker.add_comment(ticket.id, rework_comment)

        ticket.metadata.iteration = iteration + 1
        self.tracker.update_metadata(ticket.id, ticket.metadata)
        self.tracker.update_status(ticket.id, TicketStatus.READY)
        print(
            f"▶ Ticket {ticket.id} REJECTED → READY "
            f"(rework iteration {iteration + 1})"
        )

    def _auto_approve(self, ticket: Ticket) -> None:
        """Auto-approve when HITL is not required."""
        from conductor.watcher.dependency_resolver import unblock_dependents

        if self.config.git_tag_on_transitions:
            tag = f"conductor/{ticket.id}/approved"
            self.git.tag(tag)

        self.tracker.update_status(ticket.id, TicketStatus.DONE)
        print(f"  → DONE (auto-approved)")
        unblock_dependents(ticket, self.tracker)

    def _is_hitl_required(self, ticket: Ticket) -> bool:
        """Determine if human review is required for this ticket."""
        # Per-step override
        if ticket.metadata.step in self.config.hitl_override_steps:
            return self.config.hitl_override_steps[ticket.metadata.step]

        # Per-phase override
        if ticket.metadata.phase in self.config.hitl_override_phases:
            return self.config.hitl_override_phases[ticket.metadata.phase]

        # Ticket-level setting
        return ticket.metadata.hitl_required

    def _build_context(self, ticket: Ticket) -> ExecutionContext:
        """Build execution context for an agent."""
        from pathlib import Path

        working_dir = Path(ticket.metadata.working_directory)
        if not working_dir.is_absolute():
            working_dir = self.project_config.project_base_path / working_dir

        return ExecutionContext(
            project_config=self.project_config,
            working_directory=working_dir,
            llm_provider=self.llm_provider,
            tracker=self.tracker,
            git=self.git,
            workpackage_id=ticket.metadata.workpackage,
            pod_id=ticket.metadata.pod,
        )

    def _handle_stale_tickets(self) -> None:
        """Detect and reset tickets stuck in IN_PROGRESS too long."""
        from datetime import datetime, timezone

        threshold = self.config.stale_ticket_threshold_seconds
        if threshold <= 0:
            return

        in_progress = self.tracker.get_tickets_by_status(TicketStatus.IN_PROGRESS)
        now = datetime.now(timezone.utc)

        for ticket in in_progress:
            if not ticket.updated_at:
                continue
            try:
                # Parse ISO timestamp
                updated = datetime.fromisoformat(
                    ticket.updated_at.replace("Z", "+00:00")
                )
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                elapsed = (now - updated).total_seconds()
                if elapsed > threshold:
                    self.tracker.update_status(ticket.id, TicketStatus.READY)
                    self.tracker.add_comment(
                        ticket.id,
                        f"⚠ Ticket stale — no update for {int(elapsed)}s "
                        f"(threshold: {threshold}s). Reset to READY for retry.",
                    )
                    print(
                        f"  ⚠ Stale ticket {ticket.id} reset to READY "
                        f"(no update for {int(elapsed)}s)"
                    )
            except (ValueError, TypeError):
                continue

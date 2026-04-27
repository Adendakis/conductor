"""Async event watcher — concurrent agent dispatch with semaphore."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Set

from conductor.executor.base import ExecutionContext, ExecutionResult
from conductor.executor.registry import AgentRegistry
from conductor.executor.reviewer_executor import ReviewerExecutor
from conductor.git.manager import GitManager
from conductor.git.worktree_manager import WorktreeManager
from conductor.models.config import ProjectConfig, WatcherConfig
from conductor.models.enums import TicketStatus, TicketType
from conductor.models.ticket import Ticket
from conductor.providers.base import LLMProvider
from conductor.tracker.backend import TrackerBackend
from conductor.validation.validator import DeliverableValidator
from conductor.watcher.dependency_resolver import all_blockers_resolved, unblock_dependents

log = logging.getLogger("conductor.watcher")


class AsyncEventWatcher:
    """Async event watcher with concurrent agent dispatch.

    Uses asyncio.Semaphore to limit how many agents execute concurrently.
    Agents are dispatched via asyncio.to_thread() since executors are synchronous.
    """

    def __init__(
        self,
        tracker: TrackerBackend,
        registry: AgentRegistry,
        git: GitManager,
        config: WatcherConfig,
        project_config: ProjectConfig,
        llm_provider: Optional[LLMProvider] = None,
        pipeline: Optional[list] = None,
    ):
        self.tracker = tracker
        self.registry = registry
        self.git = git
        self.config = config
        self.project_config = project_config
        self.llm_provider = llm_provider
        self.validator = DeliverableValidator()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._in_flight: Set[str] = set()
        self._pipeline = pipeline or []  # list[PhaseDefinition]
        self._worktree_manager: Optional[WorktreeManager] = None

    def run(self) -> None:
        """Start the async event loop."""
        log.info(
            f"Conductor Async Watcher started. "
            f"Polling every {self.config.poll_interval_seconds}s, "
            f"max concurrent: {self.config.max_concurrent_agents}"
        )
        asyncio.run(self._async_run())

    async def _async_run(self) -> None:
        """Main async loop."""
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_agents)

        while True:
            try:
                await self._poll_and_react()
                await asyncio.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                log.info("Watcher stopped.")
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in poll cycle: {e}")
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def _poll_and_react(self) -> None:
        """Single poll cycle: dispatch READY tickets concurrently."""
        # Handle stale tickets
        self._handle_stale_tickets()

        # Handle APPROVED and REJECTED (quick, synchronous)
        for ticket in self.tracker.get_tickets_by_status(TicketStatus.APPROVED):
            self._handle_approved(ticket)

        for ticket in self.tracker.get_tickets_by_status(TicketStatus.REJECTED):
            self._handle_rejected(ticket)

        # Dispatch READY tickets concurrently
        ready_tickets = self.tracker.get_tickets_by_status(TicketStatus.READY)
        tasks = []
        for ticket in ready_tickets:
            # Skip if already in-flight (avoid double dispatch)
            if ticket.id in self._in_flight:
                continue
            # Skip if blockers not resolved
            if not all_blockers_resolved(ticket, self.tracker):
                continue
            tasks.append(self._dispatch_ticket(ticket))

        if tasks:
            # Run all dispatches concurrently (semaphore limits actual execution)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_ticket(self, ticket: Ticket) -> None:
        """Dispatch a single ticket for execution, respecting the semaphore."""
        async with self._semaphore:
            self._in_flight.add(ticket.id)
            try:
                # Transition to IN_PROGRESS
                self.tracker.update_status(ticket.id, TicketStatus.IN_PROGRESS)
                log.info(f"Ticket {ticket.id} ({ticket.title}) → IN_PROGRESS")

                # Git tag
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
                        ticket.id, f"No executor for agent: {agent_name}"
                    )
                    return

                executor = self.registry.get(agent_name)
                context = self._build_context(ticket)

                # Checkout WP branch if this is a pod-scoped ticket
                if (
                    ticket.metadata.pod
                    and ticket.metadata.workpackage
                    and self._worktree_manager
                ):
                    self._worktree_manager.checkout_wp_branch(
                        ticket.metadata.workpackage, ticket.metadata.pod
                    )

                # Execute in a thread (executors are synchronous)
                try:
                    result = await asyncio.to_thread(
                        executor.execute, ticket, context
                    )
                except Exception as e:
                    self.tracker.update_status(ticket.id, TicketStatus.FAILED)
                    self.tracker.add_comment(ticket.id, f"Execution error: {e}")
                    log.error(f"Agent failed: {e}")
                    return

                if result.success:
                    self._handle_success(ticket, result, executor, context)
                else:
                    self.tracker.update_status(ticket.id, TicketStatus.FAILED)
                    self.tracker.add_comment(
                        ticket.id, f"Agent failed: {result.error or result.summary}"
                    )
                    log.error(f"Failed: {result.error or result.summary}")
            finally:
                self._in_flight.discard(ticket.id)

    def _handle_success(
        self, ticket: Ticket, result: ExecutionResult, executor, context
    ) -> None:
        """Handle successful execution (same logic as sync watcher)."""
        # Validate
        validation = self.validator.validate(ticket, context)
        if not validation.passed:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(
                ticket.id,
                "Validation failed:\n" + "\n".join(f"- {e}" for e in validation.errors),
            )
            return

        # Git commit
        if self.config.git_commit_on_completion and result.deliverables_produced:
            if ticket.metadata.pod and self._worktree_manager:
                # Commit in worktree on the WP branch
                self._worktree_manager.commit_in_pod(
                    ticket.metadata.pod,
                    result.deliverables_produced,
                    f"conductor/{ticket.id}: {ticket.title}",
                )
            else:
                self.git.commit_deliverables(
                    result.deliverables_produced,
                    f"conductor/{ticket.id}: {ticket.title}",
                )

        # Git tag completed
        if self.config.git_tag_on_transitions:
            tag = f"conductor/{ticket.id}/completed"
            self.git.tag(tag)

        # Comment
        if result.summary:
            self.tracker.add_comment(ticket.id, result.summary[:2000])

        # Metrics
        if result.metrics:
            log.info(f"{result.metrics.to_log_line()}")

        # Reviewer handling
        if isinstance(executor, ReviewerExecutor):
            review_result = executor.get_review_result()
            if review_result.approved:
                if self._is_hitl_required(ticket):
                    self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
                    log.info(f"AWAITING_REVIEW")
                else:
                    self._auto_approve(ticket)
            else:
                self._handle_reviewer_rejection(ticket, review_result)
            return

        # HITL decision
        if self._is_hitl_required(ticket):
            self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
            log.info(f"AWAITING_REVIEW")
        else:
            self._auto_approve(ticket)

    def _handle_approved(self, ticket: Ticket) -> None:
        """Human approved."""
        if self.config.git_tag_on_transitions:
            self.git.tag(f"conductor/{ticket.id}/approved")
        self.tracker.update_status(ticket.id, TicketStatus.DONE)
        log.info(f"{ticket.id} → DONE (approved)")

        # Merge WP branch to pod branch if this is the last step for this WP
        self._try_merge_wp_to_pod(ticket)

        unblock_dependents(ticket, self.tracker)
        self._check_phase_completion(ticket)

    def _handle_rejected(self, ticket: Ticket) -> None:
        """Human rejected."""
        iteration = ticket.metadata.iteration
        if iteration >= ticket.metadata.max_iterations:
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            self.tracker.add_comment(ticket.id, "Max iterations reached. Escalating.")
            return

        feedback = ticket.comments[-1] if ticket.comments else "No feedback"
        self.tracker.add_comment(
            ticket.id,
            f"## Rework Required (iteration {iteration + 1})\n\n{feedback}",
        )
        ticket.metadata.iteration = iteration + 1
        self.tracker.update_metadata(ticket.id, ticket.metadata)
        self.tracker.update_status(ticket.id, TicketStatus.READY)
        log.info(f"{ticket.id} REJECTED → READY (iteration {iteration + 1})")

    def _handle_reviewer_rejection(self, ticket, review_result) -> None:
        """Reviewer rejected — trigger rework."""
        iteration = ticket.metadata.iteration
        if iteration >= ticket.metadata.max_iterations:
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            return

        rework_target_id = (
            review_result.rework_target
            or ticket.metadata.rework_target_step
            or (ticket.blocked_by[0] if ticket.blocked_by else None)
        )
        if not rework_target_id:
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            return

        try:
            specialist = self.tracker.get_ticket(rework_target_id)
        except KeyError:
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            return

        self.tracker.add_comment(
            rework_target_id,
            f"## Rework Required (iteration {specialist.metadata.iteration + 1})\n\n"
            f"{review_result.feedback}",
        )
        specialist.metadata.iteration += 1
        self.tracker.update_metadata(rework_target_id, specialist.metadata)
        self.tracker.update_status(rework_target_id, TicketStatus.READY)
        self.tracker.update_status(ticket.id, TicketStatus.BACKLOG)

    def _auto_approve(self, ticket: Ticket) -> None:
        """Auto-approve and unblock."""
        if self.config.git_tag_on_transitions:
            self.git.tag(f"conductor/{ticket.id}/approved")
        self.tracker.update_status(ticket.id, TicketStatus.DONE)
        log.info(f"DONE (auto-approved)")

        # Merge WP branch to pod branch if this is the last step for this WP
        self._try_merge_wp_to_pod(ticket)

        unblock_dependents(ticket, self.tracker)
        self._check_phase_completion(ticket)

    def _is_hitl_required(self, ticket: Ticket) -> bool:
        if ticket.metadata.step in self.config.hitl_override_steps:
            return self.config.hitl_override_steps[ticket.metadata.step]
        if ticket.metadata.phase in self.config.hitl_override_phases:
            return self.config.hitl_override_phases[ticket.metadata.phase]
        return ticket.metadata.hitl_required

    def _build_context(self, ticket: Ticket) -> ExecutionContext:
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

    def _try_merge_wp_to_pod(self, ticket: Ticket) -> None:
        """Merge WP branch to pod branch if this is the last step for this WP.

        Only triggers when all tickets for this WP in this phase are DONE.
        """
        if not ticket.metadata.pod or not ticket.metadata.workpackage:
            return
        if not self._worktree_manager:
            return

        wp_id = ticket.metadata.workpackage
        pod_id = ticket.metadata.pod
        phase_id = ticket.metadata.phase

        # Check if all tickets for this WP in this phase are DONE
        phase_tickets = self.tracker.get_tickets_by_metadata(phase=phase_id)
        wp_tickets = [
            t for t in phase_tickets
            if t.metadata.workpackage == wp_id and t.metadata.pod == pod_id
        ]
        all_done = all(t.status == TicketStatus.DONE for t in wp_tickets)

        if not all_done:
            return

        log.info(f"All tickets for {wp_id} in {pod_id} are DONE — merging WP→pod")
        result = self._worktree_manager.merge_wp_to_pod(wp_id, pod_id)

        if not result.success:
            # Mark the last ticket as FAILED with conflict info
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(
                ticket.id,
                f"⚠ WP→pod merge conflict for {wp_id} in {pod_id}:\n"
                f"Conflicted files: {', '.join(result.conflicted_files)}\n"
                f"Error: {result.error}",
            )
            log.error(
                f"WP→pod merge failed: {wp_id} → {pod_id}: {result.error}"
            )

    def _handle_stale_tickets(self) -> None:
        """Reset tickets stuck in IN_PROGRESS."""
        threshold = self.config.stale_ticket_threshold_seconds
        if threshold <= 0:
            return

        now = datetime.now(timezone.utc)
        for ticket in self.tracker.get_tickets_by_status(TicketStatus.IN_PROGRESS):
            if ticket.id in self._in_flight:
                continue  # Still actively running
            if not ticket.updated_at:
                continue
            try:
                updated = datetime.fromisoformat(
                    ticket.updated_at.replace("Z", "+00:00")
                )
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if (now - updated).total_seconds() > threshold:
                    self.tracker.update_status(ticket.id, TicketStatus.READY)
                    self.tracker.add_comment(
                        ticket.id, f"⚠ Stale — reset to READY (threshold: {threshold}s)"
                    )
            except (ValueError, TypeError):
                continue

    def _check_phase_completion(self, completed_ticket: Ticket) -> None:
        """Check if all tickets in a phase are DONE. If so, handle hooks and create next phase tickets."""
        if not self._pipeline:
            return

        phase_id = completed_ticket.metadata.phase
        if not phase_id:
            return

        # Find the phase definition
        phase_def = None
        for p in self._pipeline:
            if p.phase_id == phase_id:
                phase_def = p
                break

        if not phase_def:
            return

        # Check if ALL tickets in this phase are DONE
        phase_tickets = self.tracker.get_tickets_by_metadata(phase=phase_id)
        all_done = all(t.status == TicketStatus.DONE for t in phase_tickets)

        if not all_done:
            return

        log.info(f"Phase '{phase_id}' complete — all tickets DONE")

        # --- Pod→main merge if this was a pod-scoped phase ---
        if self._worktree_manager and self._worktree_manager.get_all_pod_ids():
            # Check if any tickets in this phase had pods
            has_pods = any(t.metadata.pod for t in phase_tickets)
            if has_pods:
                log.info(f"Merging pods to main for phase '{phase_id}'")
                merge_results = self._worktree_manager.merge_pods_to_main()
                for result in merge_results:
                    if not result.success:
                        # Find a ticket from this pod to mark as FAILED
                        for t in phase_tickets:
                            pod_branch = f"pod/{t.metadata.pod}"
                            if pod_branch == result.branch:
                                self.tracker.update_status(
                                    t.id, TicketStatus.FAILED
                                )
                                self.tracker.add_comment(
                                    t.id,
                                    f"⚠ Pod→main merge conflict for {result.branch}:\n"
                                    f"Conflicted files: {', '.join(result.conflicted_files)}\n"
                                    f"Error: {result.error}",
                                )
                                break
                        log.error(
                            f"Pod→main merge failed: {result.branch}: {result.error}"
                        )
                        return  # Stop — don't create next phase tickets

        # --- Post-phase hook ---
        if phase_def.post_phase_hook == "setup_and_execute_pods":
            self._execute_pod_setup_hook(phase_def)

        # --- Create next phase tickets ---
        if not phase_def.creates_next_phases:
            return

        next_phases = [
            p for p in self._pipeline
            if p.phase_id in phase_def.creates_next_phases
        ]

        if not next_phases:
            return

        from conductor.watcher.ticket_creator import DynamicTicketCreator

        creator = DynamicTicketCreator(
            working_directory=self.project_config.project_base_path
        )
        created_ids = creator.create_scoped_tickets(
            completed_ticket,
            next_phases,
            self.tracker,
            worktree_manager=self._worktree_manager,
        )

        if created_ids:
            log.info(
                f"Phase '{phase_id}' complete → created {len(created_ids)} "
                f"tickets for {[p.phase_id for p in next_phases]}"
            )

    def _execute_pod_setup_hook(self, phase_def) -> None:
        """Execute the setup_and_execute_pods post-phase hook.

        1. Commit all phase outputs to main
        2. Read Pod_Assignment.json
        3. Create pod worktrees + WP branches
        """
        from pathlib import Path

        # Commit phase outputs before branching
        self.git.add_and_commit(
            f"conductor: {phase_def.phase_id} outputs committed before pod branching"
        )

        # Find Pod_Assignment.json (configurable path)
        pod_path = (
            self.project_config.project_base_path
            / self.config.pod_assignment_path
        )
        if not pod_path.exists():
            log.error(f"Pod assignment not found: {pod_path}")
            return

        # Create worktree manager if not already present
        if not self._worktree_manager:
            worktrees_base = (
                self.project_config.project_base_path
                / self.config.worktrees_directory
            )
            self._worktree_manager = WorktreeManager(
                git=self.git, worktrees_base=worktrees_base
            )

        # Setup worktrees
        pod_worktrees = self._worktree_manager.setup_pod_worktrees(pod_path)
        log.info(
            f"Pod setup complete: {len(pod_worktrees)} worktrees created"
        )

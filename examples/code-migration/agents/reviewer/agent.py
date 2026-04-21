"""Demo reviewer agent — always approves for demo flow."""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class DemoReviewerAgent(AgentExecutor):
    """Simulates a reviewer — always approves."""

    @property
    def agent_name(self) -> str:
        return "demo_reviewer"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(6)
        wp_id = ticket.metadata.workpackage or "unknown"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# Review: {wp_id}\n\n"
                f"## Verdict: APPROVED\n\n"
                f"## Feedback\n\n"
                f"Business logic extraction is complete and accurate.\n"
                f"All 4 rules are well-defined and traceable.\n\n"
                f"## Quality Score: 8/10\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Review for {wp_id}: APPROVED (quality 8/10)",
            deliverables_produced=created,
        )

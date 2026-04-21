"""Demo specialist agent — simulates business logic extraction."""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class DemoSpecialistAgent(AgentExecutor):
    """Simulates business logic extraction for a workpackage."""

    @property
    def agent_name(self) -> str:
        return "demo_specialist"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(8)
        wp_id = ticket.metadata.workpackage or "unknown"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# Business Logic: {wp_id}\n\n"
                f"## Extracted Rules\n\n"
                f"1. Input validation on all user-facing fields\n"
                f"2. Authorization check before data mutation\n"
                f"3. Audit trail for all state changes\n"
                f"4. Soft delete with 30-day retention\n\n"
                f"## Data Flow\n\n"
                f"```\nRequest → Validate → Authorize → Execute → Audit → Respond\n```\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Logic extraction for {wp_id}: 4 business rules identified",
            deliverables_produced=created,
        )

"""Demo reporter agent — simulates final report generation."""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class DemoReporterAgent(AgentExecutor):
    """Simulates final report generation."""

    @property
    def agent_name(self) -> str:
        return "demo_reporter"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(8)

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                "# Migration Final Report\n\n"
                "## Summary\n\n"
                "All workpackages have been analyzed, specified, and reviewed.\n\n"
                "## Workpackages Completed\n\n"
                "| WP | Name | Status |\n"
                "|-----|------|--------|\n"
                "| WP-001 | User Management | ✅ Done |\n"
                "| WP-002 | Post Management | ✅ Done |\n"
                "| WP-003 | Comment System | ✅ Done |\n\n"
                "## Next Steps\n\n"
                "1. Begin code generation phase\n"
                "2. Set up CI/CD pipeline\n"
                "3. Plan integration testing\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Final report generated. Migration analysis complete.",
            deliverables_produced=created,
        )

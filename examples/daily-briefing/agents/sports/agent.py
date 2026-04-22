"""Sports agent — fetches sports updates.

TODO: Replace with a real sports API or LLM with web search.
"""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class SportsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "sports_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(5)

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                "# Sports Update\n\n"
                "## Football\n"
                "- Champions League: Quarter-final results in\n\n"
                "## Tennis\n"
                "- Roland Garros: Seeds advance to round 3\n\n"
                "## Formula 1\n"
                "- Next race: Monaco GP this weekend\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Sports update: football, tennis, F1",
            deliverables_produced=created,
        )

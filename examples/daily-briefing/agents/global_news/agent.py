"""Global news agent — fetches world news headlines.

TODO: Replace with a real news API or LLM with web search.
"""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class GlobalNewsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "global_news_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(5)

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                "# Global News\n\n"
                "## Headlines\n\n"
                "1. **Climate summit** — 190 nations agree on new emissions targets\n"
                "2. **Space exploration** — Mars sample return mission on track for 2028\n"
                "3. **AI regulation** — EU finalizes AI Act implementation guidelines\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Global news: 3 headlines",
            deliverables_produced=created,
        )

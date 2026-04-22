"""Local news agent — fetches local news for a city.

TODO: Replace with a real news API or LLM with web search.
"""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class LocalNewsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "local_news_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(6)

        city = "Unknown"
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                city = line.split(":", 1)[1].strip()

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# Local News — {city}\n\n"
                f"## Top Stories\n\n"
                f"1. **New transit line approved** — City council votes to expand metro\n"
                f"2. **Tech hub expansion** — 3 new startups open offices downtown\n"
                f"3. **Weekend festival** — Annual food festival returns to the park\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Local news for {city}: 3 stories",
            deliverables_produced=created,
        )

"""Local news agent — uses LLM to generate local news for a city."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class LocalNewsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "local_news_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        city = self._extract_city(ticket)

        system = (
            "You are a local news correspondent. Provide realistic, plausible "
            "local news stories for the given city. Write in a professional "
            "news briefing style. Format in markdown."
        )
        user = (
            f"Write a local news briefing for {city} with 3-4 stories. Include:\n\n"
            f"1. A city infrastructure or transport story\n"
            f"2. A local business or economy story\n"
            f"3. A community or cultural event\n"
            f"4. (Optional) A local politics or policy story\n\n"
            f"Make the stories realistic and relevant to {city}. "
            f"Keep each story to 2-3 sentences. Total under 250 words."
        )

        content = ask_llm(system, user, max_tokens=600)
        content = f"# 📍 Local News — {city}\n\n{content}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Local news for {city}",
            deliverables_produced=created,
        )

    def _extract_city(self, ticket: Ticket) -> str:
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                return line.split(":", 1)[1].strip()
        return "Unknown"

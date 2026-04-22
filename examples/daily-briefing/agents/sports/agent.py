"""Sports agent — uses LLM to generate sports updates."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class SportsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "sports_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        city = self._extract_city(ticket)

        system = (
            "You are a sports journalist. Provide concise sports updates "
            "covering major leagues and events. Include scores where relevant. "
            "Format in markdown."
        )
        user = (
            f"Write a sports briefing relevant to someone in {city}. Include:\n\n"
            f"1. Football/soccer (local league + Champions League/international)\n"
            f"2. One other major sport popular in the region\n"
            f"3. Any upcoming major sporting events\n\n"
            f"Make it realistic. Keep each section to 2-3 sentences. "
            f"Total under 200 words."
        )

        content = ask_llm(system, user, max_tokens=500)
        content = f"# ⚽ Sports Update\n\n{content}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Sports update generated",
            deliverables_produced=created,
        )

    def _extract_city(self, ticket: Ticket) -> str:
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                return line.split(":", 1)[1].strip()
        return "Unknown"

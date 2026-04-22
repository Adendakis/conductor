"""Finance agent — searches for real market data, then summarizes with LLM."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket

PROMPT_FILE = Path(__file__).parent / "prompts" / "finance.md"


class FinanceAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "finance_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm
        from agents.web_search import search

        city = self._extract_city(ticket)

        search_results = search(
            f"stock market today S&P 500 DAX EUR USD gold oil prices",
            max_results=5,
        )

        system = PROMPT_FILE.read_text(encoding="utf-8")
        user = (
            f"City: {city}\n\n"
            f"## Search Results\n\n{search_results}\n\n"
            f"Write the finance briefing based on these search results."
        )

        content = ask_llm(system, user, max_tokens=500)
        content = f"# 💰 Finance Summary\n\n{content}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Finance briefing (from web search)",
            deliverables_produced=created,
        )

    def _extract_city(self, ticket: Ticket) -> str:
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                return line.split(":", 1)[1].strip()
        return "Unknown"

"""Global news agent — searches for real world news, then summarizes with LLM."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket

PROMPT_FILE = Path(__file__).parent / "prompts" / "global_news.md"


class GlobalNewsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "global_news_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm
        from agents.web_search import search

        search_results = search("world news today headlines", max_results=5)

        system = PROMPT_FILE.read_text(encoding="utf-8")
        user = (
            f"## Search Results\n\n{search_results}\n\n"
            f"Write the global news briefing based on these search results."
        )

        content = ask_llm(system, user, max_tokens=600)
        content = f"# 🌍 Global News\n\n{content}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Global news briefing (from web search)",
            deliverables_produced=created,
        )

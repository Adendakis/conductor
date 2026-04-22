"""Global news agent — uses LLM to generate world news headlines."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class GlobalNewsAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "global_news_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        system = (
            "You are a global news editor. Provide a concise summary of "
            "major world events. Write in a professional news briefing style. "
            "Format in markdown with clear headlines."
        )
        user = (
            "Write a global news briefing with 4-5 major world stories. Cover:\n\n"
            "1. Geopolitics or international relations\n"
            "2. Economy or trade\n"
            "3. Technology or science\n"
            "4. Climate or environment\n"
            "5. (Optional) Health or society\n\n"
            "Make stories realistic and current-sounding. "
            "Keep each to 2-3 sentences. Total under 250 words."
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
            summary="Global news briefing generated",
            deliverables_produced=created,
        )

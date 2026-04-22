"""Finance agent — uses LLM to generate market and finance updates."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class FinanceAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "finance_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        city = self._extract_city(ticket)

        system = (
            "You are a financial analyst. Provide a concise market briefing "
            "with key indices, currencies, and notable moves. "
            "Format in markdown with tables where appropriate."
        )
        user = (
            f"Write a finance briefing relevant to someone in {city}. Include:\n\n"
            f"1. Major stock indices (S&P 500, local exchange, Asian markets)\n"
            f"2. Key currency pairs relevant to the region\n"
            f"3. Commodities (oil, gold)\n"
            f"4. One notable market story or trend\n\n"
            f"Use realistic-sounding numbers. Keep under 200 words."
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
            summary="Finance briefing generated",
            deliverables_produced=created,
        )

    def _extract_city(self, ticket: Ticket) -> str:
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                return line.split(":", 1)[1].strip()
        return "Unknown"

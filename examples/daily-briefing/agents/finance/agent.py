"""Finance agent — fetches market and finance updates.

TODO: Replace with a real finance API or LLM with web search.
"""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class FinanceAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "finance_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(5)

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                "# Finance Summary\n\n"
                "## Markets\n"
                "- S&P 500: +0.8% (5,420)\n"
                "- NASDAQ: +1.2% (17,100)\n"
                "- DAX: +0.3% (18,900)\n\n"
                "## Currencies\n"
                "- EUR/USD: 1.0845\n"
                "- GBP/USD: 1.2710\n\n"
                "## Crypto\n"
                "- BTC: $68,500 (+2.1%)\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Finance: markets up, BTC +2.1%",
            deliverables_produced=created,
        )

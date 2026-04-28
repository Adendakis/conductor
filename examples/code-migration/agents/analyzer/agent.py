"""Demo analyzer agent — simulates legacy analysis."""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.metrics import StepMetrics
from conductor.models.ticket import Ticket


class DemoAnalyzerAgent(AgentExecutor):
    """Simulates legacy analysis — creates a markdown report."""

    @property
    def agent_name(self) -> str:
        return "demo_analyzer"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        start = time.time()
        time.sleep(8)

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)

            if "db_report" in path_str:
                content = (
                    "# Database Schema Analysis\n\n"
                    "## Tables Found\n\n"
                    "| Table | Columns | Rows (est.) |\n"
                    "|-------|---------|-------------|\n"
                    "| users | 8 | 50,000 |\n"
                    "| posts | 12 | 200,000 |\n"
                    "| comments | 6 | 1,500,000 |\n"
                    "| tags | 3 | 500 |\n\n"
                    "## Relationships\n\n"
                    "- users → posts (1:N)\n"
                    "- posts → comments (1:N)\n"
                    "- posts ↔ tags (M:N via post_tags)\n"
                )
            else:
                content = (
                    "# Source Code Analysis\n\n"
                    "## Modules Identified\n\n"
                    "- **UserModule** — authentication, profiles, permissions\n"
                    "- **PostModule** — CRUD, drafts, publishing workflow\n"
                    "- **CommentModule** — threading, moderation, notifications\n\n"
                    "## Complexity Assessment\n\n"
                    "- Total LOC: 45,000\n"
                    "- Cyclomatic complexity: Medium\n"
                    "- Test coverage: 23%\n"
                )

            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Analysis complete: {len(created)} report(s) generated",
            deliverables_produced=created,
            metrics=StepMetrics(
                step_id=ticket.metadata.step,
                agent_name=self.agent_name,
                model_id="demo-simulated",
                input_tokens=12500,
                output_tokens=3200,
                requests=1,
                elapsed_seconds=time.time() - start,
                cost_usd=0.0855,
            ),
        )

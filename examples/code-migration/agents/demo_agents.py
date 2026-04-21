"""Demo agents that simulate work with realistic output.

These agents don't call any LLM — they create placeholder deliverables
with enough content to demonstrate the orchestration flow.
"""

import json
import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class DemoAnalyzerAgent(AgentExecutor):
    """Simulates legacy analysis — creates a markdown report."""

    @property
    def agent_name(self) -> str:
        return "demo_analyzer"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(8)  # Visible on dashboard (poll every 5s)

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
        )


class DemoPlannerAgent(AgentExecutor):
    """Simulates workpackage planning — creates Workpackage_Planning.json."""

    @property
    def agent_name(self) -> str:
        return "demo_planner"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(10)  # Simulate planning work

        planning = {
            "projectName": "Blog Platform Migration",
            "migrationSequence": [
                {
                    "workpackageId": 1,
                    "name": "User Management",
                    "modules": ["UserModule"],
                    "type": "flow",
                    "complexity": "medium",
                    "dependencies": [],
                },
                {
                    "workpackageId": 2,
                    "name": "Post Management",
                    "modules": ["PostModule"],
                    "type": "flow",
                    "complexity": "high",
                    "dependencies": [1],
                },
                {
                    "workpackageId": 3,
                    "name": "Comment System",
                    "modules": ["CommentModule"],
                    "type": "flow",
                    "complexity": "medium",
                    "dependencies": [1],
                },
            ],
        }

        path_str = ticket.metadata.deliverable_paths[0]
        full_path = Path(context.working_directory) / path_str
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(
            json.dumps(planning, indent=2), encoding="utf-8"
        )

        return ExecutionResult(
            success=True,
            summary="Workpackage planning complete: 3 workpackages defined (Users, Posts, Comments)",
            deliverables_produced=[path_str],
        )


class DemoSpecialistAgent(AgentExecutor):
    """Simulates business logic extraction for a workpackage."""

    @property
    def agent_name(self) -> str:
        return "demo_specialist"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(8)  # Visible on dashboard

        wp_id = ticket.metadata.workpackage or "unknown"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)

            content = (
                f"# Business Logic: {wp_id}\n\n"
                f"## Extracted Rules\n\n"
                f"1. Input validation on all user-facing fields\n"
                f"2. Authorization check before data mutation\n"
                f"3. Audit trail for all state changes\n"
                f"4. Soft delete with 30-day retention\n\n"
                f"## Data Flow\n\n"
                f"```\n"
                f"Request → Validate → Authorize → Execute → Audit → Respond\n"
                f"```\n"
            )
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Logic extraction for {wp_id}: 4 business rules identified",
            deliverables_produced=created,
        )


class DemoReviewerAgent(AgentExecutor):
    """Simulates a reviewer — always approves (for demo flow)."""

    @property
    def agent_name(self) -> str:
        return "demo_reviewer"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(6)  # Simulate review

        wp_id = ticket.metadata.workpackage or "unknown"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)

            content = (
                f"# Review: {wp_id}\n\n"
                f"## Verdict: APPROVED\n\n"
                f"## Feedback\n\n"
                f"Business logic extraction is complete and accurate.\n"
                f"All 4 rules are well-defined and traceable.\n\n"
                f"## Quality Score: 8/10\n"
            )
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Review for {wp_id}: APPROVED (quality 8/10)",
            deliverables_produced=created,
        )


class DemoReporterAgent(AgentExecutor):
    """Simulates final report generation."""

    @property
    def agent_name(self) -> str:
        return "demo_reporter"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(8)  # Simulate final report

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)

            content = (
                "# Migration Final Report\n\n"
                "## Summary\n\n"
                "All workpackages have been analyzed, specified, and reviewed.\n\n"
                "## Workpackages Completed\n\n"
                "| WP | Name | Status |\n"
                "|-----|------|--------|\n"
                "| WP-001 | User Management | ✅ Done |\n"
                "| WP-002 | Post Management | ✅ Done |\n"
                "| WP-003 | Comment System | ✅ Done |\n\n"
                "## Next Steps\n\n"
                "1. Begin code generation phase\n"
                "2. Set up CI/CD pipeline\n"
                "3. Plan integration testing\n"
            )
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary="Final report generated. Migration analysis complete.",
            deliverables_produced=created,
        )

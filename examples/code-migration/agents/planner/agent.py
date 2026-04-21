"""Demo planner agent — simulates workpackage planning."""

import json
import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class DemoPlannerAgent(AgentExecutor):
    """Simulates workpackage planning — creates Workpackage_Planning.json."""

    @property
    def agent_name(self) -> str:
        return "demo_planner"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(10)

        planning = {
            "projectName": "Blog Platform Migration",
            "migrationSequence": [
                {"workpackageId": 1, "name": "User Management", "modules": ["UserModule"], "type": "flow", "complexity": "medium", "dependencies": []},
                {"workpackageId": 2, "name": "Post Management", "modules": ["PostModule"], "type": "flow", "complexity": "high", "dependencies": [1]},
                {"workpackageId": 3, "name": "Comment System", "modules": ["CommentModule"], "type": "flow", "complexity": "medium", "dependencies": [1]},
            ],
        }

        path_str = ticket.metadata.deliverable_paths[0]
        full_path = Path(context.working_directory) / path_str
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(json.dumps(planning, indent=2), encoding="utf-8")

        return ExecutionResult(
            success=True,
            summary="Workpackage planning complete: 3 workpackages defined (Users, Posts, Comments)",
            deliverables_produced=[path_str],
        )

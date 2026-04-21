"""ToolExecutor — runs deterministic subprocess tools, no LLM."""

import subprocess
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from .base import AgentExecutor, ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from conductor.models.ticket import Ticket


class ToolExecutor(AgentExecutor):
    """Executes a deterministic subprocess tool. No LLM involved.

    Subclass and implement:
    - build_command(): returns (command_string, working_directory)
    """

    @abstractmethod
    def build_command(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> tuple[str, str]:
        """Build shell command and working directory.

        Returns:
            (command_string, working_directory) tuple.
        """
        ...

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        cmd, cwd = self.build_command(ticket, context)
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                summary=f"Tool {self.agent_name} timed out",
                error="Process exceeded 900s timeout",
            )
        except OSError as e:
            return ExecutionResult(
                success=False,
                summary=f"Tool {self.agent_name} failed to start",
                error=str(e),
            )

        if result.returncode == 0:
            deliverables = self._discover_deliverables(ticket, context)
            return ExecutionResult(
                success=True,
                summary=f"Tool {self.agent_name} completed (exit 0)",
                deliverables_produced=deliverables,
            )
        return ExecutionResult(
            success=False,
            summary=f"Tool {self.agent_name} failed (exit {result.returncode})",
            error=(result.stderr[-2000:] if result.stderr else "Unknown error"),
        )

    def _discover_deliverables(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> list[str]:
        """Check which expected deliverables actually exist after execution."""
        found = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            if full_path.exists():
                found.append(path_str)
        return found

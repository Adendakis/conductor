"""Example agent — replace with your own implementation."""

from pathlib import Path

from conductor.executor.base import ExecutionContext, ExecutionResult
from conductor.executor.tool_executor import ToolExecutor


class ExampleAgent(ToolExecutor):
    """Example agent that creates a placeholder deliverable.

    Replace this with your actual agent logic:
    - Subclass ToolExecutor for subprocess-based agents
    - Subclass HybridExecutor for context-assembly + LLM agents
    - Subclass LLMExecutor for autonomous agents with tool access
    - Subclass ReviewerExecutor for quality review agents
    """

    @property
    def agent_name(self) -> str:
        return "example_agent"

    def build_command(self, ticket, context: ExecutionContext) -> tuple[str, str]:
        """Build the command to execute.

        For this example, we just echo a message.
        Replace with your actual command.
        """
        output_path = "output/example_output.md"
        if ticket.metadata.deliverable_paths:
            output_path = ticket.metadata.deliverable_paths[0]

        # Create the deliverable directly (simulating a tool)
        full_path = Path(context.working_directory) / output_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(
            f"# {ticket.title}\n\n"
            f"Output from example_agent.\n\n"
            f"Ticket: {ticket.id}\n"
            f"Step: {ticket.metadata.step}\n",
            encoding="utf-8",
        )

        return "echo done", str(context.working_directory)

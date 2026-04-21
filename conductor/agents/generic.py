"""Built-in generic executors for testing and demo purposes."""

from pathlib import Path
from typing import TYPE_CHECKING

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.executor.hybrid_executor import HybridExecutor
from conductor.executor.tool_executor import ToolExecutor

if TYPE_CHECKING:
    from conductor.context.prompt_context import PromptContext
    from conductor.models.ticket import Ticket


class NoOpExecutor(AgentExecutor):
    """Always succeeds. Creates empty deliverable files.

    Used as a fallback when no specific agent is registered for a ticket's
    agent_name. Useful for testing pipeline structure without real agents.
    """

    def __init__(self, name: str = "__noop__"):
        self._name = name

    @property
    def agent_name(self) -> str:
        return self._name

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        # Create empty deliverable files so validation passes
        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if path_str.endswith("/"):
                # Directory deliverable
                full_path.mkdir(parents=True, exist_ok=True)
                # Create a placeholder file inside
                (full_path / ".placeholder").write_text(
                    f"# Placeholder created by NoOpExecutor\n"
                    f"# Ticket: {ticket.id}\n"
                    f"# Agent: {ticket.metadata.agent_name}\n",
                    encoding="utf-8",
                )
            else:
                # File deliverable — write enough content to pass validation
                content = (
                    f"# {ticket.title}\n\n"
                    f"Placeholder deliverable created by NoOpExecutor.\n\n"
                    f"- Ticket: {ticket.id}\n"
                    f"- Step: {ticket.metadata.step}\n"
                    f"- Agent: {ticket.metadata.agent_name}\n"
                    f"- Phase: {ticket.metadata.phase}\n"
                )
                if path_str.endswith(".json"):
                    import json
                    content = json.dumps(
                        {
                            "placeholder": True,
                            "ticket_id": ticket.id,
                            "agent_name": ticket.metadata.agent_name,
                            "step": ticket.metadata.step,
                        },
                        indent=2,
                    )
                full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"NoOpExecutor: created {len(created)} placeholder deliverable(s)",
            deliverables_produced=created,
        )


class EchoExecutor(AgentExecutor):
    """Writes the assembled prompt context to the deliverable file.

    Useful for debugging context assembly — see exactly what an LLM
    would receive without actually calling one.
    """

    def __init__(self, name: str = "__echo__"):
        self._name = name

    @property
    def agent_name(self) -> str:
        return self._name

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        from conductor.context.assembler import ContextAssembler

        # Assemble the prompt context
        assembler = ContextAssembler(context.project_config)
        prompt_ctx = assembler.assemble(ticket, context)

        # Write the full prompt to the first deliverable path
        output = (
            f"# Echo Output for {ticket.id}\n\n"
            f"## System Prompt\n\n{prompt_ctx.system_prompt}\n\n"
            f"## User Prompt\n\n{prompt_ctx.user_prompt}\n\n"
            f"## Metadata\n\n"
            f"- Token estimate: {prompt_ctx.total_tokens_estimate}\n"
            f"- Source files: {prompt_ctx.source_files_included}\n"
        )

        created = []
        if ticket.metadata.deliverable_paths:
            path_str = ticket.metadata.deliverable_paths[0]
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(output, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"EchoExecutor: wrote assembled prompt ({prompt_ctx.total_tokens_estimate} est. tokens)",
            deliverables_produced=created,
        )


class ShellExecutor(ToolExecutor):
    """Runs a shell command specified in ticket metadata or description.

    Looks for a `shell_command` field in the ticket description (YAML frontmatter)
    or falls back to the agent_name as the command.

    Useful as a generic "run this script" agent without writing a custom class.
    """

    def __init__(self, name: str = "__shell__"):
        self._name = name

    @property
    def agent_name(self) -> str:
        return self._name

    def build_command(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> tuple[str, str]:
        # Look for shell_command in description (simple extraction)
        command = ""
        for line in ticket.description.splitlines():
            if line.strip().startswith("shell_command:"):
                command = line.split(":", 1)[1].strip().strip('"').strip("'")
                break

        if not command:
            # Fall back to echo
            command = f"echo 'ShellExecutor: no shell_command found for {ticket.id}'"

        return command, str(context.working_directory)

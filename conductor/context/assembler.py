"""ContextAssembler — builds prompt context from templates and inputs."""

import glob
from pathlib import Path
from typing import TYPE_CHECKING

from .prompt_context import PromptContext

if TYPE_CHECKING:
    from conductor.executor.base import ExecutionContext
    from conductor.models.config import ProjectConfig
    from conductor.models.ticket import Ticket


class ContextAssembler:
    """Assembles prompt context for a ticket from project files and templates.

    Responsibilities:
    - Read and render the prompt file (with variable substitution)
    - Gather input dependencies (deliverables from previous phases)
    - Apply token budget constraints (truncate if too large)
    - Inject scoping context (workpackage metadata, flow info, etc.)
    - Inject rework feedback for iteration > 1
    """

    def __init__(
        self, project_config: "ProjectConfig", max_context_tokens: int = 180_000
    ):
        self.config = project_config
        self.max_context_tokens = max_context_tokens

    def assemble(
        self, ticket: "Ticket", context: "ExecutionContext"
    ) -> PromptContext:
        """Build complete prompt context for a ticket."""
        # 1. Read prompt template
        prompt_template = self._read_prompt_file(ticket)

        # 2. Substitute variables
        variables = self._build_variables(ticket, context)
        rendered_prompt = self._render_template(prompt_template, variables)

        # 3. Gather input files
        input_sections = self._gather_inputs(ticket, context)

        # 4. Budget check and truncation
        input_sections = self._apply_budget(input_sections)

        # 5. Build system prompt
        system_prompt = self._build_system_prompt(ticket)

        # 6. Combine
        user_prompt = rendered_prompt
        for section_name, section_content in input_sections:
            user_prompt += f"\n\n## {section_name}\n\n{section_content}"

        # 7. Rework feedback injection
        if ticket.metadata.iteration > 1:
            feedback = self._get_rework_feedback(ticket)
            if feedback:
                user_prompt += (
                    f"\n\n## REWORK INSTRUCTIONS\n\n"
                    f"This is iteration {ticket.metadata.iteration}. "
                    f"Your previous output was rejected. Fix ONLY the issues below. "
                    f"Do NOT regenerate from scratch.\n\n{feedback}"
                )

        source_files = [name for name, _ in input_sections]

        result = PromptContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            source_files_included=source_files,
        )
        result._update_token_estimate()
        return result

    def _read_prompt_file(self, ticket: "Ticket") -> str:
        """Read the prompt template file."""
        if not ticket.metadata.prompt_file:
            return ""
        prompt_path = self.config.project_base_path / ticket.metadata.prompt_file
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return ""

    def _build_variables(
        self, ticket: "Ticket", context: "ExecutionContext"
    ) -> dict[str, str]:
        """Build template variable dict from ticket and context."""
        return {
            "workpackage_id": context.workpackage_id or "",
            "domain_name": context.domain_name or "",
            "pod_id": context.pod_id or "",
            "flow_id": context.flow_id or "",
            "project_name": self.config.project_name,
            "project_base_path": str(self.config.project_base_path),
            "output_base_path": str(self.config.effective_output_base),
            "phase": ticket.metadata.phase,
            "step": ticket.metadata.step,
        }

    def _render_template(
        self, template: str, variables: dict[str, str]
    ) -> str:
        """Substitute {variable_name} placeholders in template."""
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    def _gather_inputs(
        self, ticket: "Ticket", context: "ExecutionContext"
    ) -> list[tuple[str, str]]:
        """Read input dependency files."""
        sections: list[tuple[str, str]] = []
        for dep_path in ticket.metadata.input_dependencies:
            # Resolve variables in dependency path
            variables = self._build_variables(ticket, context)
            resolved_str = self._render_template(dep_path, variables)
            resolved = Path(context.working_directory) / resolved_str

            # Handle glob patterns
            if "*" in resolved_str or "?" in resolved_str:
                pattern = str(Path(context.working_directory) / resolved_str)
                for match in sorted(glob.glob(pattern)):
                    match_path = Path(match)
                    if match_path.is_file():
                        content = match_path.read_text(encoding="utf-8")
                        rel = str(match_path.relative_to(context.working_directory))
                        sections.append((rel, content))
            elif resolved.exists() and resolved.is_file():
                content = resolved.read_text(encoding="utf-8")
                sections.append((resolved_str, content))

        return sections

    def _apply_budget(
        self, sections: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """Truncate sections if total exceeds token budget."""
        budget_chars = self.max_context_tokens * 4

        total_chars = sum(len(content) for _, content in sections)
        if total_chars <= budget_chars:
            return sections

        result: list[tuple[str, str]] = []
        remaining_budget = budget_chars
        for name, content in sections:
            if len(content) <= remaining_budget:
                result.append((name, content))
                remaining_budget -= len(content)
            elif remaining_budget > 0:
                truncated = content[:remaining_budget] + "\n\n[... truncated ...]"
                result.append((name, truncated))
                remaining_budget = 0
            # Skip sections that don't fit at all
        return result

    def _build_system_prompt(self, ticket: "Ticket") -> str:
        """Build system prompt from agent definition file if it exists."""
        if not ticket.metadata.agent_name:
            return "You are a helpful assistant."

        # Look for agent definition file
        agent_file = (
            self.config.project_base_path
            / "agents"
            / f"{ticket.metadata.agent_name}.md"
        )
        if agent_file.exists():
            return agent_file.read_text(encoding="utf-8")

        return (
            f"You are the {ticket.metadata.agent_name} agent. "
            f"Complete the assigned task and produce the expected deliverables."
        )

    def _get_rework_feedback(self, ticket: "Ticket") -> str:
        """Get rework feedback from ticket comments."""
        # Look for the most recent rework comment
        for comment in reversed(ticket.comments):
            if "Rework Required" in comment or "REJECTED" in comment.upper():
                return comment
        # Fall back to last comment
        if ticket.comments:
            return ticket.comments[-1]
        return ""

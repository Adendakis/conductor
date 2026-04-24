"""LLMExecutor — autonomous agent with tool access."""

import logging
import re
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from conductor.models.metrics import StepMetrics, calculate_cost

from .base import AgentExecutor, ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from conductor.models.ticket import Ticket
    from conductor.providers.base import ModelConfig
    from conductor.tools.base import AgentTool

log = logging.getLogger("conductor.executor.llm")

_READ_ONCE_INSTRUCTION = """

============================================================
CRITICAL FILE READING RULE
Read each file ONCE only. Do NOT re-read files you have already read.
The file content is in your conversation history from the previous read.
Reference it from memory. Re-reading the same file wastes time and budget.
After reading all needed files, proceed directly to writing your output.
============================================================
"""


class LLMExecutor(AgentExecutor):
    """Executes an LLM agent with tool access (read/write/search files).

    The LLM receives a prompt and can call tools in a loop until it
    produces its deliverables.

    Subclass and implement:
    - get_system_prompt(): returns the agent's system prompt
    - get_user_prompt(): returns the task instructions + context
    - (optional) get_tools(): returns available tools
    - (optional) get_model_config(): returns model selection
    """

    @abstractmethod
    def get_system_prompt(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> str:
        """Build the system prompt for this agent."""
        ...

    @abstractmethod
    def get_user_prompt(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> str:
        """Build the user prompt (task instructions + context)."""
        ...

    def get_tools(self) -> list["AgentTool"]:
        """Return tools available to this agent. Default: file ops."""
        from conductor.tools.file_ops import (
            ListFilesTool,
            ReadFilesTool,
            ReadFileTool,
            SearchFileTool,
            WriteFileTool,
        )

        return [
            ReadFileTool(),
            ReadFilesTool(),
            WriteFileTool(),
            ListFilesTool(),
            SearchFileTool(),
        ]

    def get_model_config(self) -> "ModelConfig":
        """Return model configuration. Override for agent-specific models."""
        from conductor.providers.base import ModelConfig

        return ModelConfig()

    def get_sandbox_config(self) -> dict:
        """Override to customize file access rules for this agent.

        Return a dict with any of:
        - read_blocked_patterns: list[str]
        - write_blocked_patterns: list[str]
        - write_allowed_exceptions: list[str]

        Values are merged with ToolSandbox defaults.
        """
        return {}

    def get_preloaded_files(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> list[str]:
        """Override to declare files that should be inlined into the prompt.

        Returns a list of paths relative to working_directory.
        These are read and appended to the user prompt before the agent loop,
        saving tool call round-trips on predictable static reads.
        """
        return []

    # ------------------------------------------------------------------
    # Prompt pre-loading helpers
    # ------------------------------------------------------------------

    _FILE_PATH_RE = re.compile(
        r"""(?:^|[\s"'(,=:])"""           # preceded by whitespace or delimiter
        r"""(\.?\.?/[\w./-]+"""           # path starting with ./ ../ or /
        r"""|[\w][\w./-]*\.(?:md|txt|json|yaml|yml|py|xml|csv|toml|cfg|ini|sql))"""
        r"""(?=[\s"'),;:\]]|$)""",        # followed by delimiter or EOL
        re.MULTILINE,
    )

    def _resolve_prompt_references(
        self, prompt: str, working_directory: Path
    ) -> str:
        """Scan prompt for file paths and inline their content."""
        seen: set[str] = set()
        inlined: list[str] = []
        total_size = 0
        max_total = 200_000  # 200KB budget

        for match in self._FILE_PATH_RE.finditer(prompt):
            rel_path = match.group(1).strip()
            if rel_path in seen:
                continue
            seen.add(rel_path)

            full_path = working_directory / rel_path
            if not full_path.is_file():
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            if total_size + len(content) > max_total:
                break

            total_size += len(content)
            inlined.append(
                f"\n\n## [PRE-LOADED] {rel_path}\n\n{content}"
            )
            log.info("  📎 Pre-loaded referenced file: %s (%d chars)", rel_path, len(content))

        return prompt + "".join(inlined)

    def _inline_preloaded_files(
        self, prompt: str, files: list[str], working_directory: Path
    ) -> str:
        """Inline explicitly declared pre-loaded files."""
        parts: list[str] = []
        total_size = 0
        max_total = 200_000

        for rel_path in files:
            full_path = working_directory / rel_path
            if not full_path.is_file():
                log.warning("  ⚠ Pre-load file not found: %s", rel_path)
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                log.warning("  ⚠ Pre-load file unreadable: %s", rel_path)
                continue

            if total_size + len(content) > max_total:
                log.warning("  ⚠ Pre-load budget exceeded, skipping: %s", rel_path)
                break

            total_size += len(content)
            parts.append(
                f"\n\n## [PRE-LOADED] {rel_path}\n\n{content}"
            )
            log.info("  📎 Pre-loaded declared file: %s (%d chars)", rel_path, len(content))

        return prompt + "".join(parts)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        system_prompt = self.get_system_prompt(ticket, context)
        user_prompt = self.get_user_prompt(ticket, context)
        tools = self.get_tools()
        model_config = self.get_model_config()

        # --- Pre-load referenced files from prompt text ---
        user_prompt = self._resolve_prompt_references(
            user_prompt, context.working_directory
        )

        # --- Pre-load explicitly declared files ---
        preloaded = self.get_preloaded_files(ticket, context)
        if preloaded:
            user_prompt = self._inline_preloaded_files(
                user_prompt, preloaded, context.working_directory
            )

        # --- Append read-once instruction ---
        user_prompt += _READ_ONCE_INSTRUCTION

        # --- Apply agent sandbox config to tool context ---
        sandbox_overrides = self.get_sandbox_config()
        if sandbox_overrides:
            log.info("  🔒 Agent sandbox overrides: %s", sandbox_overrides)

        provider = context.llm_provider
        response = provider.run_agent_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            model_config=model_config,
            working_directory=context.working_directory,
            max_iterations=model_config.max_tool_iterations,
            sandbox_overrides=sandbox_overrides,
        )

        metrics = None
        if response.metrics:
            metrics = response.metrics
        elif response.completed:
            metrics = StepMetrics(
                step_id=ticket.metadata.step,
                agent_name=self.agent_name,
                requests=response.tool_calls_made + 1,
            )

        return ExecutionResult(
            success=response.completed,
            summary=response.final_text[:2000] if response.final_text else "",
            deliverables_produced=response.files_written,
            error=response.error,
            metrics=metrics,
        )

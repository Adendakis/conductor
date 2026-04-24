"""Shell command execution tool for LLM agents (opt-in)."""

import logging
import subprocess
from pathlib import Path

from .base import AgentTool, ToolContext, ToolParameter

log = logging.getLogger("conductor.tools.shell")

# Commands that are allowed by default.
# Agents can override by providing their own allowlist.
DEFAULT_ALLOWED_COMMANDS = frozenset({
    "sqlite3",
    "grep",
    "wc",
    "find",
    "head",
    "tail",
    "cat",
    "sort",
    "uniq",
    "awk",
    "sed",
    "cut",
    "diff",
    "ls",
    "tree",
})

DEFAULT_TIMEOUT_SECONDS = 30


class ExecuteCommandTool(AgentTool):
    """Execute a sandboxed shell command and return stdout/stderr.

    NOT included in the default tool set. Agents opt in via get_tools():

        def get_tools(self):
            tools = super().get_tools()
            tools.append(ExecuteCommandTool())
            return tools

    Security:
    - Only commands whose base name is in the allowlist can run.
    - Commands are executed with a timeout.
    - Working directory is set to the agent's working_directory.
    """

    def __init__(
        self,
        allowed_commands: frozenset[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        allowed = ", ".join(sorted(self._allowed))
        return (
            f"Execute a shell command and return stdout/stderr. "
            f"Allowed commands: {allowed}. "
            f"Timeout: {self._timeout}s."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                "command", "string",
                "The shell command to execute (e.g. 'sqlite3 analysis.db \"SELECT ...\"')",
            ),
        ]

    async def execute(self, arguments: dict, context: ToolContext) -> str:
        command = arguments.get("command", "").strip()
        if not command:
            return "Error: command is required"

        # Extract the base command name for allowlist check
        base_cmd = command.split()[0].split("/")[-1]
        if base_cmd not in self._allowed:
            return (
                f"Error: command '{base_cmd}' is not in the allowlist. "
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

        cwd = context.working_directory
        log.info("  🐚 Executing: %s (cwd=%s, timeout=%ds)", command, cwd, self._timeout)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {self._timeout}s"
        except Exception as e:
            return f"Error: {e}"

        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.append(f"[stderr]\n{result.stderr}")
        if result.returncode != 0:
            output_parts.append(f"[exit code: {result.returncode}]")

        output = "\n".join(output_parts) if output_parts else "(no output)"

        # Truncate large output
        if len(output) > 50_000:
            output = output[:50_000] + "\n\n[... truncated at 50KB ...]"

        return output

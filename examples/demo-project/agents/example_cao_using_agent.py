"""Example agent that delegates work to a CAO (CLI Agent Orchestrator) session.

Requires a running cao-server at http://localhost:9889.
Install CAO: uv tool install git+https://github.com/awslabs/cli-agent-orchestrator.git@main
Start server: cao-server

This agent:
1. Creates a CAO session with a specified provider (kiro_cli, claude_code, q_cli, etc.)
2. Sends the ticket's prompt as input to the CAO terminal
3. Polls the terminal status until it completes (IDLE after PROCESSING)
4. Reads the terminal output as the agent's summary
5. Cleans up the CAO session

The actual AI work happens inside the CAO tmux session — conductor just
orchestrates when it runs and what happens with the result.
"""

import time
from pathlib import Path
from typing import Optional

import httpx

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class CaoUsingAgent(AgentExecutor):
    """Delegates execution to a CAO terminal session.

    Configure via constructor or override in subclass:
    - cao_url: CAO server URL (default: http://localhost:9889)
    - provider: CLI provider to use (default: kiro_cli)
    - agent_profile: CAO agent profile name (default: developer)
    - poll_interval: seconds between status checks (default: 5)
    - timeout: max seconds to wait for completion (default: 600)
    """

    def __init__(
        self,
        name: str = "cao_agent",
        cao_url: str = "http://localhost:9889",
        provider: str = "kiro_cli",
        agent_profile: str = "developer",
        poll_interval: int = 5,
        timeout: int = 600,
    ):
        self._name = name
        self.cao_url = cao_url.rstrip("/")
        self.provider = provider
        self.agent_profile = agent_profile
        self.poll_interval = poll_interval
        self.timeout = timeout

    @property
    def agent_name(self) -> str:
        return self._name

    def execute(
        self, ticket: Ticket, context: ExecutionContext
    ) -> ExecutionResult:
        """Execute by delegating to a CAO session."""

        # 1. Build the prompt to send to the CAO agent
        prompt = self._build_prompt(ticket, context)

        # 2. Create a CAO session
        terminal_id = self._create_session(
            working_directory=str(context.working_directory)
        )
        if not terminal_id:
            return ExecutionResult(
                success=False,
                summary="Failed to create CAO session",
                error="Could not connect to cao-server or create session",
            )

        try:
            # 3. Send the prompt as input
            self._send_input(terminal_id, prompt)

            # 4. Poll until the agent finishes (status goes back to IDLE)
            completed = self._wait_for_completion(terminal_id)

            if not completed:
                return ExecutionResult(
                    success=False,
                    summary="CAO agent timed out",
                    error=f"Agent did not complete within {self.timeout}s",
                )

            # 5. Read the output
            output = self._get_output(terminal_id)

            # 6. Check if deliverables were produced
            deliverables = self._discover_deliverables(ticket, context)

            return ExecutionResult(
                success=True,
                summary=output[:2000] if output else "CAO agent completed",
                deliverables_produced=deliverables,
            )

        finally:
            # 7. Cleanup — exit the terminal
            self._cleanup(terminal_id)

    def _build_prompt(
        self, ticket: Ticket, context: ExecutionContext
    ) -> str:
        """Build the prompt to send to the CAO agent.

        Override this in subclasses for custom prompt assembly.
        """
        parts = [f"## Task: {ticket.title}\n"]

        if ticket.description:
            parts.append(ticket.description)

        parts.append(f"\n## Expected Deliverables\n")
        for path in ticket.metadata.deliverable_paths:
            parts.append(f"- {path}")

        if ticket.metadata.workpackage:
            parts.append(f"\nWorkpackage: {ticket.metadata.workpackage}")

        # Read prompt file if specified
        if ticket.metadata.prompt_file:
            prompt_path = (
                context.project_config.project_base_path
                / ticket.metadata.prompt_file
            )
            if prompt_path.exists():
                parts.append(f"\n## Instructions\n")
                parts.append(prompt_path.read_text(encoding="utf-8"))

        return "\n".join(parts)

    def _create_session(
        self, working_directory: Optional[str] = None
    ) -> Optional[str]:
        """Create a CAO session and return the terminal ID."""
        try:
            params = {
                "provider": self.provider,
                "agent_profile": self.agent_profile,
            }
            if working_directory:
                params["working_directory"] = working_directory

            resp = httpx.post(
                f"{self.cao_url}/sessions",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            terminal_id = data.get("id")
            return terminal_id
        except Exception as e:
            return None

    def _send_input(self, terminal_id: str, text: str) -> bool:
        """Send text input to a CAO terminal."""
        try:
            resp = httpx.post(
                f"{self.cao_url}/terminals/{terminal_id}/input",
                json={"input": text},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _wait_for_completion(self, terminal_id: str) -> bool:
        """Poll terminal status until IDLE (completed) or timeout."""
        start = time.time()
        saw_processing = False

        while time.time() - start < self.timeout:
            try:
                resp = httpx.get(
                    f"{self.cao_url}/terminals/{terminal_id}",
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "")

                    if status == "processing":
                        saw_processing = True
                    elif status == "idle" and saw_processing:
                        # Agent finished — went from processing back to idle
                        return True
                    elif status in ("completed", "error"):
                        return status == "completed"
            except Exception:
                pass

            time.sleep(self.poll_interval)

        return False

    def _get_output(self, terminal_id: str) -> str:
        """Read the terminal output."""
        try:
            resp = httpx.get(
                f"{self.cao_url}/terminals/{terminal_id}/output",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("output", "")
        except Exception:
            pass
        return ""

    def _cleanup(self, terminal_id: str) -> None:
        """Exit and delete the terminal."""
        try:
            httpx.post(
                f"{self.cao_url}/terminals/{terminal_id}/exit",
                timeout=10,
            )
            time.sleep(2)  # Give it a moment to exit
            httpx.delete(
                f"{self.cao_url}/terminals/{terminal_id}",
                timeout=10,
            )
        except Exception:
            pass

    def _discover_deliverables(
        self, ticket: Ticket, context: ExecutionContext
    ) -> list[str]:
        """Check which expected deliverables exist after execution."""
        found = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            if full_path.exists():
                found.append(path_str)
        return found


# --- Usage example ---
#
# In your project's agents/__init__.py:
#
# from conductor.executor.registry import AgentRegistry
# from .example_cao_using_agent import CaoUsingAgent
#
# def register(registry: AgentRegistry):
#     # Use Kiro CLI via CAO for code generation tasks
#     registry.register(CaoUsingAgent(
#         name="code_generator",
#         provider="kiro_cli",
#         agent_profile="developer",
#         timeout=900,  # 15 min for complex tasks
#     ))
#
#     # Use Claude Code via CAO for analysis tasks
#     registry.register(CaoUsingAgent(
#         name="code_analyzer",
#         provider="claude_code",
#         agent_profile="reviewer",
#         timeout=300,
#     ))
#
# In your pipeline.yaml:
#
#   steps:
#     - id: "generate_code"
#       name: "Generate Backend Code"
#       agent: "code_generator"      # matches the name above
#       prompt: "prompts/generate.md"
#       deliverables:
#         - path: "output/gen_src/service.java"

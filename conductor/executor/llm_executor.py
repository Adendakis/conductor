"""LLMExecutor — autonomous agent with tool access."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from conductor.models.metrics import StepMetrics, calculate_cost

from .base import AgentExecutor, ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from conductor.models.ticket import Ticket
    from conductor.providers.base import ModelConfig
    from conductor.tools.base import AgentTool


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
            ReadFileTool,
            SearchFileTool,
            WriteFileTool,
        )

        return [ReadFileTool(), WriteFileTool(), ListFilesTool(), SearchFileTool()]

    def get_model_config(self) -> "ModelConfig":
        """Return model configuration. Override for agent-specific models."""
        from conductor.providers.base import ModelConfig

        return ModelConfig()

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        system_prompt = self.get_system_prompt(ticket, context)
        user_prompt = self.get_user_prompt(ticket, context)
        tools = self.get_tools()
        model_config = self.get_model_config()

        provider = context.llm_provider
        response = provider.run_agent_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            model_config=model_config,
            working_directory=context.working_directory,
            max_iterations=model_config.max_tool_iterations,
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

"""HybridExecutor — deterministic context assembly + LLM single pass."""

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from conductor.models.metrics import StepMetrics, calculate_cost

from .base import AgentExecutor, ExecutionContext, ExecutionResult

if TYPE_CHECKING:
    from conductor.context.prompt_context import PromptContext
    from conductor.models.ticket import Ticket
    from conductor.providers.base import ModelConfig


class HybridExecutor(AgentExecutor):
    """Deterministic context assembly + optional tool step + LLM call.

    Subclass and implement:
    - assemble_context(): gather all input files and build the prompt
    - (optional) pre_tool(): run a tool before the LLM call
    - (optional) post_process(): transform LLM output before writing
    """

    @abstractmethod
    def assemble_context(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> "PromptContext":
        """Gather all inputs and build the complete prompt for the LLM."""
        ...

    def pre_tool(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> Optional[str]:
        """Optional: run a tool before the LLM call. Returns output or None."""
        return None

    def post_process(
        self, llm_output: str, ticket: "Ticket", context: ExecutionContext
    ) -> dict[str, str]:
        """Transform LLM output into deliverable files.

        Default: write entire output to the first expected deliverable path.
        Override for agents that produce multiple files or need parsing.

        Returns:
            Dict of {relative_path: content} to write.
        """
        if ticket.metadata.deliverable_paths:
            return {ticket.metadata.deliverable_paths[0]: llm_output}
        return {}

    def get_model_config(self) -> "ModelConfig":
        """Return model configuration. Override for agent-specific models."""
        from conductor.providers.base import ModelConfig

        return ModelConfig()

    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        # 1. Deterministic context assembly
        prompt_context = self.assemble_context(ticket, context)

        # 2. Optional pre-tool
        tool_output = self.pre_tool(ticket, context)
        if tool_output:
            prompt_context.append_section("Tool Output", tool_output)

        # 3. LLM call (single pass, no tool loop)
        provider = context.llm_provider
        model_config = self.get_model_config()
        response = provider.call(
            system_prompt=prompt_context.system_prompt,
            user_prompt=prompt_context.user_prompt,
            model_config=model_config,
        )

        if not response.success:
            return ExecutionResult(
                success=False,
                error=response.error,
                summary="LLM call failed",
            )

        # 4. Post-process and write deliverables
        files_to_write = self.post_process(response.content, ticket, context)
        written: list[str] = []
        for path, content in files_to_write.items():
            full_path = Path(context.working_directory) / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            written.append(path)

        cost = calculate_cost(
            response.input_tokens,
            response.output_tokens,
            response.model_id,
            response.cache_write_tokens,
            response.cache_read_tokens,
        )

        return ExecutionResult(
            success=True,
            summary=response.content[:500],
            deliverables_produced=written,
            metrics=StepMetrics(
                step_id=ticket.metadata.step,
                agent_name=self.agent_name,
                model_id=response.model_id,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_write_tokens=response.cache_write_tokens,
                cache_read_tokens=response.cache_read_tokens,
                requests=1,
                elapsed_seconds=response.elapsed,
                cost_usd=cost,
            ),
        )

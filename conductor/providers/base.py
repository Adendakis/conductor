"""LLM provider abstraction — base classes and models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from conductor.models.metrics import StepMetrics
    from conductor.tools.base import AgentTool


@dataclass
class ModelConfig:
    """Configuration for an LLM call."""

    model_id: str = "anthropic.claude-sonnet-4-20250514"
    region: str = "us-east-1"
    temperature: float = 0.2
    max_output_tokens: int = 64_000
    max_tool_iterations: int = 50
    retry_max_attempts: int = 5
    retry_base_delay: float = 2.0
    preferred_provider: Optional[str] = None  # label from pool config

    # Sliding window history management
    history_strategy: str = "truncate"  # "truncate", "summarize", "none"
    history_trigger_tokens: int = 120_000  # start truncating above this
    history_keep_tokens: int = 60_000  # keep this many recent tokens


@dataclass
class LLMResponse:
    """Response from a single LLM call."""

    success: bool
    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    model_id: str = ""
    elapsed: float = 0.0
    error: Optional[str] = None


@dataclass
class AgentLoopResponse:
    """Response from an agent loop (LLM + tool calls)."""

    completed: bool
    final_text: str = ""
    files_written: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    metrics: Optional["StepMetrics"] = None
    error: Optional[str] = None


class LLMProvider(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model_config: ModelConfig,
    ) -> LLMResponse:
        """Single LLM call (no tools). Used by HybridExecutor."""
        ...

    @abstractmethod
    def run_agent_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list["AgentTool"],
        model_config: ModelConfig,
        working_directory: Path,
        max_iterations: int = 50,
        sandbox_overrides: dict | None = None,
    ) -> AgentLoopResponse:
        """Run an agent loop: LLM calls tools until done."""
        ...

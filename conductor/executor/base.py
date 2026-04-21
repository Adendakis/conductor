"""Agent executor base classes and result models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from conductor.models.metrics import StepMetrics

if TYPE_CHECKING:
    from conductor.models.config import ProjectConfig
    from conductor.models.ticket import Ticket
    from conductor.providers.base import LLMProvider
    from conductor.tracker.backend import TrackerBackend
    from conductor.git.manager import GitManager


@dataclass
class ExecutionResult:
    """Result returned by any agent executor."""

    success: bool
    summary: str
    error: Optional[str] = None
    deliverables_produced: list[str] = field(default_factory=list)
    metrics: Optional[StepMetrics] = None


@dataclass
class ExecutionContext:
    """Runtime context passed to every agent executor."""

    project_config: "ProjectConfig"
    working_directory: Path
    llm_provider: "LLMProvider"
    tracker: "TrackerBackend"
    git: "GitManager"

    workpackage_id: Optional[str] = None
    domain_name: Optional[str] = None
    pod_id: Optional[str] = None
    flow_id: Optional[str] = None

    previous_deliverables: dict[str, Path] = field(default_factory=dict)


class AgentExecutor(ABC):
    """Base class for all agent execution strategies."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Unique name identifying this agent."""
        ...

    @abstractmethod
    def execute(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> ExecutionResult:
        """Execute the agent's work for the given ticket."""
        ...

    def validate_deliverables(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> "ValidationResult":
        """Validate deliverables after execution.

        Default delegates to DeliverableValidator.
        """
        from conductor.validation.validator import DeliverableValidator

        validator = DeliverableValidator()
        return validator.validate(ticket, context)

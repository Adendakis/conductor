"""Demo agents for the code-migration example."""

from conductor.executor.registry import AgentRegistry

from .analyzer import DemoAnalyzerAgent
from .planner import DemoPlannerAgent
from .specialist import DemoSpecialistAgent
from .reviewer import DemoReviewerAgent
from .reporter import DemoReporterAgent
from .scope_discovery import AcmScopeDiscovery
from .validators import (
    validate_dag_no_cycles,
    validate_pod_assignment_completeness,
)


def register(registry: AgentRegistry):
    """Register all demo agents, scope discovery, and validators."""
    # Agents
    registry.register(DemoAnalyzerAgent())
    registry.register(DemoPlannerAgent())
    registry.register(DemoSpecialistAgent())
    registry.register(DemoReviewerAgent())
    registry.register(DemoReporterAgent())

    # Scope discovery — tells conductor how to read ACM's data formats
    registry.set_scope_discovery(AcmScopeDiscovery())

    # Custom validators — ACM-specific deliverable checks
    registry.register_validator("validate_dag_no_cycles", validate_dag_no_cycles)
    registry.register_validator(
        "validate_pod_assignment_completeness",
        validate_pod_assignment_completeness,
    )

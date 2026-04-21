"""Demo agents for the code-migration example."""

from conductor.executor.registry import AgentRegistry

from .demo_agents import (
    DemoAnalyzerAgent,
    DemoPlannerAgent,
    DemoReporterAgent,
    DemoReviewerAgent,
    DemoSpecialistAgent,
)


def register(registry: AgentRegistry):
    """Register all demo agents."""
    registry.register(DemoAnalyzerAgent())
    registry.register(DemoPlannerAgent())
    registry.register(DemoSpecialistAgent())
    registry.register(DemoReviewerAgent())
    registry.register(DemoReporterAgent())

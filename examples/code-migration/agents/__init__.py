"""Demo agents for the code-migration example."""

from conductor.executor.registry import AgentRegistry

from .analyzer import DemoAnalyzerAgent
from .planner import DemoPlannerAgent
from .specialist import DemoSpecialistAgent
from .reviewer import DemoReviewerAgent
from .reporter import DemoReporterAgent


def register(registry: AgentRegistry):
    """Register all demo agents."""
    registry.register(DemoAnalyzerAgent())
    registry.register(DemoPlannerAgent())
    registry.register(DemoSpecialistAgent())
    registry.register(DemoReviewerAgent())
    registry.register(DemoReporterAgent())

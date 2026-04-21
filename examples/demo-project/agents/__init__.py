"""Agent registration for this project."""

from conductor.executor.registry import AgentRegistry
from .example_agent import ExampleAgent


def register(registry: AgentRegistry):
    """Register all project agents with conductor."""
    registry.register(ExampleAgent())

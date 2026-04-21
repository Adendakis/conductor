"""Built-in agent implementations and registry builder."""

from conductor.executor.registry import AgentRegistry

from .generic import EchoExecutor, NoOpExecutor, ShellExecutor


def build_default_registry() -> AgentRegistry:
    """Build a registry with built-in generic executors.

    These serve as fallbacks when no user-specific agent is registered
    for a given agent_name.
    """
    registry = AgentRegistry()
    registry.register(NoOpExecutor("__noop__"))
    registry.register(EchoExecutor("__echo__"))
    registry.register(ShellExecutor("__shell__"))
    return registry

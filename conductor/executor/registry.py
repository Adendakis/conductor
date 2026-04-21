"""Agent registry — maps agent names to executor instances."""

from .base import AgentExecutor


class AgentRegistry:
    """Maps agent names to executor instances.

    Agents are registered at startup. The watcher resolves
    ticket.metadata.agent_name to an executor via this registry.

    If no executor is registered for a given name and a fallback is set,
    the fallback executor is used instead of raising.
    """

    def __init__(self) -> None:
        self._executors: dict[str, AgentExecutor] = {}
        self._fallback: AgentExecutor | None = None

    def register(self, executor: AgentExecutor) -> None:
        """Register an agent executor by its agent_name."""
        self._executors[executor.agent_name] = executor

    def set_fallback(self, executor: AgentExecutor) -> None:
        """Set a fallback executor for unregistered agent names."""
        self._fallback = executor

    def get(self, agent_name: str) -> AgentExecutor:
        """Resolve agent name to executor.

        If not found and a fallback is set, returns the fallback.
        If not found and no fallback, raises KeyError.
        """
        if agent_name in self._executors:
            return self._executors[agent_name]
        if self._fallback is not None:
            return self._fallback
        raise KeyError(
            f"No executor registered for agent '{agent_name}'. "
            f"Available: {list(self._executors.keys())}"
        )

    def list_agents(self) -> list[str]:
        """Return all registered agent names."""
        return list(self._executors.keys())

    def __contains__(self, agent_name: str) -> bool:
        return agent_name in self._executors or self._fallback is not None

    def __len__(self) -> int:
        return len(self._executors)

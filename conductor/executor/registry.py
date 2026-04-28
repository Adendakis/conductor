"""Agent registry — maps agent names to executor instances."""

from typing import TYPE_CHECKING, Optional

from .base import AgentExecutor

if TYPE_CHECKING:
    from conductor.watcher.scope_discovery import ScopeDiscovery


class AgentRegistry:
    """Maps agent names to executor instances.

    Agents are registered at startup. The watcher resolves
    ticket.metadata.agent_name to an executor via this registry.

    If no executor is registered for a given name and a fallback is set,
    the fallback executor is used instead of raising.

    Also holds the project's ScopeDiscovery instance for dynamic ticket creation.
    """

    def __init__(self) -> None:
        self._executors: dict[str, AgentExecutor] = {}
        self._fallback: AgentExecutor | None = None
        self._scope_discovery: Optional["ScopeDiscovery"] = None
        self._custom_validators: dict[str, object] = {}

    def register(self, executor: AgentExecutor) -> None:
        """Register an agent executor by its agent_name."""
        self._executors[executor.agent_name] = executor

    def set_fallback(self, executor: AgentExecutor) -> None:
        """Set a fallback executor for unregistered agent names."""
        self._fallback = executor

    def set_scope_discovery(self, discovery: "ScopeDiscovery") -> None:
        """Set the project's scope discovery implementation.

        Called from the project's agents module register() function.
        If not set, DefaultScopeDiscovery is used.
        """
        self._scope_discovery = discovery

    def get_scope_discovery(self) -> "ScopeDiscovery":
        """Get the scope discovery instance. Returns default if none set."""
        if self._scope_discovery is not None:
            return self._scope_discovery
        from conductor.watcher.scope_discovery import DefaultScopeDiscovery
        return DefaultScopeDiscovery()

    def register_validator(self, name: str, fn: object) -> None:
        """Register a custom validator function by name.

        Called from the project's agents module register() function.
        The validator is a callable(ticket, context) -> ValidationResult.
        """
        self._custom_validators[name] = fn

    def get_custom_validators(self) -> dict[str, object]:
        """Get all registered custom validators."""
        return dict(self._custom_validators)

    def get(self, agent_name: str) -> AgentExecutor:
        """Resolve agent name to executor."""
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

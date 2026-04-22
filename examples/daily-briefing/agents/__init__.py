"""Daily Briefing — Agent registration."""

from conductor.executor.registry import AgentRegistry

from .dispatcher import BriefingDispatcher
from .weather import WeatherAgent
from .local_news import LocalNewsAgent
from .global_news import GlobalNewsAgent
from .sports import SportsAgent
from .finance import FinanceAgent
from .reconciler import ReconcilerAgent


def register(registry: AgentRegistry):
    """Register all daily briefing agents."""
    registry.register(BriefingDispatcher())
    registry.register(WeatherAgent())
    registry.register(LocalNewsAgent())
    registry.register(GlobalNewsAgent())
    registry.register(SportsAgent())
    registry.register(FinanceAgent())
    registry.register(ReconcilerAgent())

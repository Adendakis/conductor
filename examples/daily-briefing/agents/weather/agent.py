"""Weather agent — uses LLM to generate a weather briefing for a city."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class WeatherAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "weather_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        city = self._extract_city(ticket)

        system = (
            "You are a weather briefing assistant. Provide concise, accurate "
            "weather information. Use emoji for visual clarity. "
            "Format your response in markdown."
        )
        user = (
            f"Provide a current weather briefing for {city}. Include:\n\n"
            f"1. Current conditions (temperature, sky, wind, humidity)\n"
            f"2. Today's high and low\n"
            f"3. 3-day forecast summary\n"
            f"4. Any weather alerts or advisories\n\n"
            f"Use realistic data for {city} based on the current season. "
            f"Keep it under 200 words."
        )

        content = ask_llm(system, user, max_tokens=500)
        content = f"# ☁️ Weather — {city}\n\n{content}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Weather briefing for {city}",
            deliverables_produced=created,
        )

    def _extract_city(self, ticket: Ticket) -> str:
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                return line.split(":", 1)[1].strip()
        return "Unknown"

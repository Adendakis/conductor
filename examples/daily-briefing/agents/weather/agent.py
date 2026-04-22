"""Weather agent — fetches weather for a city.

TODO: Replace with a real weather API call (OpenWeatherMap, etc.)
or an LLM that has web access.
"""

import time
from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class WeatherAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "weather_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        time.sleep(5)

        # Extract city from ticket description
        city = "Unknown"
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                city = line.split(":", 1)[1].strip()

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(
                f"# Weather Report — {city}\n\n"
                f"☀️ **Current**: 22°C, partly cloudy\n"
                f"🌡️ **High/Low**: 25°C / 18°C\n"
                f"💨 **Wind**: 12 km/h NW\n"
                f"💧 **Humidity**: 45%\n\n"
                f"## Forecast\n\n"
                f"- Tomorrow: 24°C, sunny\n"
                f"- Day after: 20°C, rain expected\n",
                encoding="utf-8",
            )
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Weather report for {city}: 22°C, partly cloudy",
            deliverables_produced=created,
        )

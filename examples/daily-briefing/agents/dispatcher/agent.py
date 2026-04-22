"""Dispatcher agent — reads human preferences and creates topic agent tickets.

This is the key agent in the daily-briefing example. It demonstrates
dynamic fan-out: creating tickets at runtime based on user input,
with a reconciliation ticket that depends on all of them.
"""

import re
from typing import Optional

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.enums import TicketStatus, TicketType
from conductor.models.ticket import Ticket, TicketMetadata


# Map checkbox labels to agent names and descriptions
TOPIC_AGENTS = {
    "weather": {
        "agent": "weather_agent",
        "title": "Weather Report",
        "deliverable": "output/topics/weather.md",
    },
    "local_news": {
        "agent": "local_news_agent",
        "title": "Local News Summary",
        "deliverable": "output/topics/local_news.md",
    },
    "global_news": {
        "agent": "global_news_agent",
        "title": "Global News Summary",
        "deliverable": "output/topics/global_news.md",
    },
    "sports": {
        "agent": "sports_agent",
        "title": "Sports Update",
        "deliverable": "output/topics/sports.md",
    },
    "finance": {
        "agent": "finance_agent",
        "title": "Finance Summary",
        "deliverable": "output/topics/finance.md",
    },
}


class BriefingDispatcher(AgentExecutor):
    """Reads user preferences and creates dynamic topic tickets.

    Parses the setup ticket's comments for:
    - city: <city name>
    - [x] weather / [ ] weather
    - [x] local_news / [ ] local_news
    - etc.

    Creates one ticket per checked topic + a reconciliation ticket
    that depends on all of them.
    """

    @property
    def agent_name(self) -> str:
        return "briefing_dispatcher"

    def execute(
        self, ticket: Ticket, context: ExecutionContext
    ) -> ExecutionResult:
        tracker = context.tracker

        # Find the setup ticket (the one the human filled out)
        setup_tickets = tracker.get_tickets_by_metadata(
            phase="setup", step="user_input"
        )
        if not setup_tickets:
            return ExecutionResult(
                success=False, summary="No setup ticket found", error="Missing user input"
            )

        setup_ticket = setup_tickets[0]

        # Parse user preferences from comments
        city, selected_topics = self._parse_preferences(setup_ticket)

        if not selected_topics:
            return ExecutionResult(
                success=False,
                summary="No topics selected",
                error="User did not select any topics. Edit the setup ticket and add selections.",
            )

        # Create topic agent tickets
        topic_ticket_ids = []
        for topic in selected_topics:
            config = TOPIC_AGENTS[topic]
            topic_ticket = Ticket(
                title=f"{config['title']} — {city}",
                description=(
                    f"## {config['title']}\n\n"
                    f"**City**: {city}\n"
                    f"**Topic**: {topic}\n\n"
                    f"Generate a brief summary about {topic.replace('_', ' ')} "
                    f"for {city}.\n"
                ),
                status=TicketStatus.READY,
                ticket_type=TicketType.TASK,
                metadata=TicketMetadata(
                    phase="topics",
                    step=f"topic_{topic}",
                    agent_name=config["agent"],
                    deliverable_paths=[config["deliverable"]],
                    hitl_required=False,
                ),
            )
            tid = tracker.create_ticket(topic_ticket)
            topic_ticket_ids.append(tid)

        # Create reconciliation ticket (blocked by ALL topic tickets)
        recon_ticket = Ticket(
            title=f"Daily Briefing — {city}",
            description=(
                f"## Daily Briefing Reconciliation\n\n"
                f"**City**: {city}\n"
                f"**Topics**: {', '.join(selected_topics)}\n\n"
                f"Merge all topic summaries into a single daily briefing.\n"
            ),
            status=TicketStatus.BACKLOG,
            ticket_type=TicketType.TASK,
            metadata=TicketMetadata(
                phase="reconcile",
                step="merge_briefing",
                agent_name="reconciler_agent",
                deliverable_paths=["output/daily_briefing.md"],
                hitl_required=True,
                input_dependencies=[
                    TOPIC_AGENTS[t]["deliverable"] for t in selected_topics
                ],
            ),
            blocked_by=topic_ticket_ids,
        )
        recon_id = tracker.create_ticket(recon_ticket)

        return ExecutionResult(
            success=True,
            summary=(
                f"Dispatched {len(topic_ticket_ids)} topic agents for {city}: "
                f"{', '.join(selected_topics)}. "
                f"Reconciliation ticket: {recon_id}"
            ),
        )

    def _parse_preferences(
        self, ticket: Ticket
    ) -> tuple[str, list[str]]:
        """Parse city and selected topics from ticket comments.

        Expected format in a comment:
            city: Berlin, Germany
            [x] weather
            [x] local_news
            [ ] global_news
            [x] sports
            [ ] finance
        """
        city = "Unknown"
        selected: list[str] = []

        # Search through all comments (latest first)
        for comment in reversed(ticket.comments):
            lines = comment.splitlines()
            for line in lines:
                line = line.strip()

                # Parse city
                city_match = re.match(r"city:\s*(.+)", line, re.IGNORECASE)
                if city_match:
                    city = city_match.group(1).strip()

                # Parse checkboxes: [x] topic_name or [X] topic_name
                check_match = re.match(r"\[([xX])\]\s*(\w+)", line)
                if check_match:
                    topic = check_match.group(2).lower()
                    if topic in TOPIC_AGENTS and topic not in selected:
                        selected.append(topic)

            # Stop after first comment that has selections
            if selected:
                break

        return city, selected

"""Reconciler agent — uses LLM to merge topic summaries into a polished briefing."""

from pathlib import Path

from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult
from conductor.models.ticket import Ticket


class ReconcilerAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "reconciler_agent"

    def execute(self, ticket: Ticket, context: ExecutionContext) -> ExecutionResult:
        from agents.llm_helper import ask_llm

        city = "Unknown"
        for line in ticket.description.splitlines():
            if line.startswith("**City**:"):
                city = line.split(":", 1)[1].strip()

        # Gather all topic outputs
        sections = []
        topics_dir = Path(context.working_directory) / "output" / "topics"
        if topics_dir.exists():
            for topic_file in sorted(topics_dir.glob("*.md")):
                sections.append(topic_file.read_text(encoding="utf-8"))

        for dep_path in ticket.metadata.input_dependencies:
            full_path = Path(context.working_directory) / dep_path
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8")
                if content not in sections:
                    sections.append(content)

        raw_content = "\n\n---\n\n".join(sections) if sections else "No topics available."

        system = (
            "You are an editorial assistant. Your job is to take multiple "
            "topic briefings and merge them into a single, cohesive daily "
            "briefing document. Maintain the key information from each section "
            "but improve flow, remove redundancy, and add a brief executive "
            "summary at the top. Format in clean markdown."
        )
        user = (
            f"Merge the following topic briefings into a single daily briefing "
            f"for {city}. Add:\n\n"
            f"1. A 2-3 sentence executive summary at the top\n"
            f"2. Clean section headers for each topic\n"
            f"3. A 'Key Takeaways' section at the end with 3-5 bullet points\n\n"
            f"Here are the raw topic briefings:\n\n"
            f"---\n\n{raw_content}"
        )

        merged = ask_llm(system, user, max_tokens=2000)
        briefing = f"# 📰 Daily Briefing — {city}\n\n{merged}\n"

        created = []
        for path_str in ticket.metadata.deliverable_paths:
            full_path = Path(context.working_directory) / path_str
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(briefing, encoding="utf-8")
            created.append(path_str)

        return ExecutionResult(
            success=True,
            summary=f"Daily briefing for {city}: merged {len(sections)} topics",
            deliverables_produced=created,
        )

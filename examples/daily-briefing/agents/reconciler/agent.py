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

        # Gather all topic outputs — use input_dependencies order (matches user's selection order)
        sections = []
        for dep_path in ticket.metadata.input_dependencies:
            full_path = Path(context.working_directory) / dep_path
            if full_path.exists() and full_path.is_file():
                sections.append(full_path.read_text(encoding="utf-8"))

        # Fall back to scanning the directory if no input_dependencies
        if not sections:
            topics_dir = Path(context.working_directory) / "output" / "topics"
            if topics_dir.exists():
                for topic_file in sorted(topics_dir.glob("*.md")):
                    sections.append(topic_file.read_text(encoding="utf-8"))

        raw_content = "\n\n---\n\n".join(sections) if sections else "No topics available."

        # Read prompt from file
        prompt_file = Path(__file__).parent / "prompts" / "reconcile.md"
        system = prompt_file.read_text(encoding="utf-8")

        user = (
            f"City: {city}\n"
            f"Number of topics: {len(sections)}\n\n"
            f"Here are the topic briefings to merge:\n\n"
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

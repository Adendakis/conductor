"""ReviewerExecutor — evaluates deliverables and returns a verdict."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from conductor.models.review import ReviewResult

from .base import ExecutionContext, ExecutionResult
from .hybrid_executor import HybridExecutor

if TYPE_CHECKING:
    from conductor.context.prompt_context import PromptContext
    from conductor.models.ticket import Ticket


class ReviewerExecutor(HybridExecutor):
    """Specialized executor for reviewer agents.

    Returns a structured ReviewResult (approved/rejected with feedback).
    The watcher uses this to decide: approve, reject + rework, or escalate.
    """

    def __init__(
        self,
        name: str,
        reviewer_for: str,
        max_iterations: int = 3,
    ):
        self._agent_name = name
        self.reviewer_for = reviewer_for
        self.max_iterations = max_iterations
        self._last_review_result: Optional[ReviewResult] = None

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def assemble_context(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> "PromptContext":
        """Assemble reviewer context: deliverables + original spec + criteria.

        Override in concrete reviewer implementations to provide
        domain-specific context assembly.
        """
        from conductor.context.prompt_context import PromptContext

        system_prompt = self._build_reviewer_system_prompt()
        user_prompt = self._build_reviewer_user_prompt(ticket, context)
        return PromptContext(system_prompt=system_prompt, user_prompt=user_prompt)

    def post_process(
        self, llm_output: str, ticket: "Ticket", context: ExecutionContext
    ) -> dict[str, str]:
        """Parse LLM output into ReviewResult. Does not write files."""
        self._last_review_result = self._parse_review_output(llm_output)

        # Optionally write the review report as a deliverable
        if ticket.metadata.deliverable_paths:
            return {ticket.metadata.deliverable_paths[0]: llm_output}
        return {}

    def get_review_result(self) -> ReviewResult:
        """Return the parsed review result after execution."""
        if self._last_review_result is None:
            return ReviewResult(approved=False, feedback="No review result available")
        return self._last_review_result

    def _build_reviewer_system_prompt(self) -> str:
        return (
            "You are a quality reviewer agent. Your job is to evaluate "
            "deliverables produced by a specialist agent and determine if "
            "they meet the required quality bar.\n\n"
            "You MUST respond with a structured verdict:\n"
            "- Start with '## Verdict: APPROVED' or '## Verdict: REJECTED'\n"
            "- Include a '## Feedback' section with detailed explanation\n"
            "- If rejecting, include a '## Issues' section with bullet points\n"
        )

    def _build_reviewer_user_prompt(
        self, ticket: "Ticket", context: ExecutionContext
    ) -> str:
        """Build the user prompt with deliverables to review."""
        parts = [f"## Review Task\n\n{ticket.description}\n"]

        # Read deliverables from the specialist step if they exist
        for dep_path in ticket.metadata.input_dependencies:
            full_path = Path(context.working_directory) / dep_path
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8")
                parts.append(f"## File: {dep_path}\n\n```\n{content}\n```\n")

        return "\n".join(parts)

    def _parse_review_output(self, output: str) -> ReviewResult:
        """Parse structured review output from LLM."""
        approved = False
        feedback = output
        issues: list[str] = []

        # Check for verdict
        verdict_match = re.search(
            r"##\s*Verdict:\s*(APPROVED|REJECTED)", output, re.IGNORECASE
        )
        if verdict_match:
            approved = verdict_match.group(1).upper() == "APPROVED"

        # Extract feedback section
        feedback_match = re.search(
            r"##\s*Feedback\s*\n(.*?)(?=\n##|\Z)", output, re.DOTALL
        )
        if feedback_match:
            feedback = feedback_match.group(1).strip()

        # Extract issues
        issues_match = re.search(
            r"##\s*Issues\s*\n(.*?)(?=\n##|\Z)", output, re.DOTALL
        )
        if issues_match:
            issues_text = issues_match.group(1)
            issues = [
                line.lstrip("- ").strip()
                for line in issues_text.splitlines()
                if line.strip().startswith("-")
            ]

        return ReviewResult(
            approved=approved,
            feedback=feedback,
            issues=issues,
        )

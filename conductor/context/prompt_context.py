"""PromptContext model — assembled prompt ready for LLM consumption."""

from dataclasses import dataclass, field


@dataclass
class PromptContext:
    """Assembled prompt ready for LLM consumption."""

    system_prompt: str = ""
    user_prompt: str = ""
    total_tokens_estimate: int = 0
    source_files_included: list[str] = field(default_factory=list)

    def append_section(self, heading: str, content: str) -> None:
        """Append a named section to the user prompt."""
        self.user_prompt += f"\n\n## {heading}\n\n{content}"
        self._update_token_estimate()

    def _update_token_estimate(self) -> None:
        """Rough token estimate: chars / 4."""
        total_chars = len(self.system_prompt) + len(self.user_prompt)
        self.total_tokens_estimate = total_chars // 4

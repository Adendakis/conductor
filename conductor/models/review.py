"""Review result model for reviewer agents."""

from typing import Optional

from pydantic import BaseModel, Field


class ReviewResult(BaseModel):
    """Structured output from a reviewer agent."""

    approved: bool = False
    feedback: str = ""
    issues: list[str] = Field(default_factory=list)
    rework_target: Optional[str] = None
    confidence: float = 1.0

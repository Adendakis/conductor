"""Migration state for resume support."""

from pydantic import BaseModel, Field


class MigrationState(BaseModel):
    """Tracks watcher state for resume after restart."""

    last_poll_timestamp: str = ""
    active_ticket_ids: list[str] = Field(default_factory=list)
    completed_phases: list[str] = Field(default_factory=list)

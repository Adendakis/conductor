"""Ticket and metadata models."""

from typing import Optional

from pydantic import BaseModel, Field

from .enums import TicketStatus, TicketType


class TicketMetadata(BaseModel):
    """Mandatory metadata on every ticket. Stored as labels or custom fields."""

    phase: str = ""
    step: str = ""
    workpackage: Optional[str] = None
    pod: Optional[str] = None
    agent_name: str = ""
    prompt_file: str = ""
    working_directory: str = "."
    deliverable_paths: list[str] = Field(default_factory=list)
    git_tag_started: Optional[str] = None
    git_tag_completed: Optional[str] = None
    git_tag_approved: Optional[str] = None
    iteration: int = 1
    max_iterations: int = 3
    hitl_required: bool = True
    parent_ticket_id: Optional[str] = None
    rework_target_step: Optional[str] = None
    input_dependencies: list[str] = Field(default_factory=list)
    custom_validators: list[str] = Field(default_factory=list)


class Ticket(BaseModel):
    """A single ticket in the tracker."""

    id: str = ""
    title: str = ""
    description: str = ""
    status: TicketStatus = TicketStatus.BACKLOG
    ticket_type: TicketType = TicketType.TASK
    metadata: TicketMetadata = Field(default_factory=TicketMetadata)
    assignee: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

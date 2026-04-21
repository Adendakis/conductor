"""Enums for ticket status and type."""

from enum import Enum


class TicketStatus(str, Enum):
    """Status of a ticket in the tracker."""

    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"


class TicketType(str, Enum):
    """Type of ticket."""

    TASK = "task"
    REVIEWER_STEP = "reviewer_step"
    REMEDIATION = "remediation"
    GATE = "gate"


class PhaseStatus(str, Enum):
    """Aggregate status of a phase."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

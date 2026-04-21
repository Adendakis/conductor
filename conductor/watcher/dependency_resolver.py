"""Dependency resolution — unblock tickets when blockers complete."""

import logging

from conductor.models.enums import TicketStatus
from conductor.models.ticket import Ticket
from conductor.tracker.backend import TrackerBackend

logger = logging.getLogger(__name__)


def all_blockers_resolved(ticket: Ticket, tracker: TrackerBackend) -> bool:
    """Check if all tickets that block this one are DONE."""
    for blocker_id in ticket.blocked_by:
        try:
            blocker = tracker.get_ticket(blocker_id)
        except KeyError:
            # Blocker ticket doesn't exist — treat as unresolved
            logger.warning(f"Blocker ticket {blocker_id} not found for {ticket.id}")
            return False
        if blocker.status != TicketStatus.DONE:
            return False
    return True


def unblock_dependents(ticket: Ticket, tracker: TrackerBackend) -> list[str]:
    """Check all tickets blocked by this one. If all blockers done, set READY.

    Returns list of ticket IDs that were unblocked.
    """
    unblocked: list[str] = []

    for blocked_id in ticket.blocks:
        try:
            blocked = tracker.get_ticket(blocked_id)
        except KeyError:
            logger.warning(f"Dependent ticket {blocked_id} not found")
            continue

        # Only unblock tickets that are in BACKLOG
        if blocked.status != TicketStatus.BACKLOG:
            continue

        if all_blockers_resolved(blocked, tracker):
            tracker.update_status(blocked_id, TicketStatus.READY)
            unblocked.append(blocked_id)
            logger.info(f"Unblocked {blocked_id} (all blockers done)")
            print(f"  → Unblocked: {blocked_id} ({blocked.title})")

    return unblocked

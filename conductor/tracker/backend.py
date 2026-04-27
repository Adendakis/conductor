"""Abstract tracker backend interface."""

from abc import ABC, abstractmethod

from conductor.models.enums import TicketStatus
from conductor.models.ticket import Ticket, TicketMetadata


class TrackerBackend(ABC):
    """Abstract interface for any issue tracker.

    All tracker interactions go through this interface. Implementations
    exist for each supported tracker backend (SQLite, Vikunja, Gitea, etc.).
    """

    @abstractmethod
    def connect(self, config: dict) -> None:
        """Connect to the tracker (API key, URL, project ID, etc.)."""
        ...

    @abstractmethod
    def create_ticket(self, ticket: Ticket) -> str:
        """Create a ticket. Returns the tracker-assigned ID."""
        ...

    @abstractmethod
    def update_status(
        self, ticket_id: str, new_status: TicketStatus, changed_by: str = "watcher"
    ) -> None:
        """Transition a ticket to a new status."""
        ...

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> Ticket:
        """Get a ticket by ID."""
        ...

    @abstractmethod
    def get_tickets_by_status(self, status: TicketStatus) -> list[Ticket]:
        """Get all tickets with a given status."""
        ...

    @abstractmethod
    def get_tickets_by_metadata(self, **kwargs: str) -> list[Ticket]:
        """Query tickets by metadata fields (phase, workpackage, pod, etc.)."""
        ...

    @abstractmethod
    def add_comment(
        self, ticket_id: str, comment: str, author: str = "watcher"
    ) -> None:
        """Add a comment to a ticket."""
        ...

    @abstractmethod
    def update_metadata(
        self, ticket_id: str, metadata: TicketMetadata
    ) -> None:
        """Update ticket metadata fields."""
        ...

    @abstractmethod
    def get_changed_tickets(self, since_timestamp: str) -> list[Ticket]:
        """Get tickets that changed status since a given timestamp."""
        ...

    @abstractmethod
    def create_link(
        self, from_id: str, to_id: str, link_type: str = "blocks"
    ) -> None:
        """Create a dependency link between tickets."""
        ...

    @abstractmethod
    def delete_ticket(self, ticket_id: str) -> None:
        """Delete a ticket and all its associated data (comments, links, history)."""
        ...

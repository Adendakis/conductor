"""Integration tests for conductor core workflow."""

from conductor.board_initializer import initialize_board
from conductor.models.enums import TicketStatus
from conductor.watcher.dependency_resolver import all_blockers_resolved, unblock_dependents


def test_init_creates_tickets(tracker, git_manager):
    """Board init creates Phase 1 tickets in READY status."""
    ids = initialize_board(
        tracker=tracker,
        git=git_manager,
        pipeline_mode="minimal",
    )
    assert len(ids) == 1
    for ticket_id in ids:
        ticket = tracker.get_ticket(ticket_id)
        assert ticket.status == TicketStatus.READY


def test_dependency_chain(tracker):
    """Completing a blocker unblocks dependent tickets."""
    from conductor.models.ticket import Ticket, TicketMetadata
    from conductor.models.enums import TicketType

    # Create A (done) → B (backlog, blocked by A)
    a_id = tracker.create_ticket(Ticket(
        title="A", status=TicketStatus.DONE, ticket_type=TicketType.TASK,
        metadata=TicketMetadata(phase="test", step="a"),
    ))
    b_id = tracker.create_ticket(Ticket(
        title="B", status=TicketStatus.BACKLOG, ticket_type=TicketType.TASK,
        metadata=TicketMetadata(phase="test", step="b"),
        blocked_by=[a_id],
    ))

    # Unblock
    a = tracker.get_ticket(a_id)
    unblocked = unblock_dependents(a, tracker)
    assert b_id in unblocked

    b = tracker.get_ticket(b_id)
    assert b.status == TicketStatus.READY


def test_noop_executor(tracker, git_manager, registry, project_config, tmp_dir):
    """NoOp executor creates placeholder deliverables."""
    from conductor.models.ticket import Ticket, TicketMetadata
    from conductor.models.enums import TicketType
    from conductor.executor.base import ExecutionContext

    ticket = Ticket(
        id="TEST-001", title="Test",
        status=TicketStatus.READY, ticket_type=TicketType.TASK,
        metadata=TicketMetadata(
            phase="test", step="s1", agent_name="anything",
            deliverable_paths=["output/result.md"],
        ),
    )

    ctx = ExecutionContext(
        project_config=project_config,
        working_directory=tmp_dir,
        llm_provider=None,
        tracker=tracker,
        git=git_manager,
    )

    executor = registry.get("anything")  # falls back to NoOp
    result = executor.execute(ticket, ctx)
    assert result.success
    assert (tmp_dir / "output/result.md").exists()

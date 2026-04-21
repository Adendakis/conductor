"""Tests for async concurrent watcher."""

import asyncio
import time
from unittest.mock import patch

from conductor.board_initializer import initialize_board
from conductor.models.enums import TicketStatus
from conductor.watcher.async_watcher import AsyncEventWatcher


def test_async_watcher_dispatches_concurrently(
    tracker, git_manager, registry, project_config, watcher_config, tmp_dir
):
    """Multiple READY tickets are dispatched concurrently."""
    # Init with minimal pipeline (2 tickets, both READY)
    watcher_config.hitl_default = False
    watcher_config.max_concurrent_agents = 2

    ids = initialize_board(
        tracker=tracker, git=git_manager, pipeline_mode="minimal",
        working_directory=tmp_dir,
    )
    assert len(ids) == 2

    watcher = AsyncEventWatcher(
        tracker=tracker,
        registry=registry,
        git=git_manager,
        config=watcher_config,
        project_config=project_config,
    )
    watcher._semaphore = asyncio.Semaphore(watcher_config.max_concurrent_agents)

    # Run one poll cycle
    asyncio.run(watcher._poll_and_react())

    # COND-001 has hitl_after=False → DONE
    # COND-002 has hitl_after=True → AWAITING_REVIEW (ticket-level overrides config)
    t1 = tracker.get_ticket(ids[0])
    t2 = tracker.get_ticket(ids[1])
    assert t1.status == TicketStatus.DONE, f"{ids[0]} is {t1.status.value}"
    assert t2.status == TicketStatus.AWAITING_REVIEW, f"{ids[1]} is {t2.status.value}"


def test_semaphore_limits_concurrency(
    tracker, git_manager, registry, project_config, watcher_config, tmp_dir
):
    """Semaphore limits how many agents run at once."""
    from conductor.models.ticket import Ticket, TicketMetadata
    from conductor.models.enums import TicketType

    watcher_config.hitl_default = False
    watcher_config.max_concurrent_agents = 2

    # Create 4 READY tickets
    for i in range(4):
        tracker.create_ticket(Ticket(
            title=f"Task {i}",
            status=TicketStatus.READY,
            ticket_type=TicketType.TASK,
            metadata=TicketMetadata(
                phase="test", step=f"step_{i}", agent_name="slow_agent",
                deliverable_paths=[f"output/task_{i}.md"],
            ),
        ))

    # Track max concurrent executions
    max_concurrent = 0
    current_concurrent = 0

    original_execute = registry.get("slow_agent").execute

    def slow_execute(ticket, context):
        nonlocal max_concurrent, current_concurrent
        current_concurrent += 1
        max_concurrent = max(max_concurrent, current_concurrent)
        time.sleep(0.05)  # Simulate work
        result = original_execute(ticket, context)
        current_concurrent -= 1
        return result

    # Patch the executor
    executor = registry.get("slow_agent")
    executor.execute = slow_execute

    watcher = AsyncEventWatcher(
        tracker=tracker,
        registry=registry,
        git=git_manager,
        config=watcher_config,
        project_config=project_config,
    )
    watcher._semaphore = asyncio.Semaphore(2)

    asyncio.run(watcher._poll_and_react())

    # Max concurrent should not exceed semaphore value
    assert max_concurrent <= 2, f"Max concurrent was {max_concurrent}, expected <= 2"


def test_in_flight_prevents_double_dispatch(
    tracker, git_manager, registry, project_config, watcher_config, tmp_dir
):
    """Tickets already in-flight are not dispatched again."""
    from conductor.models.ticket import Ticket, TicketMetadata
    from conductor.models.enums import TicketType

    watcher_config.hitl_default = False

    tid = tracker.create_ticket(Ticket(
        title="Test",
        status=TicketStatus.READY,
        ticket_type=TicketType.TASK,
        metadata=TicketMetadata(
            phase="test", step="s1", agent_name="x",
            deliverable_paths=["output/x.md"],
        ),
    ))

    watcher = AsyncEventWatcher(
        tracker=tracker,
        registry=registry,
        git=git_manager,
        config=watcher_config,
        project_config=project_config,
    )

    # Simulate ticket already in-flight
    watcher._in_flight.add(tid)
    watcher._semaphore = asyncio.Semaphore(3)

    asyncio.run(watcher._poll_and_react())

    # Ticket should still be READY (not picked up again)
    t = tracker.get_ticket(tid)
    assert t.status == TicketStatus.READY

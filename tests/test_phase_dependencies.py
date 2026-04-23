"""Tests for inter-phase dependency wiring during board init."""

from conductor.board_initializer import initialize_board
from conductor.models.enums import TicketStatus
from conductor.models.phases import (
    DeliverableSpec,
    PhaseDefinition,
    QualityGateDefinition,
    StepDefinition,
)
from conductor.pipeline.builder import build_pipeline


def test_phase_depends_on_blocks_tickets(tracker, git_manager, tmp_dir):
    """Tickets in dependent phases start as BACKLOG, not READY."""
    # Create a simple 2-phase pipeline where phase_1 depends on phase_0
    # We need to use the "full" built-in pipeline and check that
    # phase_2 tickets are blocked by phase_1 tickets.
    # But the built-in pipeline uses creates_next_phases (progressive),
    # so let's test with a YAML pipeline instead.

    import yaml
    pipeline_yaml = tmp_dir / "pipeline.yaml"
    pipeline_yaml.write_text(yaml.dump({
        "pipeline": {
            "name": "test",
            "phases": [
                {
                    "id": "phase_0",
                    "name": "Setup",
                    "scope": "global",
                    "steps": [{
                        "id": "step_0",
                        "name": "Setup Step",
                        "agent": "noop",
                        "hitl_after": True,
                    }],
                },
                {
                    "id": "phase_1",
                    "name": "Analysis",
                    "scope": "global",
                    "depends_on": ["phase_0"],
                    "steps": [{
                        "id": "step_1",
                        "name": "Analyze",
                        "agent": "noop",
                        "hitl_after": True,
                    }],
                },
                {
                    "id": "phase_2",
                    "name": "Planning",
                    "scope": "global",
                    "depends_on": ["phase_1"],
                    "steps": [{
                        "id": "step_2",
                        "name": "Plan",
                        "agent": "noop",
                        "hitl_after": True,
                    }],
                },
            ],
        }
    }))

    config_dir = tmp_dir / ".conductor"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({
        "pipeline": "pipeline.yaml",
    }))

    ids = initialize_board(
        tracker=tracker,
        git=git_manager,
        pipeline_mode=f"yaml:{pipeline_yaml}",
        working_directory=tmp_dir,
    )

    assert len(ids) == 3

    # phase_0 ticket should be READY (no dependencies)
    t0 = tracker.get_ticket(ids[0])
    assert t0.status == TicketStatus.READY
    assert t0.metadata.phase == "phase_0"
    assert t0.blocked_by == []

    # phase_1 ticket should be BACKLOG (blocked by phase_0's last ticket)
    t1 = tracker.get_ticket(ids[1])
    assert t1.status == TicketStatus.BACKLOG
    assert t1.metadata.phase == "phase_1"
    assert ids[0] in t1.blocked_by

    # phase_2 ticket should be BACKLOG (blocked by phase_1's last ticket)
    t2 = tracker.get_ticket(ids[2])
    assert t2.status == TicketStatus.BACKLOG
    assert t2.metadata.phase == "phase_2"
    assert ids[1] in t2.blocked_by


def test_unblocking_chain(tracker, git_manager, tmp_dir):
    """Completing a phase unblocks the next phase's tickets."""
    import yaml
    from conductor.watcher.dependency_resolver import unblock_dependents

    pipeline_yaml = tmp_dir / "pipeline.yaml"
    pipeline_yaml.write_text(yaml.dump({
        "pipeline": {
            "name": "test",
            "phases": [
                {
                    "id": "p0",
                    "name": "First",
                    "scope": "global",
                    "steps": [{"id": "s0", "name": "S0", "agent": "x", "hitl_after": True}],
                },
                {
                    "id": "p1",
                    "name": "Second",
                    "scope": "global",
                    "depends_on": ["p0"],
                    "steps": [{"id": "s1", "name": "S1", "agent": "x", "hitl_after": True}],
                },
            ],
        }
    }))

    ids = initialize_board(
        tracker=tracker,
        git=git_manager,
        pipeline_mode=f"yaml:{pipeline_yaml}",
        working_directory=tmp_dir,
    )

    # s0 is READY, s1 is BACKLOG
    assert tracker.get_ticket(ids[0]).status == TicketStatus.READY
    assert tracker.get_ticket(ids[1]).status == TicketStatus.BACKLOG

    # Complete s0
    tracker.update_status(ids[0], TicketStatus.DONE)

    # Unblock dependents
    t0 = tracker.get_ticket(ids[0])
    unblocked = unblock_dependents(t0, tracker)

    # s1 should now be READY
    assert ids[1] in unblocked
    assert tracker.get_ticket(ids[1]).status == TicketStatus.READY

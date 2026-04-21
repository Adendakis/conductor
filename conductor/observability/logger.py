"""Structured JSON logger for machine-parseable event logging."""

import json
import logging
from datetime import datetime, timezone


class StructuredLogger:
    """JSON-structured logger for Conductor events."""

    def __init__(self, name: str = "conductor"):
        self.logger = logging.getLogger(name)

    def event(self, event_type: str, **kwargs) -> None:
        """Log a structured event."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **kwargs,
        }
        self.logger.info(json.dumps(record))

    def ticket_transition(
        self, ticket_id: str, from_status: str, to_status: str, **kwargs
    ) -> None:
        self.event(
            "ticket_transition",
            ticket_id=ticket_id,
            from_status=from_status,
            to_status=to_status,
            **kwargs,
        )

    def agent_started(self, ticket_id: str, agent_name: str, **kwargs) -> None:
        self.event(
            "agent_started",
            ticket_id=ticket_id,
            agent_name=agent_name,
            **kwargs,
        )

    def agent_completed(
        self,
        ticket_id: str,
        agent_name: str,
        elapsed_seconds: float = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0,
        **kwargs,
    ) -> None:
        self.event(
            "agent_completed",
            ticket_id=ticket_id,
            agent_name=agent_name,
            elapsed_seconds=elapsed_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            **kwargs,
        )

    def deliverable_validated(
        self, ticket_id: str, passed: bool, errors: int = 0, warnings: int = 0
    ) -> None:
        self.event(
            "deliverable_validated",
            ticket_id=ticket_id,
            passed=passed,
            errors=errors,
            warnings=warnings,
        )

    def hitl_waiting(self, ticket_id: str, step_id: str) -> None:
        self.event("hitl_waiting", ticket_id=ticket_id, step_id=step_id)

    def rework_triggered(
        self, ticket_id: str, iteration: int, target_step: str
    ) -> None:
        self.event(
            "rework_triggered",
            ticket_id=ticket_id,
            iteration=iteration,
            target_step=target_step,
        )

    def phase_completed(self, phase_id: str, scope_id: str = "") -> None:
        self.event("phase_completed", phase_id=phase_id, scope_id=scope_id)

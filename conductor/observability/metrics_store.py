"""Metrics storage and aggregation."""

import json
import sqlite3
from pathlib import Path
from typing import Optional

from conductor.models.metrics import StepMetrics


class MetricsStore:
    """Stores per-step execution metrics for reporting and cost analysis.

    Backed by the step_metrics table in the SQLite tracker database.
    """

    def __init__(self, db_path: str = ".conductor/tracker.db"):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, ticket_id: str, metrics: StepMetrics) -> None:
        """Store metrics for a completed step."""
        conn = self._conn()
        conn.execute(
            """INSERT INTO step_metrics (
                ticket_id, step_id, agent_name, model_id,
                input_tokens, output_tokens, cache_write_tokens, cache_read_tokens,
                requests, elapsed_seconds, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticket_id, metrics.step_id, metrics.agent_name, metrics.model_id,
                metrics.input_tokens, metrics.output_tokens,
                metrics.cache_write_tokens, metrics.cache_read_tokens,
                metrics.requests, metrics.elapsed_seconds, metrics.cost_usd,
            ),
        )
        conn.commit()
        conn.close()

    def get_phase_summary(self, phase_id: str) -> dict:
        """Aggregate metrics for a phase."""
        conn = self._conn()
        row = conn.execute(
            """SELECT
                COUNT(*) as steps,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(cost_usd) as total_cost,
                SUM(elapsed_seconds) as total_elapsed,
                SUM(requests) as total_requests
            FROM step_metrics sm
            JOIN tickets t ON sm.ticket_id = t.id
            WHERE t.phase = ?""",
            (phase_id,),
        ).fetchone()
        conn.close()

        if not row or row["steps"] == 0:
            return {"phase_id": phase_id, "steps": 0}

        return {
            "phase_id": phase_id,
            "steps": row["steps"],
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
            "total_cost_usd": row["total_cost"] or 0.0,
            "total_elapsed_seconds": row["total_elapsed"] or 0.0,
            "total_requests": row["total_requests"] or 0,
        }

    def get_project_summary(self) -> dict:
        """Aggregate metrics for the entire project."""
        conn = self._conn()
        row = conn.execute(
            """SELECT
                COUNT(*) as steps,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(cost_usd) as total_cost,
                SUM(elapsed_seconds) as total_elapsed,
                SUM(requests) as total_requests
            FROM step_metrics"""
        ).fetchone()
        conn.close()

        if not row or row["steps"] == 0:
            return {"steps": 0}

        return {
            "steps": row["steps"],
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
            "total_cost_usd": row["total_cost"] or 0.0,
            "total_elapsed_seconds": row["total_elapsed"] or 0.0,
            "total_requests": row["total_requests"] or 0,
        }

    def get_cost_breakdown(self) -> list[dict]:
        """Cost breakdown by agent."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT
                agent_name,
                model_id,
                COUNT(*) as executions,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cost_usd) as total_cost,
                SUM(elapsed_seconds) as total_elapsed
            FROM step_metrics
            GROUP BY agent_name, model_id
            ORDER BY total_cost DESC"""
        ).fetchall()
        conn.close()

        return [
            {
                "agent_name": r["agent_name"],
                "model_id": r["model_id"],
                "executions": r["executions"],
                "total_input_tokens": r["total_input"] or 0,
                "total_output_tokens": r["total_output"] or 0,
                "total_cost_usd": r["total_cost"] or 0.0,
                "total_elapsed_seconds": r["total_elapsed"] or 0.0,
            }
            for r in rows
        ]

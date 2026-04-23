"""SQLite-backed tracker implementation."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from conductor.models.enums import TicketStatus, TicketType
from conductor.models.ticket import Ticket, TicketMetadata

from .backend import TrackerBackend

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'backlog',
    ticket_type TEXT NOT NULL DEFAULT 'task',
    assignee TEXT,
    phase TEXT,
    step TEXT,
    workpackage TEXT,
    pod TEXT,
    agent_name TEXT,
    prompt_file TEXT,
    working_directory TEXT DEFAULT '.',
    deliverable_paths TEXT,
    git_tag_started TEXT,
    git_tag_completed TEXT,
    git_tag_approved TEXT,
    iteration INTEGER DEFAULT 1,
    max_iterations INTEGER DEFAULT 3,
    hitl_required INTEGER DEFAULT 1,
    parent_ticket_id TEXT,
    rework_target_step TEXT,
    input_dependencies TEXT,
    custom_validators TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ticket_links (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    link_type TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, link_type)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    author TEXT,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

CREATE TABLE IF NOT EXISTS step_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT,
    step_id TEXT,
    agent_name TEXT,
    model_id TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    requests INTEGER DEFAULT 0,
    elapsed_seconds REAL DEFAULT 0.0,
    cost_usd REAL DEFAULT 0.0,
    recorded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_phase ON tickets(phase);
CREATE INDEX IF NOT EXISTS idx_tickets_workpackage ON tickets(workpackage);
CREATE INDEX IF NOT EXISTS idx_tickets_pod ON tickets(pod);
CREATE INDEX IF NOT EXISTS idx_status_history_ticket ON status_history(ticket_id);
CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);
"""


class SqliteTracker(TrackerBackend):
    """Minimal issue tracker backed by SQLite."""

    def __init__(self, db_path: str = ".conductor/tracker.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._next_id_lock = threading.Lock()

    @property
    def _conn(self) -> sqlite3.Connection:
        """Get a connection. Creates a new one per thread.

        Uses isolation_level=None (autocommit) to ensure reads always
        see the latest data from other processes (watcher, CLI).
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def connect(self, config: dict) -> None:
        """Create tables if they don't exist."""
        if "db_path" in config:
            self.db_path = config["db_path"]
            self._local.conn = None  # Reset connection
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _next_id(self) -> str:
        """Generate the next sequential ticket ID."""
        with self._next_id_lock:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM tickets"
            ).fetchone()
            seq = row["cnt"] + 1
            return f"COND-{seq:03d}"

    def create_ticket(self, ticket: Ticket) -> str:
        ticket_id = ticket.id if ticket.id else self._next_id()
        now = datetime.now(timezone.utc).isoformat()
        meta = ticket.metadata

        self._conn.execute(
            """INSERT INTO tickets (
                id, title, description, status, ticket_type, assignee,
                phase, step, workpackage, pod, agent_name, prompt_file,
                working_directory, deliverable_paths,
                git_tag_started, git_tag_completed, git_tag_approved,
                iteration, max_iterations, hitl_required, parent_ticket_id,
                rework_target_step, input_dependencies, custom_validators,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?
            )""",
            (
                ticket_id, ticket.title, ticket.description,
                ticket.status.value, ticket.ticket_type.value, ticket.assignee,
                meta.phase, meta.step, meta.workpackage, meta.pod,
                meta.agent_name, meta.prompt_file,
                meta.working_directory, json.dumps(meta.deliverable_paths),
                meta.git_tag_started, meta.git_tag_completed, meta.git_tag_approved,
                meta.iteration, meta.max_iterations,
                1 if meta.hitl_required else 0, meta.parent_ticket_id,
                meta.rework_target_step,
                json.dumps(meta.input_dependencies),
                json.dumps(meta.custom_validators),
                now, now,
            ),
        )

        # Create dependency links from blocked_by
        for blocker_id in ticket.blocked_by:
            self._conn.execute(
                "INSERT OR IGNORE INTO ticket_links (from_id, to_id, link_type) VALUES (?, ?, ?)",
                (blocker_id, ticket_id, "blocks"),
            )

        # Record initial status
        self._conn.execute(
            "INSERT INTO status_history (ticket_id, old_status, new_status, changed_by) VALUES (?, ?, ?, ?)",
            (ticket_id, None, ticket.status.value, "init"),
        )

        self._conn.commit()
        return ticket_id

    def update_status(
        self, ticket_id: str, new_status: TicketStatus, changed_by: str = "watcher"
    ) -> None:
        row = self._conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Ticket {ticket_id} not found")

        old_status = row["status"]
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (new_status.value, now, ticket_id),
        )
        self._conn.execute(
            "INSERT INTO status_history (ticket_id, old_status, new_status, changed_by) VALUES (?, ?, ?, ?)",
            (ticket_id, old_status, new_status.value, changed_by),
        )
        self._conn.commit()

    def get_ticket(self, ticket_id: str) -> Ticket:
        row = self._conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Ticket {ticket_id} not found")
        return self._row_to_ticket(row)

    def get_tickets_by_status(self, status: TicketStatus) -> list[Ticket]:
        rows = self._conn.execute(
            "SELECT * FROM tickets WHERE status = ?", (status.value,)
        ).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def get_tickets_by_metadata(self, **kwargs: str) -> list[Ticket]:
        conditions = []
        params: list[str] = []
        allowed_fields = {
            "phase", "step", "workpackage", "pod", "agent_name", "status",
        }
        for key, value in kwargs.items():
            if key in allowed_fields:
                conditions.append(f"{key} = ?")
                params.append(value)

        if not conditions:
            return []

        query = "SELECT * FROM tickets WHERE " + " AND ".join(conditions)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def add_comment(
        self, ticket_id: str, comment: str, author: str = "watcher"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO comments (ticket_id, author, content) VALUES (?, ?, ?)",
            (ticket_id, author, comment),
        )
        self._conn.execute(
            "UPDATE tickets SET updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
        self._conn.commit()

    def update_metadata(self, ticket_id: str, metadata: TicketMetadata) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE tickets SET
                phase = ?, step = ?, workpackage = ?, pod = ?,
                agent_name = ?, prompt_file = ?, working_directory = ?,
                deliverable_paths = ?,
                git_tag_started = ?, git_tag_completed = ?, git_tag_approved = ?,
                iteration = ?, max_iterations = ?, hitl_required = ?,
                parent_ticket_id = ?, rework_target_step = ?,
                input_dependencies = ?, custom_validators = ?,
                updated_at = ?
            WHERE id = ?""",
            (
                metadata.phase, metadata.step, metadata.workpackage, metadata.pod,
                metadata.agent_name, metadata.prompt_file, metadata.working_directory,
                json.dumps(metadata.deliverable_paths),
                metadata.git_tag_started, metadata.git_tag_completed,
                metadata.git_tag_approved,
                metadata.iteration, metadata.max_iterations,
                1 if metadata.hitl_required else 0,
                metadata.parent_ticket_id, metadata.rework_target_step,
                json.dumps(metadata.input_dependencies),
                json.dumps(metadata.custom_validators),
                now, ticket_id,
            ),
        )
        self._conn.commit()

    def get_changed_tickets(self, since_timestamp: str) -> list[Ticket]:
        rows = self._conn.execute(
            """SELECT DISTINCT t.* FROM tickets t
               JOIN status_history sh ON t.id = sh.ticket_id
               WHERE sh.changed_at > ?
               ORDER BY sh.changed_at""",
            (since_timestamp,),
        ).fetchall()
        return [self._row_to_ticket(r) for r in rows]

    def create_link(
        self, from_id: str, to_id: str, link_type: str = "blocks"
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO ticket_links (from_id, to_id, link_type) VALUES (?, ?, ?)",
            (from_id, to_id, link_type),
        )
        self._conn.commit()

    # --- Internal helpers ---

    def _row_to_ticket(self, row: sqlite3.Row) -> Ticket:
        """Convert a database row to a Ticket model."""
        ticket_id = row["id"]

        # Load blocked_by and blocks from links
        blocked_by = [
            r["from_id"]
            for r in self._conn.execute(
                "SELECT from_id FROM ticket_links WHERE to_id = ? AND link_type = 'blocks'",
                (ticket_id,),
            ).fetchall()
        ]
        blocks = [
            r["to_id"]
            for r in self._conn.execute(
                "SELECT to_id FROM ticket_links WHERE from_id = ? AND link_type = 'blocks'",
                (ticket_id,),
            ).fetchall()
        ]

        # Load comments
        comments = [
            r["content"]
            for r in self._conn.execute(
                "SELECT content FROM comments WHERE ticket_id = ? ORDER BY created_at",
                (ticket_id,),
            ).fetchall()
        ]

        def _parse_json_list(val: Optional[str]) -> list[str]:
            if not val:
                return []
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return []

        metadata = TicketMetadata(
            phase=row["phase"] or "",
            step=row["step"] or "",
            workpackage=row["workpackage"],
            pod=row["pod"],
            agent_name=row["agent_name"] or "",
            prompt_file=row["prompt_file"] or "",
            working_directory=row["working_directory"] or ".",
            deliverable_paths=_parse_json_list(row["deliverable_paths"]),
            git_tag_started=row["git_tag_started"],
            git_tag_completed=row["git_tag_completed"],
            git_tag_approved=row["git_tag_approved"],
            iteration=row["iteration"] or 1,
            max_iterations=row["max_iterations"] or 3,
            hitl_required=bool(row["hitl_required"]),
            parent_ticket_id=row["parent_ticket_id"],
            rework_target_step=row["rework_target_step"],
            input_dependencies=_parse_json_list(row["input_dependencies"]),
            custom_validators=_parse_json_list(row["custom_validators"]),
        )

        return Ticket(
            id=ticket_id,
            title=row["title"] or "",
            description=row["description"] or "",
            status=TicketStatus(row["status"]),
            ticket_type=TicketType(row["ticket_type"]),
            metadata=metadata,
            assignee=row["assignee"],
            blocked_by=blocked_by,
            blocks=blocks,
            comments=comments,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

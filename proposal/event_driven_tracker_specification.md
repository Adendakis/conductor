# Event-Driven Tracker Orchestration — Technical Specification

## For implementation by a developer or AI coding agent

**Version**: 1.0
**Date**: 2026-04-18
**Status**: Specification
**Purpose**: Enable an AI or developer to build the event-driven orchestration system
for ACM using an issue tracker as the control plane

---

## 1. System Overview

### 1.1 What This System Does

An event loop watches an issue tracker board. When a ticket changes status, the
system reacts: delegates work to an agent, validates deliverables, unblocks
dependent tickets, or creates remediation tickets. There is no central orchestrator.
The board IS the orchestrator. Agents react to events. Humans steer by moving tickets.

### 1.2 Core Components

```
┌─────────────────────────────────────────────────┐
│                 TRACKER                          │
│  (Gitea, Jira, GitHub Issues, or built-in)      │
│                                                  │
│  Projects → Milestones (Phases) → Issues (Tasks) │
│  Labels, Assignees, Status, Metadata             │
└──────────────────┬──────────────────────────────┘
                   │ poll / webhook
                   ▼
┌─────────────────────────────────────────────────┐
│              EVENT WATCHER                       │
│                                                  │
│  Stateless Python process                        │
│  Polls tracker every N seconds (or webhook)      │
│  Reacts to status transitions                    │
│  Delegates to Agent Executor                     │
│  Updates tracker on completion                   │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│            AGENT EXECUTOR                        │
│                                                  │
│  Resolves ticket metadata → agent + prompt       │
│  Invokes agent (Kiro CLI / Pydantic AI / Bedrock)│
│  Runs deliverable validation                     │
│  Reports result back to Event Watcher            │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│              GIT MANAGER                         │
│                                                  │
│  Creates tags on ticket transitions              │
│  Commits deliverables after agent completion     │
│  Manages worktree lifecycle for pods             │
└─────────────────────────────────────────────────┘
```

### 1.3 Design Principles

1. **Stateless watcher**: If it crashes, restart it. It reads board state and resumes.
2. **Tracker is source of truth**: No `.migration_state.json`. The board IS the state.
3. **Git tags are checkpoints**: Every transition creates a tag. Any point is restorable.
4. **Human-in-the-loop = ticket transition**: Approve, reject, pause — all via the tracker.
5. **Tracker-agnostic**: An abstraction layer allows swapping trackers without changing logic.

---

## 2. Tracker Abstraction Layer

### 2.1 Interface Definition

All tracker interactions go through a single abstract interface. Implementations
exist for each supported tracker backend.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TicketStatus(str, Enum):
    BACKLOG = "backlog"              # Created but not ready (dependencies unmet)
    READY = "ready"                  # All dependencies met, waiting for agent
    IN_PROGRESS = "in_progress"      # Agent is working on it
    AWAITING_REVIEW = "awaiting_review"  # Agent done, awaiting human review
    APPROVED = "approved"            # Human approved deliverables
    REJECTED = "rejected"            # Human rejected, needs rework
    DONE = "done"                    # Fully complete (approved + post-processing done)
    FAILED = "failed"                # Agent failed, needs investigation
    PAUSED = "paused"                # Human paused, no action until resumed


class TicketType(str, Enum):
    TASK = "task"                    # Agent work (analysis, extraction, generation, etc.)
    REVIEWER_STEP = "reviewer_step"  # Review step (agent reviewer validates deliverables)
    REMEDIATION = "remediation"      # Rework after rejection
    GATE = "gate"                    # Quality gate (deterministic check, no agent)


@dataclass
class TicketMetadata:
    """Mandatory metadata on every ticket. Stored as labels or custom fields."""
    phase: str                          # e.g., "phase-1", "phase-3.2"
    step: str                           # e.g., "step-1.1-db-analysis"
    workpackage: Optional[str] = None   # e.g., "WP-001" (None for cross-WP steps)
    pod: Optional[str] = None           # e.g., "pod-a" (None for non-pod steps)
    agent_name: str = ""                # e.g., "analysis_specialist_database"
    prompt_file: str = ""               # e.g., "prompts/01_analysis/Database/01_analysis.md"
    working_directory: str = "."        # Git worktree path for pod-scoped work
    deliverable_paths: list[str] = field(default_factory=list)  # Expected output paths
    git_tag_started: Optional[str] = None    # Set by watcher when agent starts
    git_tag_completed: Optional[str] = None  # Set by watcher when agent completes
    git_tag_approved: Optional[str] = None   # Set by watcher when human approves
    iteration: int = 1                  # Rework iteration count
    max_iterations: int = 3             # Max rework before escalation
    hitl_required: bool = True          # Whether human review is required after agent
    parent_ticket_id: Optional[str] = None  # For remediation tickets


@dataclass
class Ticket:
    """A single ticket in the tracker."""
    id: str                             # Tracker-assigned ID (e.g., "ACM-42")
    title: str                          # Human-readable title
    description: str                    # Markdown content (prompt context, instructions)
    status: TicketStatus
    ticket_type: TicketType
    metadata: TicketMetadata
    assignee: Optional[str] = None      # Agent name or "human"
    blocked_by: list[str] = field(default_factory=list)  # Ticket IDs this depends on
    blocks: list[str] = field(default_factory=list)       # Ticket IDs that depend on this
    comments: list[str] = field(default_factory=list)     # Rejection feedback, agent logs
    created_at: str = ""
    updated_at: str = ""


class TrackerBackend(ABC):
    """Abstract interface for any issue tracker."""

    @abstractmethod
    def connect(self, config: dict) -> None:
        """Connect to the tracker (API key, URL, project ID, etc.)."""
        ...

    @abstractmethod
    def create_ticket(self, ticket: Ticket) -> str:
        """Create a ticket. Returns the tracker-assigned ID."""
        ...

    @abstractmethod
    def update_status(self, ticket_id: str, new_status: TicketStatus) -> None:
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
    def get_tickets_by_metadata(self, **kwargs) -> list[Ticket]:
        """Query tickets by metadata fields (phase, workpackage, pod, etc.)."""
        ...

    @abstractmethod
    def add_comment(self, ticket_id: str, comment: str) -> None:
        """Add a comment to a ticket (agent log, rejection feedback, etc.)."""
        ...

    @abstractmethod
    def update_metadata(self, ticket_id: str, metadata: TicketMetadata) -> None:
        """Update ticket metadata fields."""
        ...

    @abstractmethod
    def get_changed_tickets(self, since_timestamp: str) -> list[Ticket]:
        """Get tickets that changed status since a given timestamp. For polling."""
        ...

    @abstractmethod
    def create_link(self, from_id: str, to_id: str, link_type: str) -> None:
        """Create a dependency link between tickets (blocks/blocked-by)."""
        ...
```

### 2.2 Built-In Tracker (Default — SQLite-Based)

For simplicity and zero external dependencies, the default tracker is a local
SQLite database with a minimal web UI. This is the recommended starting point.

```python
class SqliteTracker(TrackerBackend):
    """
    Minimal issue tracker backed by SQLite.
    No external dependencies. Runs locally.
    Provides a simple web UI via Flask/FastAPI for human interaction.
    """

    def __init__(self, db_path: str = ".acm/tracker.db"):
        self.db_path = db_path

    def connect(self, config: dict) -> None:
        # Create tables if not exist
        # tickets, comments, links, status_history
        ...
```

**SQLite Schema**:

```sql
CREATE TABLE tickets (
    id TEXT PRIMARY KEY,                    -- ACM-001, ACM-002, ...
    title TEXT NOT NULL,
    description TEXT,                       -- Markdown content
    status TEXT NOT NULL DEFAULT 'backlog', -- TicketStatus enum value
    ticket_type TEXT NOT NULL DEFAULT 'task',
    assignee TEXT,
    -- Metadata fields (denormalized for query speed)
    phase TEXT,
    step TEXT,
    workpackage TEXT,
    pod TEXT,
    agent_name TEXT,
    prompt_file TEXT,
    working_directory TEXT DEFAULT '.',
    deliverable_paths TEXT,                 -- JSON array
    git_tag_started TEXT,
    git_tag_completed TEXT,
    git_tag_approved TEXT,
    iteration INTEGER DEFAULT 1,
    max_iterations INTEGER DEFAULT 3,
    hitl_required INTEGER DEFAULT 1,        -- boolean
    parent_ticket_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE ticket_links (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    link_type TEXT NOT NULL,                -- 'blocks', 'blocked_by', 'parent'
    PRIMARY KEY (from_id, to_id, link_type)
);

CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    author TEXT,                             -- agent name or 'human'
    content TEXT NOT NULL,                   -- Markdown
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

CREATE TABLE status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,                         -- 'watcher', 'human', agent name
    changed_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES tickets(id)
);

CREATE INDEX idx_tickets_status ON tickets(status);
CREATE INDEX idx_tickets_phase ON tickets(phase);
CREATE INDEX idx_tickets_workpackage ON tickets(workpackage);
CREATE INDEX idx_tickets_pod ON tickets(pod);
CREATE INDEX idx_status_history_ticket ON status_history(ticket_id);
```

### 2.3 Vikunja Tracker Backend (Recommended Self-Hosted)

Vikunja is the recommended self-hosted tracker. It provides the richest feature set
for the ACM use case with minimal setup effort.

**Why Vikunja is recommended**:
- Single Go binary or Docker container — deploys in minutes
- Built-in Kanban board, list, table, and Gantt views — no UI to build
- REST API for all operations (tasks, labels, projects, comments)
- Webhooks — fires on task created, updated, completed, commented (replaces polling)
- Labels for metadata encoding (phase, workpackage, pod, agent)
- Task assignees, priorities, comments (markdown), descriptions
- Multiple views per project with independent filters
- MCP server exists ([vikunja-mcp](https://github.com/democratize-technology/vikunja-mcp)) for direct AI agent interaction
- Open source (AGPL), self-hosted, EU-based
- `pip install` not needed — standalone binary

**Metadata encoding convention**:
- Labels: `phase:phase-1`, `wp:WP-001`, `pod:pod-a`, `agent:analysis_specialist_database`
- Status: Kanban columns map to TicketStatus (Backlog, Ready, In Progress, Review, etc.)
- Task description: YAML frontmatter block with structured metadata
- Dependencies: Vikunja task relations (requires Vikunja 1.0+) or label convention `blocked-by:ACM-042`

```python
class VikunjaTracker(TrackerBackend):
    """
    Vikunja task management backend.
    Uses Vikunja REST API.
    Maps: Projects → Migration Project, Tasks → Tickets, Labels → Metadata.
    Kanban columns → TicketStatus.
    """

    def __init__(self, base_url: str, token: str, project_id: int):
        self.base_url = base_url  # e.g., "http://localhost:3456/api/v1"
        self.token = token
        self.project_id = project_id

    # Webhook setup: POST {base_url}/projects/{id}/webhooks
    # Events: task.created, task.updated, task.deleted, task.comment.created
    # Labels encode ACM metadata: "phase:phase-3.2", "wp:WP-001"
    # Kanban view columns = status buckets
    # Task description = markdown with YAML frontmatter for structured metadata
```

**Vikunja limitations for ACM**:
- No native "blocked by" task dependency (use label convention or task relations in v1.0+)
- No native "epic" concept (use sub-projects or parent tasks)
- Custom fields must be encoded as labels or description frontmatter
- No status workflow enforcement (any user can move to any column)

### 2.4 Roundup Tracker Backend (Python-Native)

Roundup is a Python-native issue tracker with a fully customizable schema. It is
the most flexible option if deep Python integration is needed.

**Why Roundup may be useful**:
- Written in Python — same language as ACM, can be embedded or extended directly
- Fully customizable database schema — add `phase`, `workpackage`, `pod`, `agent_name`
  as native fields (not label hacks)
- REST API with JWT authentication
- Auditors and reactors — Python hooks that fire before/after database changes
  (this IS the event system — no polling or webhooks needed)
- SQLite, MySQL, or PostgreSQL backends
- Email integration (issues can be created/updated via email)
- Fine-grained authorization (ABAC, RBAC)
- `pip install roundup` — pure Python, no Go/Docker required
- Used by Python.org itself for CPython bug tracking

**Metadata encoding**:
- Native custom fields: `phase`, `workpackage`, `pod`, `agent_name`, `prompt_file`,
  `working_directory`, `git_tag_started`, `git_tag_completed` — all as first-class
  schema properties, not label hacks
- Status: Native workflow states with configurable transitions
- Dependencies: Native `depends` and `blocks` link types

```python
class RoundupTracker(TrackerBackend):
    """
    Roundup issue tracker backend.
    Uses Roundup REST API or direct Python API.
    Maps: Classes → Ticket types, Properties → Metadata fields.
    Auditors/Reactors → Event handlers (replaces polling entirely).
    """

    def __init__(self, base_url: str, token: str, tracker_home: str = None):
        self.base_url = base_url  # e.g., "http://localhost:8080/rest"
        self.token = token
        self.tracker_home = tracker_home  # For direct Python API access

    # Schema customization: add phase, workpackage, pod as native properties
    # Auditors: fire BEFORE a change (can validate/reject)
    # Reactors: fire AFTER a change (can trigger agent execution)
    # Direct Python API: roundup.instance.open(tracker_home) for in-process access
    # REST API: /rest/data/issue/{id} with JWT auth
```

**Roundup advantages over other trackers**:
- Auditors/reactors replace the entire polling/webhook mechanism — the event watcher
  can be implemented as a Roundup reactor that fires when a ticket status changes
- Native custom fields mean no label encoding hacks
- Native `depends`/`blocks` links for dependency management
- Python API means the watcher can run in-process with the tracker (zero network overhead)

**Roundup limitations for ACM**:
- No built-in Kanban board view (web UI is traditional issue list/detail)
- Web UI is functional but dated — not as polished as Vikunja or Gitea
- Smaller community than Vikunja or Gitea
- Setup is more involved (schema customization, template configuration)
- No MCP server available

### 2.5 Gitea Tracker Backend

```python
class GiteaTracker(TrackerBackend):
    """
    Gitea issue tracker backend.
    Uses Gitea REST API v1.
    Maps: Milestones → Phases, Issues → Tickets, Labels → Metadata.
    """

    def __init__(self, base_url: str, token: str, repo_owner: str, repo_name: str):
        self.base_url = base_url
        self.token = token
        self.repo = f"{repo_owner}/{repo_name}"

    # Label convention: "phase:phase-1", "wp:WP-001", "pod:pod-a", "agent:analysis_specialist"
    # Status mapped to Gitea labels: "status:ready", "status:in-progress", etc.
    # Or use Gitea Projects (kanban boards) with columns as statuses
```

### 2.6 Jira Tracker Backend

```python
class JiraTracker(TrackerBackend):
    """
    Jira Cloud/Server backend.
    Maps: Epics → Phases, Stories → Tickets, Custom Fields → Metadata.
    """

    def __init__(self, base_url: str, email: str, api_token: str, project_key: str):
        ...
    # Uses Jira REST API v3
    # Custom fields for phase, workpackage, pod, agent_name, prompt_file
    # Transitions mapped to TicketStatus
```

### 2.7 GitHub Issues Backend

```python
class GitHubTracker(TrackerBackend):
    """
    GitHub Issues backend.
    Maps: Milestones → Phases, Issues → Tickets, Labels → Metadata.
    """
    # Uses GitHub REST API or GraphQL
    # Labels for metadata: "phase:1", "wp:WP-001", "status:ready"
    # GitHub Projects (v2) for board view
```

---

## 3. Board Initialization

### 3.1 The `acm init` Command

```
acm init --project carddemo --tracker sqlite [--tracker-config config.json]
```

This command reads the project configuration and creates tickets only for the
phases that are knowable at initialization time. Later phases (per-workpackage,
per-pod) are created dynamically as earlier phases complete and produce the
information needed to define them.

### 3.2 Progressive Ticket Creation

The board is NOT populated with all tickets upfront. Tickets are created progressively
as the migration advances and new information becomes available.

**At `acm init` time** (knowable without any analysis):
- Phase 0: Project preparation / configuration (optional)
- Phase 1.1: Database Analysis
- Phase 1.2: Source Code Analysis

**After Phase 1 completes** (analysis outputs available):
- Phase 2.1: Workpackage Definition

**After Phase 2.1 completes** (workpackages defined):
- Phase 2.5: Pod Partitioning

**After Phase 2.5 completes** (pods and WP assignments known):
- Phase 3.x tickets: one per step × workpackage (dynamically created)
- Phase 4.x tickets: one per step × workpackage
- Phase 5.x tickets: one per step × workpackage
- Phase 6: Integration testing

This means the watcher has a dual role:
1. **React to status changes** (pick up READY tickets, handle approvals/rejections)
2. **Create new tickets** when milestone phases complete (Phase 1 → create Phase 2,
   Phase 2.5 → create all per-WP tickets for Phases 3-5)

```python
def handle_milestone_completion(self, ticket: Ticket):
    """
    When certain milestone tickets complete, create the next batch of tickets.
    Called after a ticket transitions to DONE.
    """
    if ticket.metadata.step == "step-1.2-source-analysis":
        # Phase 1 complete → create Phase 2.1
        self.create_phase_2_tickets()

    elif ticket.metadata.step == "step-2.1-workpackage-definition":
        # Workpackages defined → create Phase 2.5
        self.create_phase_2_5_tickets()

    elif ticket.metadata.step == "step-2.5-pod-partitioning":
        # Pods assigned → create all per-WP tickets for Phases 3-6
        workpackages = self.load_workpackage_planning()
        pod_assignment = self.load_pod_assignment()
        self.create_per_workpackage_tickets(workpackages, pod_assignment)
```

### 3.3 Alternative: Config-Driven Initialization

If the project configuration already contains workpackage definitions (e.g., from
a previous run or manual planning), `acm init` can create all known tickets upfront:

```
acm init --project carddemo --tracker sqlite --workpackages output/analysis/workpackages/Workpackage_Planning.json
```

This skips the progressive creation and populates the board with all tickets
immediately. Useful for re-initializing a board after a partial migration or
for testing.

### 3.4 Initialization Process (Minimal — Phase 1 Only)

```python
def initialize_board(project_config: ProjectConfig, tracker: TrackerBackend):
    """
    Create initial tickets for the migration.
    Only creates tickets for phases that are knowable at init time.
    """

    # Phase 1 tickets (always knowable)
    tickets = [
        create_ticket(
            phase="phase-1", step="step-1.1-db-analysis",
            title="Phase 1.1: Database Analysis",
            agent_name="analysis_specialist_database",
            prompt_file="prompts/01_analysis/Database/01_analysis.md",
        ),
        create_ticket(
            phase="phase-1", step="step-1.2-source-analysis",
            title="Phase 1.2: Source Code Analysis",
            agent_name="analysis_specialist_legacy_code",
            prompt_file="prompts/01_analysis/Sourcecode/01_generate_cobol_analysis_tool.md",
            blocked_by=["step-1.1-db-analysis"],  # Optional: can run in parallel
        ),
    ]

    for ticket in tickets:
        tracker.create_ticket(ticket)

    # Set Phase 1 tickets to READY (no dependencies or dependencies met)
    for ticket in tickets:
        if not ticket.blocked_by:
            tracker.update_status(ticket.id, TicketStatus.READY)

    # Git tag
    git_tag("acm/initialized")
```

### 3.3 Ticket Template per Phase

| Phase | Ticket Granularity | Example Title | Agent |
|-------|-------------------|---------------|-------|
| 1.1 | Per step | "Phase 1.1: Database Analysis" | analysis_specialist_database |
| 1.2 | Per step | "Phase 1.2: Source Code Analysis" | analysis_specialist_legacy_code |
| 2.1 | Per step | "Phase 2.1: Workpackage Definition" | planning_specialist_workpackage |
| 2.5 | Per step | "Phase 2.5: Pod Partitioning" | planning_specialist_workpackage |
| 3.0 | Per WP | "Phase 3.0: WP-001 Business Context" | business_specialist_requirements |
| 3.1 | Per WP | "Phase 3.1: WP-001 Logic Extraction" | business_specialist_logic_extraction |
| 3.1.1 | Per WP | "Phase 3.1.1: WP-001 Logic Review" | business_reviewer_logic_extraction |
| 3.2 | Per WP | "Phase 3.2: WP-001 Specification" | business_specialist_requirements |
| 3.2.1 | Per WP | "Phase 3.2.1: WP-001 Spec Review" | business_reviewer_requirements |
| 4 | Per WP | "Phase 4: WP-001 Test Cases" | business_specialist_test_design |
| 5.0.0 | Per WP | "Phase 5.0.0: WP-001 Tech Spec" | tech_spec_extraction_specialist |
| 5.2 | Per WP | "Phase 5.2: WP-001 Backend Code" | development_specialist_code_generation |
| 6 | Global | "Phase 6: Integration Testing" | development_team_supervisor |

### 3.4 Ticket Description Template

Each ticket's description contains the information the agent needs:

```markdown
## Task: {step_display_name}

**Phase**: {phase_id}
**Step**: {step_id}
**Workpackage**: {workpackage_id} (if applicable)
**Pod**: {pod_id} (if applicable)
**Agent**: {agent_name}
**Prompt File**: {prompt_file_path}
**Working Directory**: {worktree_path}

### Expected Deliverables
{list of deliverable paths}

### Input Dependencies
{list of input file paths from previous phases}

### Quality Criteria
{from phase definition}

### HITL Configuration
- Review required after completion: {yes/no}
- Auto-approve if validation passes: {yes/no}
- Escalate after {max_iterations} failed reviews
```

---

## 4. Event Watcher

### 4.1 Main Loop

```python
class EventWatcher:
    """
    Stateless event loop that reacts to tracker status changes.
    """

    def __init__(
        self,
        tracker: TrackerBackend,
        executor: AgentExecutor,
        git: GitManager,
        config: WatcherConfig,
    ):
        self.tracker = tracker
        self.executor = executor
        self.git = git
        self.config = config
        self.last_poll = datetime.now(timezone.utc).isoformat()

    def run(self):
        """Main event loop. Runs forever until interrupted."""
        print("ACM Event Watcher started. Polling every "
              f"{self.config.poll_interval_seconds}s...")

        while True:
            try:
                self.poll_and_react()
                time.sleep(self.config.poll_interval_seconds)
            except KeyboardInterrupt:
                print("Watcher stopped.")
                break
            except Exception as e:
                print(f"Error in poll cycle: {e}")
                time.sleep(self.config.poll_interval_seconds)

    def poll_and_react(self):
        """Single poll cycle: check for changes, react to each."""

        # 1. Check for tickets that became READY
        ready_tickets = self.tracker.get_tickets_by_status(TicketStatus.READY)
        for ticket in ready_tickets:
            self.handle_ready(ticket)

        # 2. Check for tickets that were APPROVED by human
        approved_tickets = self.tracker.get_tickets_by_status(TicketStatus.APPROVED)
        for ticket in approved_tickets:
            self.handle_approved(ticket)

        # 3. Check for tickets that were REJECTED by human
        rejected_tickets = self.tracker.get_tickets_by_status(TicketStatus.REJECTED)
        for ticket in rejected_tickets:
            self.handle_rejected(ticket)

    def handle_ready(self, ticket: Ticket):
        """A ticket is ready for agent execution."""

        # Verify all blockers are actually done
        if not self.all_blockers_resolved(ticket):
            return  # Not actually ready — dependency not met

        # Transition to IN_PROGRESS
        self.tracker.update_status(ticket.id, TicketStatus.IN_PROGRESS)

        # Git tag: started
        tag = f"ACM-{ticket.id}/started"
        self.git.tag(tag)
        self.tracker.update_metadata(ticket.id, TicketMetadata(
            **{**ticket.metadata.__dict__, "git_tag_started": tag}
        ))

        # Delegate to agent
        try:
            result = self.executor.execute(ticket)

            if result.success:
                # Validate deliverables
                validation = self.executor.validate_deliverables(ticket)

                # Git commit deliverables
                self.git.commit_deliverables(
                    ticket.metadata.deliverable_paths,
                    f"ACM-{ticket.id}: {ticket.title} completed"
                )

                # Git tag: completed
                tag = f"ACM-{ticket.id}/completed"
                self.git.tag(tag)
                self.tracker.update_metadata(ticket.id, TicketMetadata(
                    **{**ticket.metadata.__dict__, "git_tag_completed": tag}
                ))

                # Add agent output as comment
                self.tracker.add_comment(ticket.id, result.summary)

                if ticket.metadata.hitl_required:
                    # Move to AWAITING_REVIEW — wait for human
                    self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
                else:
                    # Auto-approve if HITL not required
                    self.handle_auto_approve(ticket)
            else:
                # Agent failed
                self.tracker.update_status(ticket.id, TicketStatus.FAILED)
                self.tracker.add_comment(ticket.id, f"Agent failed: {result.error}")

        except Exception as e:
            self.tracker.update_status(ticket.id, TicketStatus.FAILED)
            self.tracker.add_comment(ticket.id, f"Execution error: {e}")

    def handle_approved(self, ticket: Ticket):
        """Human approved a ticket. Unblock dependents."""

        # Git tag: approved
        tag = f"ACM-{ticket.id}/approved"
        self.git.tag(tag)

        # Move to DONE
        self.tracker.update_status(ticket.id, TicketStatus.DONE)

        # Unblock dependent tickets
        self.unblock_dependents(ticket)

    def handle_rejected(self, ticket: Ticket):
        """Human rejected a ticket. Create remediation ticket."""

        iteration = ticket.metadata.iteration

        if iteration >= ticket.metadata.max_iterations:
            # Max iterations exceeded — escalate
            self.tracker.add_comment(
                ticket.id,
                f"Max iterations ({ticket.metadata.max_iterations}) exceeded. "
                "Escalating to human supervisor."
            )
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            return

        # Get rejection feedback from latest comment
        feedback = ticket.comments[-1] if ticket.comments else "No feedback provided"

        # Create remediation ticket
        remediation = Ticket(
            id="",  # assigned by tracker
            title=f"{ticket.title} — Remediation (iteration {iteration + 1})",
            description=f"## Remediation\n\nOriginal: {ticket.id}\n\n"
                        f"### Rejection Feedback\n\n{feedback}\n\n"
                        f"### Original Task\n\n{ticket.description}",
            status=TicketStatus.READY,
            ticket_type=TicketType.REMEDIATION,
            metadata=TicketMetadata(
                **{**ticket.metadata.__dict__,
                   "iteration": iteration + 1,
                   "parent_ticket_id": ticket.id}
            ),
        )
        new_id = self.tracker.create_ticket(remediation)
        self.tracker.create_link(ticket.id, new_id, "parent")

        # Mark original as DONE (remediation takes over)
        self.tracker.update_status(ticket.id, TicketStatus.DONE)

    def handle_auto_approve(self, ticket: Ticket):
        """Auto-approve when HITL is not required."""
        tag = f"ACM-{ticket.id}/approved"
        self.git.tag(tag)
        self.tracker.update_status(ticket.id, TicketStatus.DONE)
        self.unblock_dependents(ticket)

    def unblock_dependents(self, ticket: Ticket):
        """Check all tickets blocked by this one. If all blockers done, set READY."""
        for blocked_id in ticket.blocks:
            blocked = self.tracker.get_ticket(blocked_id)
            if self.all_blockers_resolved(blocked):
                self.tracker.update_status(blocked_id, TicketStatus.READY)

    def all_blockers_resolved(self, ticket: Ticket) -> bool:
        """Check if all tickets that block this one are DONE."""
        for blocker_id in ticket.blocked_by:
            blocker = self.tracker.get_ticket(blocker_id)
            if blocker.status != TicketStatus.DONE:
                return False
        return True
```

### 4.2 Watcher Configuration

```python
@dataclass
class WatcherConfig:
    poll_interval_seconds: int = 30         # How often to check tracker
    hitl_default: bool = True               # Default: require human review
    hitl_override_phases: dict = field(default_factory=dict)
    # Per-phase HITL override:
    # {"phase-1": False, "phase-3.2.1": True, "phase-5": False}
    # False = auto-approve after validation passes
    # True = always require human review
    max_concurrent_agents: int = 3          # Max parallel agent executions
    auto_approve_on_validation_pass: bool = False  # Skip HITL if validation passes
```

### 4.3 HITL Configuration

The `hitl_required` field on each ticket controls whether human review is needed.
This can be configured at multiple levels:

| Level | How | Example |
|-------|-----|---------|
| Global default | `WatcherConfig.hitl_default` | `True` = all tickets need review |
| Per-phase override | `WatcherConfig.hitl_override_phases` | `{"phase-1": False}` = auto-approve Phase 1 |
| Per-ticket override | `TicketMetadata.hitl_required` | Set during board initialization |
| Runtime override | Human moves ticket directly to APPROVED | Bypasses watcher's HITL logic |

**HITL flow**:
- `hitl_required = True`: Agent completes → ticket moves to REVIEW → human must move to APPROVED or REJECTED
- `hitl_required = False`: Agent completes → validation runs → if passes, auto-approve → unblock dependents
- Human can always override: move any REVIEW ticket to APPROVED or REJECTED regardless of config

---

## 5. Agent Executor

### 5.1 Interface

```python
@dataclass
class ExecutionResult:
    success: bool
    summary: str                    # Agent output summary (for ticket comment)
    error: Optional[str] = None     # Error message if failed
    deliverables_produced: list[str] = field(default_factory=list)


class AgentExecutor(ABC):
    """Abstract interface for agent execution backends."""

    @abstractmethod
    def execute(self, ticket: Ticket) -> ExecutionResult:
        """
        Execute the agent for this ticket.
        Reads ticket.metadata for agent_name, prompt_file, working_directory.
        Returns result with success/failure and summary.
        """
        ...

    @abstractmethod
    def validate_deliverables(self, ticket: Ticket) -> list[str]:
        """
        Run deliverable_validator.py against expected deliverables.
        Returns list of validation errors (empty = all passed).
        """
        ...
```

### 5.2 Kiro CLI Executor

```python
class KiroCliExecutor(AgentExecutor):
    """Execute agents via Kiro CLI (IDE-based)."""

    def execute(self, ticket: Ticket) -> ExecutionResult:
        # 1. Read prompt file
        prompt = Path(ticket.metadata.prompt_file).read_text()

        # 2. Inject ticket context into prompt
        context = f"## Ticket Context\n\n"
        context += f"Ticket: {ticket.id}\n"
        context += f"Workpackage: {ticket.metadata.workpackage}\n"
        context += f"Working Directory: {ticket.metadata.working_directory}\n\n"
        full_prompt = context + prompt

        # 3. Send to Kiro CLI
        # (Implementation depends on Kiro CLI API — may be stdin/stdout or API call)
        ...
```

### 5.3 Bedrock Direct Executor

```python
class BedrockExecutor(AgentExecutor):
    """Execute agents via direct Bedrock API calls (Pydantic AI or boto3)."""

    def execute(self, ticket: Ticket) -> ExecutionResult:
        # 1. Read prompt file
        # 2. Build Bedrock request with prompt + context
        # 3. Call Bedrock ConversationModel
        # 4. Parse response, write deliverables to disk
        # 5. Return result
        ...
```

### 5.4 CAO Executor (Optional)

```python
class CaoExecutor(AgentExecutor):
    """Execute agents via CAO assign() — for multi-agent orchestration."""

    def execute(self, ticket: Ticket) -> ExecutionResult:
        # 1. cao assign --agent {agent_name} --working-directory {working_dir}
        # 2. Send prompt via CAO send_message
        # 3. Poll CAO terminal for completion
        # 4. Return result
        ...
```

---

## 6. Git Manager

### 6.1 Interface

```python
class GitManager:
    """Manages git operations tied to ticket lifecycle."""

    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path

    def tag(self, tag_name: str):
        """Create a lightweight git tag at HEAD."""
        subprocess.run(["git", "tag", tag_name], cwd=self.repo_path, check=True)

    def commit_deliverables(self, paths: list[str], message: str):
        """Stage and commit deliverable files."""
        for path in paths:
            subprocess.run(["git", "add", path], cwd=self.repo_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.repo_path, check=True
        )

    def create_worktree(self, branch: str, path: str):
        """Create a git worktree for pod execution."""
        subprocess.run(["git", "branch", branch, "main"], cwd=self.repo_path, check=True)
        subprocess.run(
            ["git", "worktree", "add", path, branch],
            cwd=self.repo_path, check=True
        )

    def merge_worktree(self, branch: str):
        """Merge a pod branch back to main."""
        subprocess.run(
            ["git", "merge", branch, "--no-ff", "-m", f"Merge {branch}"],
            cwd=self.repo_path, check=True
        )
```

---

## 7. Status Transition Diagram

```
                    ┌──────────┐
                    │ BACKLOG  │ (created, dependencies unmet)
                    └────┬─────┘
                         │ all blockers → DONE
                         ▼
                    ┌──────────┐
                    │  READY   │ (waiting for agent pickup)
                    └────┬─────┘
                         │ watcher picks up
                         ▼
                    ┌──────────┐
                    │IN_PROGRESS│ (agent working)
                    └────┬─────┘
                         │
                    ┌────┴────┐
                    │         │
                    ▼         ▼
             ┌──────────┐  ┌──────────┐
             │AWAITING │  │  FAILED  │
             │ REVIEW  │  │(error)   │
             └────┬─────┘  └──────────┘
                  │
             ┌────┴────┐
             │         │
             ▼         ▼
      ┌──────────┐  ┌──────────┐
      │ APPROVED │  │ REJECTED │
      │(human)   │  │(human)   │
      └────┬─────┘  └────┬─────┘
           │              │ create remediation ticket
           ▼              ▼
      ┌──────────┐  ┌──────────┐
      │   DONE   │  │  READY   │ (remediation ticket)
      │          │  │          │
      └──────────┘  └──────────┘
           │
           ▼
      unblock dependents → their status: BACKLOG → READY
```

**Special transitions**:
- `PAUSED`: Human can move any ticket to PAUSED. Watcher ignores it until human moves it back.
- `FAILED → READY`: Human can retry a failed ticket by moving it back to READY.
- `REVIEW → DONE`: If `hitl_required = False`, watcher auto-transitions REVIEW → DONE.

---

## 8. Dependency Resolution

### 8.1 Dependency Rules

Dependencies are defined during board initialization based on the ACM phase pipeline:

| Ticket | Blocked By |
|--------|-----------|
| Phase 2.1 (WP Definition) | Phase 1.1 (DB Analysis), Phase 1.2 (Source Analysis) |
| Phase 2.5 (Pod Partitioning) | Phase 2.1 (WP Definition) |
| Phase 3.0 WP-001 (Context) | Phase 2.5 (Pod Partitioning) |
| Phase 3.1 WP-001 (Extraction) | Phase 3.0 WP-001 (Context) |
| Phase 3.1.1 WP-001 (Review) | Phase 3.1 WP-001 (Extraction) |
| Phase 3.2 WP-001 (Spec) | Phase 3.1.1 WP-001 (Review) |
| Phase 5.0.0 WP-001 (Tech Spec) | Phase 3.2.1 WP-001 (Spec Review) |
| Phase 5.2 WP-001 (Backend) | Phase 5.0.0 WP-001 (Tech Spec) |

### 8.2 Cross-Workpackage Dependencies

Workpackages within the same pod have no cross-dependencies (they share a worktree
but execute independently). Cross-pod dependencies are resolved at merge time.

### 8.3 Natural Parallelism

If WP-001 Phase 3.0 and WP-003 Phase 3.0 are both READY (no mutual dependencies),
the watcher delegates both to agents simultaneously. Parallelism emerges from the
dependency graph — no explicit pod partitioning needed for the event-driven model.

---

## 9. Built-In Tracker Web UI

### 9.1 Minimal Requirements

The built-in SQLite tracker needs a simple web UI for human interaction:

| Feature | Purpose |
|---------|---------|
| Board view | Kanban columns: Backlog, Ready, In Progress, Awaiting Review, Approved, Rejected, Done |
| Ticket detail | View title, description (markdown rendered), metadata, comments, status history |
| Status transition | Drag-and-drop or button to move ticket between columns |
| Comment | Add rejection feedback or notes |
| Filter | By phase, workpackage, pod, status |
| Search | By ticket ID or title |

### 9.2 Technology Stack

- **Backend**: FastAPI (Python) — single file, minimal dependencies
- **Database**: SQLite (already used by tracker)
- **Frontend**: HTMX + minimal CSS — no build step, no npm, no React
- **Deployment**: `acm tracker serve --port 8080`

### 9.3 API Endpoints (for watcher and UI)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/tickets` | List tickets (with filters) |
| GET | `/api/tickets/{id}` | Get ticket detail |
| PATCH | `/api/tickets/{id}/status` | Change ticket status |
| POST | `/api/tickets/{id}/comments` | Add comment |
| GET | `/api/board` | Board view data (tickets grouped by status) |
| GET | `/api/stats` | Dashboard stats (counts by status, phase, etc.) |

---

## 10. Project Structure

```
acm/
├── tracker/
│   ├── __init__.py
│   ├── models.py              # Ticket, TicketMetadata, TicketStatus, etc.
│   ├── backend.py             # TrackerBackend ABC
│   ├── sqlite_backend.py      # SqliteTracker implementation
│   ├── gitea_backend.py       # GiteaTracker implementation
│   ├── jira_backend.py        # JiraTracker implementation (optional)
│   ├── github_backend.py      # GitHubTracker implementation (optional)
│   └── web/
│       ├── app.py             # FastAPI app for built-in tracker UI
│       ├── templates/         # HTMX templates
│       └── static/            # Minimal CSS
├── watcher/
│   ├── __init__.py
│   ├── event_watcher.py       # EventWatcher main loop
│   ├── config.py              # WatcherConfig
│   └── dependency_resolver.py # Dependency graph logic
├── executor/
│   ├── __init__.py
│   ├── base.py                # AgentExecutor ABC
│   ├── kiro_executor.py       # Kiro CLI executor
│   ├── bedrock_executor.py    # Direct Bedrock executor
│   └── cao_executor.py        # CAO executor (optional)
├── git_manager.py             # GitManager
├── board_initializer.py       # acm init logic
└── cli.py                     # CLI entry points (acm init, acm watch, acm tracker serve)
```

---

## 11. CLI Commands

```bash
# Initialize the board with all tickets
acm init --project ./project-config.json --tracker sqlite

# Start the event watcher
acm watch --tracker sqlite --poll-interval 30

# Start the built-in tracker web UI
acm tracker serve --port 8080

# Manual ticket operations (for scripting/debugging)
acm ticket list --status ready
acm ticket show ACM-042
acm ticket transition ACM-042 --to approved
acm ticket comment ACM-042 --message "Looks good, approved."
```

---

## 12. Tracker Comparison

| Criterion | SQLite (built-in) | Vikunja | Roundup | Gitea | Jira | GitHub Issues |
|-----------|-------------------|---------|---------|-------|------|---------------|
| Language | Python | Go | Python | Go | Java (SaaS) | N/A (SaaS) |
| Deployment | Embedded (zero setup) | Docker / binary | pip install | Docker / binary | Cloud or Server | Cloud |
| Web UI / Kanban | Must build (Phase 2) | ✅ Built-in (Kanban, list, Gantt, table) | ⚠️ List/detail only (no Kanban) | ✅ Projects board | ✅ Full board | ✅ Projects board |
| REST API | Must build | ✅ Full | ✅ Full + JWT | ✅ Full | ✅ Full | ✅ Full |
| Webhooks | ❌ (polling only) | ✅ Native | ❌ (use reactors instead) | ✅ Native | ✅ Native | ✅ Native |
| Event hooks (in-process) | ✅ (direct DB) | ❌ | ✅ Auditors/Reactors (Python) | ❌ | ❌ | ❌ |
| Custom fields | ✅ Native (schema) | ⚠️ Labels + frontmatter | ✅ Native (schema) | ⚠️ Labels | ✅ Custom fields | ⚠️ Labels |
| Task dependencies | ✅ Native (schema) | ⚠️ Task relations (v1.0+) | ✅ Native depends/blocks | ⚠️ Labels | ✅ Native links | ⚠️ Labels |
| MCP server | ❌ | ✅ Exists | ❌ | ❌ | ❌ | ✅ Exists |
| Self-hosted | ✅ | ✅ | ✅ | ✅ | ⚠️ Data Center only | ❌ |
| Data sovereignty | ✅ Local file | ✅ Your server | ✅ Your server | ✅ Your server | ⚠️ Atlassian cloud | ❌ AWS cloud |
| Python integration | ✅ Native | ⚠️ REST only | ✅ Native Python API | ⚠️ REST only | ⚠️ REST only | ⚠️ REST only |
| Setup effort | None | Low (Docker) | Medium (schema config) | Low (Docker) | Low (cloud) / High (server) | None (cloud) |
| Community | N/A (custom) | Active | Small but stable | Large | Largest | Largest |
| License | N/A | AGPL | MIT-like (Zope) | MIT | Commercial | Commercial |

### Recommendation

| Use Case | Recommended Tracker |
|----------|-------------------|
| **Fastest MVP** | SQLite built-in — zero dependencies, embedded |
| **Best self-hosted experience** | **Vikunja** — Kanban UI, webhooks, MCP server, minimal setup |
| **Deepest Python integration** | **Roundup** — in-process reactors replace polling, native custom fields |
| **Git + issues in one tool** | Gitea — git hosting + issues + board in one binary |
| **Enterprise / existing tooling** | Jira — if the customer already uses it |
| **GitHub-native workflow** | GitHub Issues — if repo is on GitHub |

**Default recommendation**: Start with **Vikunja** for the self-hosted default. It
provides the Kanban board UI, webhooks, and REST API out of the box — eliminating
the need to build a custom web UI (Phase 2 of the implementation plan). Fall back
to the SQLite built-in tracker only if zero external dependencies is a hard requirement.

Consider **Roundup** if deep Python integration is valued — its auditor/reactor
system means the event watcher can be implemented as a Roundup plugin rather than
a separate polling process, and its native custom fields avoid the label-encoding
workarounds needed by Vikunja, Gitea, and GitHub.

---

## 13. Implementation Priority

### Phase 1: Core (MVP)
1. `models.py` — data classes (Ticket, TicketMetadata, TicketStatus)
2. `backend.py` — TrackerBackend ABC
3. `vikunja_backend.py` — Vikunja tracker (recommended default)
4. `sqlite_backend.py` — built-in fallback tracker
5. `board_initializer.py` — `acm init`
6. `event_watcher.py` — main loop with READY/APPROVED/REJECTED handlers
7. `bedrock_executor.py` — simplest agent executor
8. `git_manager.py` — tag + commit
9. `cli.py` — `acm init` + `acm watch`

### Phase 2: Web UI (only if using SQLite backend)
10. `web/app.py` — FastAPI + HTMX board view
11. Ticket detail, status transition, comments
12. (Skip if using Vikunja — its UI covers this)

### Phase 3: Additional Backends
13. `roundup_backend.py` — with reactor-based event handling
14. `gitea_backend.py`
15. `github_backend.py`
16. `jira_backend.py`

### Phase 4: Advanced Features
17. `kiro_executor.py`
18. `cao_executor.py`
19. Concurrent agent execution (thread pool)
20. Webhook listener (for Vikunja/Gitea webhooks — replace polling)
21. Pod worktree integration

---

## 14. Multi-Machine Deployment

### 14.1 The Problem

Multiple event watchers polling the same tracker create race conditions: two watchers
see a ticket as READY, both transition it to IN_PROGRESS, both delegate to agents —
duplicate execution. This applies regardless of tracker backend (SQLite, Vikunja, Jira).
The issue is not concurrent writes to the tracker (most handle that fine) but the
non-atomic read-then-write decision in the watcher.

### 14.2 Recommended Architecture: Single Watcher, Remote Agents

```
Machine 1 (Coordinator):
  ├── Event Watcher (single instance — the only decision-maker)
  ├── Tracker (Vikunja / SQLite)
  ├── Git repo (main branch)
  └── Git Manager (tags, commits)

Machine 2..N (Workers):
  ├── Agent Executor (HTTP endpoint, listens for work)
  ├── Git worktree (clone/pull from coordinator)
  ├── Bedrock API access (via IAM role)
  └── Local filesystem for deliverables
```

The watcher picks up a READY ticket, calls a worker machine's HTTP endpoint with the
ticket metadata (agent name, prompt file, working directory), and waits for the result.
The worker executes the agent, writes deliverables to its local git worktree, pushes
to the shared remote, and returns the result. The watcher then commits/tags on the
coordinator and updates the tracker.

**Worker endpoint** (minimal):
```
POST /execute
Body: { "ticket_id": "ACM-042", "agent_name": "...", "prompt_file": "...", "working_directory": "..." }
Response: { "success": true, "summary": "...", "deliverables": [...] }
```

### 14.3 Alternative: Queue-Based Distribution

For higher reliability, the watcher pushes work items to a queue (SQS, Redis list,
or a database table). Workers pull from the queue. The queue guarantees exactly-once
delivery. This decouples the watcher from worker availability — if a worker is busy,
the item stays in the queue until one is free.

### 14.4 What NOT to Do

Do not run multiple watchers against the same tracker without a distributed locking
mechanism. The complexity of distributed locks (Redis, DynamoDB conditional writes)
is not justified when a single watcher handles the load — the watcher is lightweight
(poll + dispatch), and the heavy work (LLM inference) happens on remote APIs.

---

## 15. Vikunja Real-Time UI Limitation

Vikunja's web frontend (Vue.js SPA) does not auto-refresh when the backend state
changes via REST API calls. If the watcher moves a ticket from READY to IN_PROGRESS
via the API, a human viewing the Kanban board will not see the change until they
manually refresh the browser page.

There is no WebSocket or server-sent events (SSE) push mechanism in Vikunja.

**Impact on ACM**: The human's workflow is check board → review deliverables on disk →
approve/reject via board → refresh to see next batch. The manual refresh is a natural
part of this workflow and is acceptable for the MVP.

**Future options if real-time matters**:
1. Browser auto-refresh (meta tag or extension) — crude but effective
2. Thin WebSocket proxy — listens to Vikunja webhooks, pushes to connected browsers
3. ACM web dashboard integration — the existing ACM dashboard could poll the tracker
   API and display real-time status alongside its own views

---

## Appendix A: Pipeline Definition Format

The event-driven tracker system is generic — it can orchestrate any phased workflow,
not just ACM migrations. The pipeline is defined in a YAML configuration file that
describes phases, steps, ticket chains, and HITL points.

### A.1 Pipeline Configuration Schema

```yaml
# pipeline.yaml — defines the entire workflow as phases and steps

pipeline:
  name: "CardDemo Legacy Migration"
  description: "Migrate CardDemo mainframe application to Spring Boot"
  version: "1.0"

  # Global settings
  settings:
    hitl_default: true                    # Default: require human review
    max_iterations: 3                     # Max rework cycles before escalation
    poll_interval_seconds: 30
    max_concurrent_agents: 3

  # Phase definitions (ordered)
  phases:
    - id: "phase-1"
      name: "Legacy Analysis"
      scope: "global"                     # global = one set of tickets for the whole project
      creates_next_phases: ["phase-2"]    # Milestone: completing this phase creates Phase 2 tickets
      steps:
        - id: "step-1.1"
          name: "Database Analysis"
          type: "task"
          agent: "analysis_specialist_database"
          prompt: "prompts/01_analysis/Database/01_analysis.md"
          deliverables:
            - "output/analysis/database/reports/DB_Source_Analysis_Report.md"
            - "output/analysis/database/gen_src_db/sqlite_ddl.sql"
          hitl_after: false               # No human review needed — goes to next step
          
        - id: "step-1.2"
          name: "Source Code Analysis"
          type: "task"
          agent: "analysis_specialist_legacy_code"
          prompt: "prompts/01_analysis/Sourcecode/01_generate_cobol_analysis_tool.md"
          depends_on: []                  # Can run in parallel with step-1.1
          deliverables:
            - "output/analysis/source_code/reports/Cobol_Source_Analysis_Report.md"
            - "output/analysis/source_code/flows/Business_Flows.json"
            - "output/analysis/source_code/Module_Classifications.json"
          hitl_after: true                # Human reviews analysis before proceeding

    - id: "phase-2"
      name: "Migration Planning"
      scope: "global"
      creates_next_phases: ["phase-2.5"]
      steps:
        - id: "step-2.1"
          name: "Workpackage Definition"
          type: "task"
          agent: "planning_specialist_workpackage"
          prompt: "prompts/02_workpackage/01_generate_workpackage_definition_tool.md"
          deliverables:
            - "output/analysis/workpackages/Workpackage_Planning.json"
          hitl_after: true

    - id: "phase-2.5"
      name: "Pod Partitioning"
      scope: "global"
      creates_next_phases: ["phase-3"]    # Milestone: creates per-WP tickets
      steps:
        - id: "step-2.5.1"
          name: "Pod Assignment"
          type: "task"
          agent: "planning_specialist_workpackage"
          prompt: "prompts/02_workpackage/02_pod_partitioning.md"
          deliverables:
            - "output/analysis/workpackages/Pod_Assignment.json"
          hitl_after: true

    - id: "phase-3"
      name: "Business Specification"
      scope: "per_workpackage"            # Creates tickets for each WP
      steps:
        # Ticket chain: specialist → reviewer → human
        - id: "step-3.1"
          name: "Business Logic Extraction"
          type: "task"
          agent: "business_specialist_logic_extraction"
          prompt: "prompts/03-business_extraction/phase_3.1_business_logic_extraction.md"
          deliverables:
            - "output/specifications/business/traceability/{wp_id}-chapter6.md"
          hitl_after: false               # Goes straight to reviewer agent

        - id: "step-3.1.1"
          name: "Logic Extraction Review"
          type: "reviewer_step"
          agent: "business_reviewer_logic_extraction"
          prompt: "prompts/03-business_extraction/phase_3.1.1_business_logic_extraction_review.md"
          depends_on: ["step-3.1"]        # Blocked until extraction completes
          deliverables:
            - "output/specifications/business/traceability/review/{wp_id}-logic-extraction-review.md"
          hitl_after: true                # Human reviews after reviewer agent

        - id: "step-3.2"
          name: "Business Specification Generation"
          type: "task"
          agent: "business_specialist_requirements"
          prompt: "prompts/03-business_extraction/phase_3.2_business_specification_generation.md"
          depends_on: ["step-3.1.1"]
          deliverables:
            - "output/specifications/business/specs/{wp_id}-specification.md"
          hitl_after: false

        - id: "step-3.2.1"
          name: "Business Specification Review"
          type: "reviewer_step"
          agent: "business_reviewer_requirements"
          prompt: "prompts/03-business_extraction/phase_3.2.1_business_specification_review.md"
          depends_on: ["step-3.2"]
          deliverables:
            - "output/specifications/business/specs/review/{wp_id}-review.md"
          hitl_after: true

    - id: "phase-5"
      name: "Code Generation"
      scope: "per_workpackage"
      steps:
        - id: "step-5.0.0"
          name: "Technical Implementation Guide"
          type: "task"
          agent: "tech_spec_extraction_specialist"
          prompt: "prompts/05_code_generation/phase_5.0.0_tech_spec_creation.md"
          depends_on: ["step-3.2.1"]      # Cross-phase dependency
          deliverables:
            - "output/specifications/technical/{wp_id}-tech-implementation-guide.md"
          hitl_after: false

        - id: "step-5.0.1"
          name: "Tech Spec Review"
          type: "reviewer_step"
          agent: "tech_spec_review_specialist"
          prompt: "prompts/05_code_generation/phase_5.0.1_tech_spec_review.md"
          depends_on: ["step-5.0.0"]
          hitl_after: true
```

### A.2 Ticket Chain Patterns

The pipeline YAML supports several common patterns:

**Pattern 1: Single agent, human review**
```yaml
steps:
  - id: "analysis"
    type: "task"
    agent: "specialist"
    hitl_after: true          # Agent → Human
```
Creates 1 ticket. After agent completes → AWAITING_REVIEW → human approves/rejects.

**Pattern 2: Specialist → Reviewer → Human**
```yaml
steps:
  - id: "extraction"
    type: "task"
    agent: "specialist"
    hitl_after: false          # Agent → next ticket (no human)
  - id: "review"
    type: "reviewer_step"
    agent: "reviewer"
    depends_on: ["extraction"]
    hitl_after: true           # Reviewer → Human
```
Creates 2 tickets. Specialist auto-approves → reviewer runs → human reviews.

**Pattern 3: Agent only, no human**
```yaml
steps:
  - id: "gate"
    type: "gate"
    agent: "validator"
    hitl_after: false          # Agent → auto-approve → unblock next
```
Creates 1 ticket. Agent runs, auto-approves, unblocks dependents. No human involved.

**Pattern 4: Human-gated at every step**
```yaml
steps:
  - id: "extraction"
    type: "task"
    agent: "specialist"
    hitl_after: true           # Agent → Human
  - id: "review"
    type: "reviewer_step"
    agent: "reviewer"
    depends_on: ["extraction"]
    hitl_after: true           # Reviewer → Human
```
Creates 2 tickets. Human approves after specialist AND after reviewer. Slowest but most controlled.

### A.3 Scope and Dynamic Ticket Creation

| Scope | Meaning | When tickets are created |
|-------|---------|------------------------|
| `global` | One set of tickets for the whole project | At init or when parent phase completes |
| `per_workpackage` | One set of tickets per workpackage | When workpackage planning completes (Phase 2.1) |
| `per_pod` | One set of tickets per pod | When pod assignment completes (Phase 2.5) |

The `creates_next_phases` field on a phase tells the watcher: "when all steps in this
phase are DONE, create tickets for the listed phases." This is how progressive ticket
creation works — the watcher reads the pipeline YAML and the deliverables from the
completed phase to know what tickets to create next.

### A.4 Deliverable Path Templates

Deliverable paths support `{wp_id}` and `{pod_id}` placeholders that are resolved
when tickets are created for per-workpackage or per-pod scopes:

```yaml
deliverables:
  - "output/specifications/business/traceability/{wp_id}-chapter6.md"
  # Resolved to: output/specifications/business/traceability/WP-001-chapter6.md
```

### A.5 Cross-Phase Dependencies

Steps can depend on steps in other phases using the step ID:

```yaml
- id: "step-5.0.0"
  depends_on: ["step-3.2.1"]    # Depends on Phase 3 review being done
```

For per-workpackage scopes, the dependency is resolved per-WP: WP-001's step-5.0.0
depends on WP-001's step-3.2.1, not on all workpackages' step-3.2.1.

### A.6 Rejection and Remediation Flow

When a human rejects a ticket (moves to REJECTED with a comment):

1. The watcher reads the rejection comment
2. Creates a remediation ticket targeting the LAST `type: "task"` step in the chain
   before the rejected ticket
3. The remediation ticket has `iteration: N+1` and includes the rejection feedback
4. When the remediation completes, the reviewer step is re-triggered automatically

Example: Human rejects "Phase 3.1.1: WP-001 Logic Review" →
- Remediation ticket created for step-3.1 (the specialist, not the reviewer)
- Specialist re-runs with feedback
- On completion, step-3.1.1 (reviewer) is re-triggered
- Reviewer validates the fix
- Back to AWAITING_REVIEW for human

---

## Appendix B: Sample Project Configuration

### B.1 project-config.json (Minimal)

```json
{
  "projectName": "carddemo",
  "projectBasePath": ".",
  "legacySystem": {
    "language": "COBOL",
    "framework": "CICS",
    "platform": "z/OS Mainframe",
    "database": "VSAM"
  },
  "targetSystem": {
    "language": "Java",
    "framework": "Spring Boot",
    "platform": "Cloud Native",
    "database": "PostgreSQL"
  }
}
```

### B.2 Watcher Configuration (watcher-config.yaml)

```yaml
tracker:
  backend: "sqlite"                       # sqlite | vikunja | roundup | gitea | jira | github
  config:
    db_path: ".acm/tracker.db"            # For sqlite
    # base_url: "http://localhost:3456"    # For vikunja
    # token: "your-api-token"             # For vikunja/gitea/jira

pipeline: "pipeline.yaml"                 # Path to pipeline definition

settings:
  poll_interval_seconds: 30
  max_concurrent_agents: 3
  hitl_default: true

executor:
  backend: "bedrock"                      # bedrock | kiro | cao
  config:
    model_id: "anthropic.claude-sonnet-4-20250514"
    region: "us-east-1"
    # For kiro: kiro_cli_path: "/usr/local/bin/kiro"
    # For cao: cao_server_url: "http://localhost:9889"

git:
  auto_tag: true
  auto_commit: true
  tag_prefix: "ACM"
```

### B.3 Deliverable Validator Interface

The watcher calls a validator after each agent completes. The validator is a simple
Python function that checks whether expected deliverables exist and meet basic criteria.

```python
def validate_deliverables(
    deliverable_paths: list[str],
    working_directory: str = "."
) -> list[str]:
    """
    Validate that expected deliverables exist and are non-empty.
    Returns list of error messages (empty = all passed).
    """
    errors = []
    for path in deliverable_paths:
        full_path = Path(working_directory) / path
        if not full_path.exists():
            errors.append(f"Missing: {path}")
        elif full_path.stat().st_size < 100:
            errors.append(f"Too small (<100 bytes): {path}")
        elif path.endswith(".json"):
            try:
                json.loads(full_path.read_text())
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON: {path} — {e}")
    return errors
```

### B.4 Agent Executor Contract

The executor receives a ticket and must:
1. Read the prompt file from `ticket.metadata.prompt_file`
2. Inject context (workpackage ID, working directory, deliverable paths)
3. Send to the LLM (Bedrock, Kiro, or CAO)
4. The LLM produces deliverables on disk (writes files)
5. Return success/failure with a summary

The executor does NOT need to understand the migration domain. It just sends a prompt
and checks if files appeared. The domain knowledge is in the prompts.

```python
# Minimal Bedrock executor pseudocode
def execute(ticket: Ticket) -> ExecutionResult:
    # 1. Read prompt
    prompt = Path(ticket.metadata.prompt_file).read_text()

    # 2. Add context header
    context = f"""You are working on: {ticket.title}
Workpackage: {ticket.metadata.workpackage or 'N/A'}
Working directory: {ticket.metadata.working_directory}
Expected deliverables: {ticket.metadata.deliverable_paths}
"""
    full_prompt = context + "\n\n" + prompt

    # 3. Call Bedrock
    response = bedrock_client.converse(
        modelId="anthropic.claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": [{"text": full_prompt}]}]
    )

    # 4. Check if deliverables appeared
    errors = validate_deliverables(
        ticket.metadata.deliverable_paths,
        ticket.metadata.working_directory
    )

    if errors:
        return ExecutionResult(success=False, error=str(errors), summary="Deliverables missing")
    else:
        return ExecutionResult(success=True, summary="All deliverables produced")
```

---

## Appendix C: Using This Spec for Non-ACM Workflows

The event-driven tracker system is generic. To use it for a different phased workflow:

1. Write a `pipeline.yaml` defining your phases, steps, agents, and HITL points
2. Create prompt files for each step (the agent's instructions)
3. Define expected deliverables per step
4. Run `acm init` (or rename the CLI) to create the initial tickets
5. Run `acm watch` to start the event loop

Examples of other workflows this could orchestrate:
- **Document generation pipeline**: Research → Draft → Review → Edit → Publish
- **Data pipeline**: Extract → Transform → Validate → Load → Verify
- **Testing pipeline**: Generate tests → Run tests → Analyze failures → Fix → Re-run
- **Compliance audit**: Collect evidence → Analyze gaps → Remediate → Verify → Report

The only ACM-specific parts are the prompt files and the deliverable paths. The
orchestration engine (tracker + watcher + executor + git manager) is domain-agnostic.

---
---

# Part 2: Framework Implementation Specification (Fresh Project)

These sections extend the base specification with the detail needed to implement
the complete framework from scratch, including agent execution strategies, context
assembly, LLM provider abstraction, tool system, reviewer patterns, validation,
scoping, and observability.

**Added**: 2026-04-20
**Purpose**: Enable building the framework skeleton with stubs, then migrating
existing agent logic from the current Pydantic AI orchestrator into the new
event-driven architecture.

---

## 15. Agent Registry & Execution Strategy Pattern

### 15.1 Design Principle

Every ticket maps to a named agent. The watcher does not know or care whether
that agent runs a subprocess, calls an LLM, or does both. The registry resolves
the agent name to an executor instance that encapsulates the execution strategy.

### 15.2 AgentExecutor Protocol

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExecutionResult:
    """Result returned by any agent executor."""
    success: bool
    summary: str                              # Human-readable summary (goes into ticket comment)
    error: Optional[str] = None              # Error message if failed
    deliverables_produced: list[str] = field(default_factory=list)  # Paths written
    metrics: Optional["StepMetrics"] = None  # Token usage, timing, cost


class AgentExecutor(ABC):
    """Base class for all agent execution strategies."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Unique name identifying this agent (matches ticket metadata)."""
        ...

    @abstractmethod
    def execute(self, ticket: "Ticket", context: "ExecutionContext") -> ExecutionResult:
        """Execute the agent's work for the given ticket.

        Args:
            ticket: The ticket being executed (contains metadata, description, etc.)
            context: Runtime context (project config, working directory, etc.)

        Returns:
            ExecutionResult with success/failure, summary, and deliverables list.
        """
        ...

    def validate_deliverables(self, ticket: "Ticket", context: "ExecutionContext") -> "ValidationResult":
        """Optional: validate deliverables after execution.

        Default implementation delegates to the DeliverableValidator.
        Subclasses can override for agent-specific validation.
        """
        from .validation import DeliverableValidator
        validator = DeliverableValidator()
        return validator.validate(ticket, context)
```

### 15.3 Execution Strategy Types

```python
class ToolExecutor(AgentExecutor):
    """Executes a deterministic subprocess tool. No LLM involved.

    Examples: legacy_analyzer, migration_planner, pod_partitioner, full_analyzer.

    Subclass and implement:
    - build_command(): returns the shell command string and working directory
    - parse_output(): optionally parse stdout into structured result
    """

    @abstractmethod
    def build_command(self, ticket: "Ticket", context: "ExecutionContext") -> tuple[str, str]:
        """Build shell command and working directory.

        Returns:
            (command_string, working_directory) tuple.
        """
        ...

    def execute(self, ticket: "Ticket", context: "ExecutionContext") -> ExecutionResult:
        cmd, cwd = self.build_command(ticket, context)
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        if result.returncode == 0:
            return ExecutionResult(
                success=True,
                summary=f"Tool {self.agent_name} completed (exit 0)",
                deliverables_produced=self._discover_deliverables(ticket, context),
            )
        return ExecutionResult(
            success=False,
            summary=f"Tool {self.agent_name} failed (exit {result.returncode})",
            error=result.stderr[-2000:] if result.stderr else "Unknown error",
        )


class LLMExecutor(AgentExecutor):
    """Executes an LLM agent with tool access (read/write/search files).

    The LLM receives a prompt and can call tools in a loop until it
    produces its deliverables. This is the "autonomous agent" pattern.

    Subclass and implement:
    - get_system_prompt(): returns the agent's system prompt (role, instructions)
    - get_tools(): returns the list of tools available to this agent
    - get_model_config(): returns model selection and parameters
    """

    @abstractmethod
    def get_system_prompt(self, ticket: "Ticket", context: "ExecutionContext") -> str:
        """Build the system prompt for this agent."""
        ...

    @abstractmethod
    def get_user_prompt(self, ticket: "Ticket", context: "ExecutionContext") -> str:
        """Build the user prompt (task instructions + context)."""
        ...

    def get_tools(self) -> list["AgentTool"]:
        """Return tools available to this agent. Default: file ops."""
        return [ReadFileTool(), WriteFileTool(), ListFilesTool(), SearchFileTool()]

    def get_model_config(self) -> "ModelConfig":
        """Return model configuration. Override for agent-specific models."""
        return ModelConfig()

    def execute(self, ticket: "Ticket", context: "ExecutionContext") -> ExecutionResult:
        system_prompt = self.get_system_prompt(ticket, context)
        user_prompt = self.get_user_prompt(ticket, context)
        tools = self.get_tools()
        model_config = self.get_model_config()

        # Delegate to LLM provider with tool loop
        provider = context.llm_provider
        response = provider.run_agent_loop(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            model_config=model_config,
            working_directory=context.working_directory,
            max_iterations=model_config.max_tool_iterations,
        )

        return ExecutionResult(
            success=response.completed,
            summary=response.final_text[:2000],
            deliverables_produced=response.files_written,
            error=response.error,
            metrics=response.metrics,
        )


class HybridExecutor(AgentExecutor):
    """Deterministic context assembly + optional tool step + LLM call.

    This is the recommended pattern for most agents. It:
    1. Assembles context deterministically (reads files, gathers inputs)
    2. Optionally runs a pre-processing tool (compile, analyze, etc.)
    3. Calls the LLM with a fat prompt (all context pre-assembled)
    4. Writes deliverables from the LLM response
    5. Optionally runs post-validation

    The LLM does NOT get tool access (or minimal tool access). It receives
    everything it needs in the prompt and produces output in a single pass.
    This is cheaper, faster, and more predictable than the autonomous pattern.

    Subclass and implement:
    - assemble_context(): gather all input files and build the prompt
    - (optional) pre_tool(): run a tool before the LLM call
    - (optional) post_process(): transform LLM output before writing
    """

    @abstractmethod
    def assemble_context(self, ticket: "Ticket", context: "ExecutionContext") -> "PromptContext":
        """Gather all inputs and build the complete prompt for the LLM.

        This is deterministic — no LLM involved. Reads prompt files,
        gathers deliverables from previous phases, applies templates.
        """
        ...

    def pre_tool(self, ticket: "Ticket", context: "ExecutionContext") -> Optional[str]:
        """Optional: run a tool before the LLM call. Returns tool output or None."""
        return None

    def post_process(self, llm_output: str, ticket: "Ticket", context: "ExecutionContext") -> dict[str, str]:
        """Transform LLM output into deliverable files.

        Default: write the entire output to the first expected deliverable path.
        Override for agents that produce multiple files or need parsing.

        Returns:
            Dict of {relative_path: content} to write.
        """
        if ticket.metadata.deliverable_paths:
            return {ticket.metadata.deliverable_paths[0]: llm_output}
        return {}

    def get_model_config(self) -> "ModelConfig":
        """Return model configuration. Override for agent-specific models."""
        return ModelConfig()

    def execute(self, ticket: "Ticket", context: "ExecutionContext") -> ExecutionResult:
        # 1. Deterministic context assembly
        prompt_context = self.assemble_context(ticket, context)

        # 2. Optional pre-tool
        tool_output = self.pre_tool(ticket, context)
        if tool_output:
            prompt_context.append_section("Tool Output", tool_output)

        # 3. LLM call (single pass, no tool loop)
        provider = context.llm_provider
        response = provider.call(
            system_prompt=prompt_context.system_prompt,
            user_prompt=prompt_context.user_prompt,
            model_config=self.get_model_config(),
        )

        if not response.success:
            return ExecutionResult(success=False, error=response.error, summary="LLM call failed")

        # 4. Post-process and write deliverables
        files_to_write = self.post_process(response.content, ticket, context)
        written = []
        for path, content in files_to_write.items():
            full_path = context.working_directory / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            written.append(path)

        return ExecutionResult(
            success=True,
            summary=response.content[:500],
            deliverables_produced=written,
            metrics=StepMetrics(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                model_id=response.model_id,
                elapsed_seconds=response.elapsed,
            ),
        )


class ReviewerExecutor(LLMExecutor):
    """Specialized LLM executor for reviewer agents.

    Returns a structured ReviewResult instead of free-form output.
    The watcher uses the ReviewResult to decide: approve, reject + rework,
    or escalate.
    """

    def execute(self, ticket: "Ticket", context: "ExecutionContext") -> "ReviewExecutionResult":
        # Same as LLMExecutor but parses output into ReviewResult
        ...
```

### 15.4 Agent Registry

```python
class AgentRegistry:
    """Maps agent names to executor instances.

    Agents are registered at startup. The watcher resolves ticket.metadata.agent_name
    to an executor via this registry.
    """

    def __init__(self):
        self._executors: dict[str, AgentExecutor] = {}

    def register(self, executor: AgentExecutor) -> None:
        """Register an agent executor by its agent_name."""
        self._executors[executor.agent_name] = executor

    def get(self, agent_name: str) -> AgentExecutor:
        """Resolve agent name to executor. Raises KeyError if not found."""
        if agent_name not in self._executors:
            raise KeyError(
                f"No executor registered for agent '{agent_name}'. "
                f"Available: {list(self._executors.keys())}"
            )
        return self._executors[agent_name]

    def list_agents(self) -> list[str]:
        """Return all registered agent names."""
        return list(self._executors.keys())


# Registration example:
registry = AgentRegistry()
registry.register(FullAnalyzerExecutor())          # ToolExecutor
registry.register(MigrationPlannerExecutor())      # ToolExecutor
registry.register(BusinessSpecialistExecutor())    # HybridExecutor
registry.register(BusinessReviewerExecutor())      # ReviewerExecutor
registry.register(CodeGenerationExecutor())        # LLMExecutor (needs tool access)
```

### 15.5 ExecutionContext

```python
@dataclass
class ExecutionContext:
    """Runtime context passed to every agent executor.

    Contains everything an agent needs to execute: project config,
    working directory, LLM provider, and references to previous outputs.
    """
    project_config: "ProjectConfig"
    working_directory: Path
    llm_provider: "LLMProvider"
    tracker: "TrackerBackend"
    git: "GitManager"

    # Scoping context (set for per-WP/per-domain tickets)
    workpackage_id: Optional[str] = None
    domain_name: Optional[str] = None
    pod_id: Optional[str] = None
    flow_id: Optional[str] = None

    # Previous phase outputs (for context assembly)
    previous_deliverables: dict[str, Path] = field(default_factory=dict)
```


---

## 16. Context Assembly

### 16.1 Design Principle

Most agent steps follow the same pattern: read a prompt file, gather input files
from previous phases, substitute template variables, and produce a complete prompt.
This is deterministic work — no LLM needed. The `ContextAssembler` does this work
so that `HybridExecutor` agents receive everything they need in a single prompt.

### 16.2 PromptContext Model

```python
@dataclass
class PromptContext:
    """Assembled prompt ready for LLM consumption."""

    system_prompt: str                    # Agent role + instructions
    user_prompt: str                      # Task + all gathered context
    total_tokens_estimate: int = 0        # Estimated token count (for budget checks)
    source_files_included: list[str] = field(default_factory=list)  # Paths that were read

    def append_section(self, heading: str, content: str) -> None:
        """Append a named section to the user prompt."""
        self.user_prompt += f"\n\n## {heading}\n\n{content}"
```

### 16.3 ContextAssembler Interface

```python
class ContextAssembler:
    """Assembles prompt context for a ticket from project files and templates.

    Responsibilities:
    - Read and render the prompt file (with variable substitution)
    - Gather input dependencies (deliverables from previous phases)
    - Apply token budget constraints (truncate or summarize if too large)
    - Inject scoping context (workpackage metadata, flow info, etc.)
    """

    def __init__(self, project_config: "ProjectConfig", max_context_tokens: int = 180_000):
        self.config = project_config
        self.max_context_tokens = max_context_tokens

    def assemble(self, ticket: "Ticket", context: "ExecutionContext") -> PromptContext:
        """Build complete prompt context for a ticket.

        Steps:
        1. Read prompt file from ticket.metadata.prompt_file
        2. Substitute template variables ({workpackage_id}, {domain_name}, etc.)
        3. Gather input dependencies (files from previous phases)
        4. Apply token budget (truncate large files, prioritize recent outputs)
        5. Build system prompt from agent definition
        6. Combine into PromptContext
        """
        # 1. Read prompt template
        prompt_path = self.config.project_base_path / "prompts" / ticket.metadata.prompt_file
        prompt_template = prompt_path.read_text(encoding="utf-8")

        # 2. Substitute variables
        variables = self._build_variables(ticket, context)
        rendered_prompt = self._render_template(prompt_template, variables)

        # 3. Gather input files
        input_sections = self._gather_inputs(ticket, context)

        # 4. Budget check and truncation
        input_sections = self._apply_budget(input_sections)

        # 5. Build system prompt (from agent definition file if exists)
        system_prompt = self._build_system_prompt(ticket)

        # 6. Combine
        user_prompt = rendered_prompt
        for section_name, section_content in input_sections:
            user_prompt += f"\n\n## {section_name}\n\n{section_content}"

        return PromptContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            source_files_included=[name for name, _ in input_sections],
        )

    def _build_variables(self, ticket: "Ticket", context: "ExecutionContext") -> dict[str, str]:
        """Build template variable dict from ticket and context."""
        return {
            "workpackage_id": context.workpackage_id or "",
            "domain_name": context.domain_name or "",
            "pod_id": context.pod_id or "",
            "flow_id": context.flow_id or "",
            "project_name": self.config.project_name,
            "project_base_path": str(self.config.project_base_path),
            "output_base_path": str(self.config.output_base_path),
            "phase": ticket.metadata.phase,
            "step": ticket.metadata.step,
        }

    def _render_template(self, template: str, variables: dict[str, str]) -> str:
        """Substitute {variable_name} placeholders in template."""
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    def _gather_inputs(self, ticket: "Ticket", context: "ExecutionContext") -> list[tuple[str, str]]:
        """Read input dependency files.

        Resolves paths from ticket.metadata.input_dependencies (list of
        glob patterns or explicit paths). Returns list of (name, content) tuples.
        """
        sections = []
        for dep_path in ticket.metadata.input_dependencies:
            resolved = self._resolve_path(dep_path, context)
            if resolved.exists():
                content = resolved.read_text(encoding="utf-8")
                sections.append((dep_path, content))
        return sections

    def _apply_budget(self, sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Truncate sections if total exceeds token budget.

        Strategy:
        - Estimate tokens as len(text) / 4
        - If over budget, truncate largest sections from the end
        - Always keep the prompt file (first section) intact
        - Add "[truncated]" marker to truncated sections
        """
        total_chars = sum(len(content) for _, content in sections)
        budget_chars = self.max_context_tokens * 4  # rough chars-to-tokens

        if total_chars <= budget_chars:
            return sections

        # Truncate from the end, largest first
        result = []
        remaining_budget = budget_chars
        for name, content in sections:
            if len(content) <= remaining_budget:
                result.append((name, content))
                remaining_budget -= len(content)
            else:
                truncated = content[:remaining_budget] + "\n\n[... truncated ...]"
                result.append((name, truncated))
                remaining_budget = 0
        return result
```

### 16.4 Input Dependency Resolution

Each step definition declares its input dependencies — files or directories
produced by previous phases that this step needs as context:

```python
@dataclass
class StepDefinition:
    # ... existing fields ...
    input_dependencies: list[str] = field(default_factory=list)
    # Examples:
    # - "output/analysis/source_code/flows/Business_Flows.json"
    # - "output/specifications/business/specs/{workpackage_id}_business_spec.md"
    # - "output/analysis/database/gen_src_db/*.sql"  (glob)
```

The `ContextAssembler` resolves these paths (with variable substitution and glob
expansion) and reads them into the prompt context.

### 16.5 Static vs. Dynamic Context

| Type | Source | When Resolved | Example |
|------|--------|---------------|---------|
| Static | Prompt file | At ticket creation | `prompts/03-business_extraction/phase_3.1.md` |
| Static | Agent definition | At registry startup | System prompt, role description |
| Dynamic | Previous deliverables | At execution time | `output/specifications/business/specs/WP-001_spec.md` |
| Dynamic | Scoping metadata | At execution time | Workpackage ID, flow ID, file scope |
| Dynamic | Tool output | At execution time (pre_tool) | Analysis DB query results |

---

## 17. LLM Provider Abstraction

### 17.1 Design Principle

The framework is LLM-agnostic. A provider interface abstracts the model call.
Implementations exist for Bedrock, OpenAI, local models, etc. The provider
handles retries, throttling, and token tracking.

### 17.2 Provider Interface

```python
@dataclass
class ModelConfig:
    """Configuration for an LLM call."""
    model_id: str = "anthropic.claude-sonnet-4-20250514"
    region: str = "us-east-1"
    temperature: float = 0.2
    max_output_tokens: int = 16_000
    max_tool_iterations: int = 50        # For agent loop mode
    retry_max_attempts: int = 5
    retry_base_delay: float = 2.0        # Exponential backoff base


@dataclass
class LLMResponse:
    """Response from a single LLM call."""
    success: bool
    content: str                          # Text output
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    model_id: str = ""
    elapsed: float = 0.0                  # Seconds
    error: Optional[str] = None


@dataclass
class AgentLoopResponse:
    """Response from an agent loop (LLM + tool calls)."""
    completed: bool
    final_text: str                       # Final LLM output after all tool calls
    files_written: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    metrics: Optional["StepMetrics"] = None
    error: Optional[str] = None


class LLMProvider(ABC):
    """Abstract LLM provider interface."""

    @abstractmethod
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model_config: ModelConfig,
    ) -> LLMResponse:
        """Single LLM call (no tools). Used by HybridExecutor."""
        ...

    @abstractmethod
    def run_agent_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list["AgentTool"],
        model_config: ModelConfig,
        working_directory: Path,
        max_iterations: int = 50,
    ) -> AgentLoopResponse:
        """Run an agent loop: LLM calls tools until done. Used by LLMExecutor."""
        ...
```

### 17.3 Bedrock Provider Implementation

```python
class BedrockProvider(LLMProvider):
    """AWS Bedrock Converse API provider.

    Handles:
    - Model invocation via boto3 bedrock-runtime client
    - Exponential backoff on throttling (ThrottlingException, TooManyRequestsException)
    - Transient error retry (timeouts, connection errors, 5xx)
    - Token usage tracking per call
    - Cost calculation based on model pricing
    """

    def __init__(self, region: str = "us-east-1"):
        import boto3
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def call(self, system_prompt: str, user_prompt: str, model_config: ModelConfig) -> LLMResponse:
        """Single Bedrock Converse call."""
        # Build messages, call self.client.converse(), parse response
        # Retry on throttle/transient errors with exponential backoff
        ...

    def run_agent_loop(self, system_prompt: str, user_prompt: str,
                       tools: list["AgentTool"], model_config: ModelConfig,
                       working_directory: Path, max_iterations: int = 50) -> AgentLoopResponse:
        """Bedrock Converse with tool_use loop.

        Loop:
        1. Call converse() with tool definitions
        2. If response contains tool_use blocks, execute each tool
        3. Append tool results to conversation
        4. Repeat until LLM produces end_turn or max_iterations reached
        """
        ...
```

### 17.4 Retry & Throttle Handling

```python
def _call_with_retry(self, request_fn, model_config: ModelConfig) -> dict:
    """Execute a Bedrock API call with exponential backoff.

    Retries on:
    - ThrottlingException / TooManyRequestsException
    - ServiceUnavailableException
    - Timeouts / connection errors

    Does NOT retry on:
    - ValidationException (bad request — fix the input)
    - AccessDeniedException (permissions — fix IAM)
    - ModelNotReadyException (model loading — wait longer)
    """
    for attempt in range(1, model_config.retry_max_attempts + 1):
        try:
            return request_fn()
        except Exception as exc:
            exc_str = str(exc).lower()
            is_retryable = any(k in exc_str for k in (
                "throttl", "too many", "rate",
                "timeout", "connection", "service unavailable",
            ))
            if is_retryable and attempt < model_config.retry_max_attempts:
                wait = min(model_config.retry_base_delay ** attempt, 60)
                time.sleep(wait)
                continue
            raise
```

### 17.5 Token Usage & Cost Tracking

```python
@dataclass
class StepMetrics:
    """Metrics for a single step execution."""
    step_id: str = ""
    agent_name: str = ""
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    requests: int = 0                     # Number of LLM API calls
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0

    def to_log_line(self) -> str:
        return (
            f"[{self.agent_name}] {self.model_id} "
            f"in={self.input_tokens:,} out={self.output_tokens:,} "
            f"${self.cost_usd:.4f} ({self.elapsed_seconds:.1f}s)"
        )


# Pricing table (per 1M tokens)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_1M, output_price_per_1M)
    "anthropic.claude-sonnet-4-20250514": (3.0, 15.0),
    "anthropic.claude-haiku-3-20250310": (0.25, 1.25),
    "anthropic.claude-opus-4-20250514": (15.0, 75.0),
}


def calculate_cost(input_tokens: int, output_tokens: int, model_id: str,
                   cache_write: int = 0, cache_read: int = 0) -> float:
    """Calculate USD cost for a model invocation."""
    prices = MODEL_PRICING.get(model_id, (3.0, 15.0))
    input_cost = (input_tokens / 1_000_000) * prices[0]
    output_cost = (output_tokens / 1_000_000) * prices[1]
    # Cache pricing: write = 1.25x input, read = 0.1x input
    cache_write_cost = (cache_write / 1_000_000) * prices[0] * 1.25
    cache_read_cost = (cache_read / 1_000_000) * prices[0] * 0.1
    return input_cost + output_cost + cache_write_cost + cache_read_cost
```


---

## 18. Agent Tool System

### 18.1 Design Principle

Tools are capabilities exposed to LLM agents during execution. They allow the
agent to interact with the filesystem, run commands, or query data. Tools are
sandboxed to the agent's working directory and subject to access controls.

Tools are only relevant for `LLMExecutor` agents (which run an autonomous tool
loop). `HybridExecutor` agents do their file reading deterministically in
`assemble_context()` and typically don't need runtime tools (or need only `write_file`).

### 18.2 AgentTool Protocol

```python
@dataclass
class ToolParameter:
    """Schema for a single tool parameter."""
    name: str
    type: str                             # "string", "integer", "boolean"
    description: str
    required: bool = True
    default: Optional[str] = None


class AgentTool(ABC):
    """Base class for tools available to LLM agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (used in LLM tool_use blocks)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description (sent to LLM in tool definitions)."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> list[ToolParameter]:
        """Parameter schema for this tool."""
        ...

    @abstractmethod
    async def execute(self, arguments: dict, context: "ToolContext") -> str:
        """Execute the tool with given arguments. Returns result as string."""
        ...

    def to_bedrock_schema(self) -> dict:
        """Convert to Bedrock Converse tool definition format."""
        properties = {}
        required = []
        for param in self.parameters:
            properties[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.required:
                required.append(param.name)
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                },
            }
        }


@dataclass
class ToolContext:
    """Runtime context for tool execution."""
    working_directory: Path               # Root directory for file operations
    workpackage_id: Optional[str] = None
    domain_name: Optional[str] = None
    # Tracking
    turns_since_write: int = 0
    total_tool_calls: int = 0
    files_written: list[str] = field(default_factory=list)
```

### 18.3 Built-In Tools

```python
class ReadFileTool(AgentTool):
    """Read a file from the working directory.

    Features:
    - Line range support (start_line, end_line)
    - Read cache (avoid re-reading same file)
    - Large file handling (outline for files > threshold)
    - Blocked path enforcement (binary files, protected dirs)
    - Directory detection (returns listing instead of error)
    """
    name = "read_file"
    description = "Read a file. Returns content or error message."
    parameters = [
        ToolParameter("path", "string", "Relative file path"),
        ToolParameter("start_line", "integer", "First line (1-based, optional)", required=False),
        ToolParameter("end_line", "integer", "Last line (1-based, optional)", required=False),
    ]


class WriteFileTool(AgentTool):
    """Write content to a file in the working directory.

    Features:
    - Creates parent directories automatically
    - Write guards (blocked paths: input/, analysis/, state files)
    - Tracks files written for deliverable validation
    - Invalidates read cache for written files
    """
    name = "write_file"
    description = "Write content to a file. Creates directories as needed."
    parameters = [
        ToolParameter("path", "string", "Relative file path"),
        ToolParameter("content", "string", "File content to write"),
    ]


class ListFilesTool(AgentTool):
    """List files in a directory (non-recursive, immediate children only)."""
    name = "list_files"
    description = "List files and directories. Non-recursive."
    parameters = [
        ToolParameter("directory", "string", "Relative directory path", required=False, default="."),
    ]


class SearchFileTool(AgentTool):
    """Search for a pattern in a file. Returns matching lines with context.

    Useful for finding specific content in large files without reading
    the entire file into the conversation history.
    """
    name = "search_file"
    description = "Search for a regex pattern in a file. Returns matches with context lines."
    parameters = [
        ToolParameter("path", "string", "File path to search"),
        ToolParameter("pattern", "string", "Regex pattern (case-insensitive)"),
        ToolParameter("context_lines", "integer", "Lines of context around matches", required=False, default="5"),
    ]


class ReadFilesTool(AgentTool):
    """Batch read multiple files in a single tool call.

    Reduces round-trips for agents that need to read several files.
    Subject to total batch size budget to prevent context overflow.
    """
    name = "read_files"
    description = "Read multiple files at once. Paths separated by newlines."
    parameters = [
        ToolParameter("paths", "string", "Newline-separated list of file paths"),
    ]
```

### 18.4 Tool Sandboxing & Access Control

```python
@dataclass
class ToolSandbox:
    """Defines access boundaries for agent tools.

    All file operations are restricted to the working directory.
    Certain paths are blocked for read or write based on the agent's role.
    """

    working_directory: Path
    read_blocked_patterns: list[str] = field(default_factory=lambda: [
        "*.dat", "*.ps", "*/ebcdic/*",     # Binary/EBCDIC data
        ".migration_state.json",            # Internal state
    ])
    write_blocked_patterns: list[str] = field(default_factory=lambda: [
        "output/analysis/*",                # Phase 1 outputs (read-only)
        "input/*",                          # Source inputs (read-only)
        ".migration_state.json",            # Internal state
    ])
    write_allowed_exceptions: list[str] = field(default_factory=lambda: [
        "*/Pod_Assignment.json",            # Phase 2.5 writes this
    ])

    def can_read(self, path: str) -> bool:
        """Check if a path is readable by the agent."""
        ...

    def can_write(self, path: str) -> bool:
        """Check if a path is writable by the agent."""
        ...
```

### 18.5 Tool Loop Control

The LLM provider's `run_agent_loop` must enforce limits to prevent runaway agents:

| Control | Threshold | Action |
|---------|-----------|--------|
| Max tool iterations | 50 (configurable) | Force stop, return partial result |
| Turns without write | 30 | Inject system nudge: "stop reading, write output now" |
| Repeated file reads | 5 reads of same file | Return cached + nudge |
| Total context budget | 180K tokens | Stop accepting new tool results |
| Elapsed time | 15 minutes | Force stop, return partial result |

---

## 19. Reviewer Pattern

### 19.1 Design Principle

Reviewer agents are specialized LLM agents that evaluate deliverables produced
by specialist agents. They return a structured verdict: approved or rejected with
feedback. The watcher uses this verdict to decide the next state transition.

Reviewers are NOT the same as human review (HITL). The flow is:
1. Specialist produces deliverables
2. Reviewer agent evaluates (automated quality check)
3. If reviewer approves AND hitl_required: ticket → AWAITING_REVIEW (human)
4. If reviewer approves AND NOT hitl_required: ticket → DONE (auto-approve)
5. If reviewer rejects: rework loop (specialist re-runs with feedback)

### 19.2 ReviewResult Model

```python
@dataclass
class ReviewResult:
    """Structured output from a reviewer agent."""
    approved: bool                        # True = deliverables pass quality bar
    feedback: str                         # Detailed feedback (always present)
    issues: list[str] = field(default_factory=list)  # Specific issues found
    rework_target: Optional[str] = None   # Step ID to rework (if not the immediately preceding step)
    confidence: float = 1.0               # 0.0-1.0 confidence in the verdict
```

### 19.3 ReviewerExecutor

```python
class ReviewerExecutor(HybridExecutor):
    """Reviewer agent that evaluates deliverables and returns a verdict.

    Context assembly:
    - Reads the deliverables produced by the specialist (the step it reviews)
    - Reads the original prompt/spec that the specialist was given
    - Reads any quality criteria from the step definition
    - Optionally reads reference materials (target specs, standards)

    Output parsing:
    - Expects the LLM to produce a structured response with APPROVED/REJECTED
    - Parses feedback and issues list from the response
    - Falls back to REJECTED if parsing fails (conservative)
    """

    def __init__(self, agent_name: str, reviewer_for: str, max_iterations: int = 3):
        self._agent_name = agent_name
        self.reviewer_for = reviewer_for      # Step ID this reviewer evaluates
        self.max_iterations = max_iterations

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def assemble_context(self, ticket: "Ticket", context: "ExecutionContext") -> PromptContext:
        """Assemble reviewer context: deliverables + original spec + criteria."""
        # 1. Read the deliverables from the specialist step
        specialist_deliverables = self._read_specialist_outputs(ticket, context)

        # 2. Read the original prompt that the specialist received
        original_prompt = self._read_specialist_prompt(ticket, context)

        # 3. Read quality criteria
        criteria = self._read_quality_criteria(ticket)

        # 4. Build reviewer prompt
        system_prompt = self._build_reviewer_system_prompt()
        user_prompt = self._build_reviewer_user_prompt(
            specialist_deliverables, original_prompt, criteria, ticket
        )

        return PromptContext(system_prompt=system_prompt, user_prompt=user_prompt)

    def post_process(self, llm_output: str, ticket: "Ticket", context: "ExecutionContext") -> dict[str, str]:
        """Parse LLM output into ReviewResult. Does not write files."""
        # Store the parsed ReviewResult on the context for the watcher to read
        self._last_review_result = self._parse_review_output(llm_output)
        return {}  # Reviewers don't write deliverable files

    def get_review_result(self) -> ReviewResult:
        """Return the parsed review result after execution."""
        return self._last_review_result

    def _parse_review_output(self, output: str) -> ReviewResult:
        """Parse structured review output from LLM.

        Expected format (flexible — parser handles variations):
        ```
        ## Verdict: APPROVED | REJECTED

        ## Feedback
        ...

        ## Issues
        - Issue 1
        - Issue 2
        ```
        """
        ...
```

### 19.4 Rework Loop Mechanics

When a reviewer rejects, the watcher orchestrates the rework loop:

```python
# In EventWatcher.handle_ready() — after agent execution:

if ticket.ticket_type == TicketType.REVIEWER_STEP:
    review_result = executor.get_review_result()

    if review_result.approved:
        # Reviewer approved — proceed to HITL or auto-approve
        if ticket.metadata.hitl_required:
            self.tracker.update_status(ticket.id, TicketStatus.AWAITING_REVIEW)
        else:
            self.handle_auto_approve(ticket)
    else:
        # Reviewer rejected — trigger rework
        iteration = ticket.metadata.iteration
        if iteration >= ticket.metadata.max_iterations:
            # Escalate — too many rework cycles
            self.tracker.update_status(ticket.id, TicketStatus.PAUSED)
            self.tracker.add_comment(ticket.id,
                f"Max iterations ({ticket.metadata.max_iterations}) reached. "
                f"Escalating to human.")
        else:
            # Rework: re-run the specialist with feedback
            self._trigger_rework(ticket, review_result)
```

### 19.5 Rework Strategies

| Strategy | When | Behavior |
|----------|------|----------|
| **In-place rework** (default) | Same ticket, increment iteration | Specialist re-runs with rejection feedback appended to prompt. Reviewer re-runs after. Status cycles: READY → IN_PROGRESS → (reviewer rejects) → READY → IN_PROGRESS → ... |
| **Remediation ticket** | Optional, for audit trail | Create child ticket with `ticket_type=REMEDIATION`. Original ticket stays in REJECTED. New ticket references parent. |
| **Targeted rework** | When `rework_target` is set | Instead of re-running the immediately preceding specialist, re-run a specific earlier step (e.g., reviewer for step 3.2 can target step 3.1 for rework). |

**In-place rework implementation** (recommended — simpler, less ticket noise):

```python
def _trigger_rework(self, reviewer_ticket: "Ticket", review_result: ReviewResult):
    """Re-run the specialist step with rejection feedback.

    The same ticket is reused. Iteration counter increments.
    Feedback is injected into the specialist's prompt on next execution.
    """
    # Determine which specialist to re-run
    rework_target = review_result.rework_target or reviewer_ticket.metadata.rework_target_step
    specialist_ticket = self.tracker.get_ticket(rework_target)

    # Store feedback as comment on the specialist ticket
    self.tracker.add_comment(specialist_ticket.id,
        f"## Rework Required (iteration {specialist_ticket.metadata.iteration + 1})\n\n"
        f"{review_result.feedback}\n\n"
        f"### Issues\n" + "\n".join(f"- {i}" for i in review_result.issues)
    )

    # Increment iteration
    specialist_ticket.metadata.iteration += 1
    self.tracker.update_metadata(specialist_ticket.id, specialist_ticket.metadata)

    # Move specialist back to READY (watcher will pick it up next cycle)
    self.tracker.update_status(specialist_ticket.id, TicketStatus.READY)

    # Move reviewer back to BACKLOG (blocked by specialist)
    self.tracker.update_status(reviewer_ticket.id, TicketStatus.BACKLOG)
```

### 19.6 Feedback Injection

When a specialist is re-run after rejection, the feedback must be included in
its prompt. The `ContextAssembler` handles this:

```python
def assemble(self, ticket: "Ticket", context: "ExecutionContext") -> PromptContext:
    # ... normal assembly ...

    # If this is a rework iteration, inject feedback
    if ticket.metadata.iteration > 1:
        feedback_comments = self._get_rework_feedback(ticket)
        prompt_context.append_section(
            "REWORK INSTRUCTIONS",
            f"This is iteration {ticket.metadata.iteration}. "
            f"Your previous output was rejected. Fix ONLY the issues below. "
            f"Do NOT regenerate from scratch.\n\n{feedback_comments}"
        )

    return prompt_context
```

---

## 20. Deliverable Validation

### 20.1 Design Principle

After an agent completes, its deliverables are validated before the ticket
advances. Validation is deterministic (no LLM) and fast. It catches obvious
failures (missing files, empty output, invalid JSON) before wasting human
review time.

### 20.2 ValidationResult Model

```python
@dataclass
class ValidationResult:
    """Result of deliverable validation."""
    passed: bool
    errors: list[str] = field(default_factory=list)    # Hard failures
    warnings: list[str] = field(default_factory=list)  # Non-blocking issues
```

### 20.3 DeliverableValidator Interface

```python
class DeliverableValidator:
    """Validates deliverables produced by an agent against expectations.

    Runs after agent execution, before status transitions to AWAITING_REVIEW.
    If validation fails, ticket goes to FAILED (not REJECTED — rejection is
    for quality issues caught by reviewers or humans, not missing files).
    """

    def __init__(self, custom_validators: dict[str, callable] = None):
        self.custom_validators = custom_validators or {}

    def validate(self, ticket: "Ticket", context: "ExecutionContext") -> ValidationResult:
        """Run all validations for a ticket's expected deliverables.

        Checks:
        1. Each expected deliverable exists
        2. Each file meets minimum size requirement
        3. File type validation (valid JSON, valid markdown structure, etc.)
        4. Custom validators (if configured for this step)
        """
        errors = []
        warnings = []

        for spec in ticket.metadata.expected_deliverables:
            path = self._resolve_path(spec.output_path, context)

            # Existence check
            if spec.file_type == "directory":
                if not path.is_dir():
                    errors.append(f"Expected directory not found: {spec.output_path}")
                    continue
                # Check directory is not empty
                if not any(path.iterdir()):
                    errors.append(f"Expected directory is empty: {spec.output_path}")
            else:
                if not path.is_file():
                    if spec.required:
                        errors.append(f"Expected file not found: {spec.output_path}")
                    else:
                        warnings.append(f"Optional file not found: {spec.output_path}")
                    continue

                # Size check
                size = path.stat().st_size
                if size < spec.min_size_bytes:
                    errors.append(
                        f"File too small: {spec.output_path} "
                        f"({size} bytes, minimum {spec.min_size_bytes})"
                    )

                # Type-specific validation
                type_errors = self._validate_file_type(path, spec.file_type)
                errors.extend(type_errors)

        # Custom validators
        for validator_name in ticket.metadata.custom_validators:
            if validator_name in self.custom_validators:
                custom_result = self.custom_validators[validator_name](ticket, context)
                errors.extend(custom_result.errors)
                warnings.extend(custom_result.warnings)

        return ValidationResult(
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_file_type(self, path: Path, file_type: str) -> list[str]:
        """Type-specific validation."""
        errors = []
        if file_type == "json":
            try:
                import json
                json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                errors.append(f"Invalid JSON in {path.name}: {e}")
        elif file_type == "markdown":
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                errors.append(f"Empty markdown file: {path.name}")
            # Check for at least one heading
            if not any(line.startswith("#") for line in content.splitlines()):
                errors.append(f"Markdown file has no headings: {path.name}")
        elif file_type == "sql":
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                errors.append(f"Empty SQL file: {path.name}")
        return errors
```

### 20.4 Custom Validators

Custom validators are registered by name and invoked for specific steps:

```python
# Example custom validators:

def validate_dag_no_cycles(ticket: "Ticket", context: "ExecutionContext") -> ValidationResult:
    """Validate that Workpackage_Planning.json has no dependency cycles."""
    import json
    path = context.working_directory / "output/analysis/workpackages/Workpackage_Planning.json"
    data = json.loads(path.read_text())
    # ... cycle detection logic ...
    return ValidationResult(passed=True)


def validate_pod_assignment_completeness(ticket: "Ticket", context: "ExecutionContext") -> ValidationResult:
    """Validate all workpackages are assigned to pods."""
    ...


# Registration:
validator = DeliverableValidator(custom_validators={
    "validate_dag_no_cycles": validate_dag_no_cycles,
    "validate_pod_assignment_completeness": validate_pod_assignment_completeness,
})
```

### 20.5 Validation vs. Review

| Aspect | Validation | Reviewer Agent | Human Review (HITL) |
|--------|-----------|----------------|---------------------|
| When | Immediately after agent | After validation passes | After reviewer approves |
| Who | Deterministic code | LLM agent | Human |
| Checks | File exists, valid format, size | Content quality, correctness, completeness | Business accuracy, strategic fit |
| On failure | Ticket → FAILED | Rework loop (specialist re-runs) | Ticket → REJECTED (rework or escalate) |
| Cost | Free (no LLM) | ~$0.01-0.10 per review | Human time |

---

## 21. Scoping Model

### 21.1 Design Principle

The pipeline operates at different scopes. Early phases are global (one execution
for the whole project). Later phases are per-workpackage or per-domain (one
execution per unit of work). The scoping model defines how tickets are created,
how context is injected, and how dependencies work across scopes.

### 21.2 Scope Types

| Scope | Meaning | Ticket Creation | Example |
|-------|---------|-----------------|---------|
| `global` | One set of tickets for the whole project | At init or when parent phase completes | Phase 1 Analysis, Phase 2 Planning |
| `per_workpackage` | One ticket per workpackage per step | When workpackage planning completes | Phase 3 Business Spec for WP-001 |
| `per_domain` | One ticket per domain per step | When domain discovery completes | ATX BRE v2 test gen for domain X |
| `per_pod` | One ticket per pod (group of WPs) | When pod assignment completes | Pod-A executes WP-001, WP-003, WP-007 |

### 21.3 Dynamic Ticket Creation

Scoped tickets are created dynamically when the prerequisite phase completes
and produces the information needed to enumerate the scope units:

```python
class DynamicTicketCreator:
    """Creates scoped tickets when milestone phases complete.

    Triggered by the watcher when a phase with `creates_next_phases` completes.
    Reads the phase's deliverables to discover scope units (workpackages, domains, pods).
    """

    def create_scoped_tickets(
        self,
        phase_def: "PhaseDefinition",
        completed_ticket: "Ticket",
        tracker: "TrackerBackend",
        pipeline: "PipelineConfig",
    ) -> list[str]:
        """Create tickets for the next phase based on scope.

        Returns list of created ticket IDs.
        """
        next_phases = pipeline.get_phases_created_by(completed_ticket.metadata.phase)

        created_ids = []
        for next_phase in next_phases:
            if next_phase.execution_scope == "per_workpackage":
                workpackages = self._discover_workpackages(completed_ticket)
                for wp_id in workpackages:
                    ids = self._create_phase_tickets_for_scope(
                        next_phase, tracker, scope_id=wp_id, scope_type="workpackage"
                    )
                    created_ids.extend(ids)

            elif next_phase.execution_scope == "per_domain":
                domains = self._discover_domains(completed_ticket)
                for domain in domains:
                    ids = self._create_phase_tickets_for_scope(
                        next_phase, tracker, scope_id=domain, scope_type="domain"
                    )
                    created_ids.extend(ids)

            elif next_phase.execution_scope == "per_pod":
                pods = self._discover_pods(completed_ticket)
                for pod_id, wp_list in pods.items():
                    ids = self._create_phase_tickets_for_scope(
                        next_phase, tracker, scope_id=pod_id, scope_type="pod",
                        extra_metadata={"workpackages": wp_list}
                    )
                    created_ids.extend(ids)

            else:  # global
                ids = self._create_phase_tickets_for_scope(
                    next_phase, tracker, scope_id=None, scope_type="global"
                )
                created_ids.extend(ids)

        return created_ids

    def _discover_workpackages(self, ticket: "Ticket") -> list[str]:
        """Read Workpackage_Planning.json to get WP IDs."""
        import json
        planning_path = Path(ticket.metadata.working_directory) / \
            "output/analysis/workpackages/Workpackage_Planning.json"
        data = json.loads(planning_path.read_text())
        return [f"WP-{item['workpackageId']:03d}" for item in data["migrationSequence"]]

    def _discover_domains(self, ticket: "Ticket") -> list[str]:
        """Discover domains from BRE v2 output directory."""
        bre_path = Path(ticket.metadata.working_directory) / "input/legacy/atx/bre_v2_output"
        return sorted([d.name for d in bre_path.iterdir() if d.is_dir()])
```

### 21.4 Scoped Ticket ID Convention

Scoped tickets include the scope unit in their ID for easy filtering:

| Scope | Ticket ID Pattern | Example |
|-------|-------------------|---------|
| Global | `ACM-{seq}` | `ACM-001` |
| Per-WP | `ACM-{seq}` with metadata `workpackage=WP-001` | `ACM-042` (metadata: WP-001, step-3.1) |
| Per-domain | `ACM-{seq}` with metadata `domain=ACCOUNT` | `ACM-087` (metadata: ACCOUNT, step-4.0) |

The ticket ID is always sequential (assigned by tracker). The scope is in metadata
and labels, not in the ID itself. This keeps the tracker backend simple.

### 21.5 Scoped State Keys

When tracking step results in state, the key includes the scope unit:

```python
# Global scope:
result_key = step.step_id                    # "phase_1_full_analyzer"

# Per-workpackage scope:
result_key = f"{workpackage_id}:{step.step_id}"  # "WP-001:phase_3.1_logic_extraction"

# Per-domain scope:
result_key = f"{domain_name}:{step.step_id}"     # "ACCOUNT:phase_4_atx_v2.0_test_design"
```

### 21.6 Cross-Scope Dependencies

Dependencies within the same scope unit are straightforward:
- WP-001's step-3.2 depends on WP-001's step-3.1 (same WP, sequential)

Cross-scope dependencies require all units of the previous scope to complete:
- Phase 5 (global) depends on Phase 4 (per-WP) → ALL WP tickets for Phase 4 must be DONE

```python
def _check_cross_scope_dependency(self, ticket: "Ticket", dep_phase_id: str) -> bool:
    """Check if all scoped tickets for a dependency phase are DONE."""
    dep_tickets = self.tracker.get_tickets_by_metadata(phase=dep_phase_id)
    return all(t.status == TicketStatus.DONE for t in dep_tickets)
```

### 21.7 Conditional Step Execution

Steps can be conditional on scope metadata (e.g., workpackage type):

```python
@dataclass
class StepDefinition:
    # ... existing fields ...
    workpackage_type: Optional[str] = None  # "flow", "job", or None (all)
    condition: Optional[str] = None         # Python expression evaluated at runtime
```

When creating scoped tickets, steps with a `workpackage_type` filter are only
created for matching workpackages:

```python
def _create_phase_tickets_for_scope(self, phase, tracker, scope_id, scope_type, extra_metadata=None):
    """Create tickets for each step in a phase, filtered by scope conditions."""
    for step in phase.steps:
        # Skip steps that don't match this scope's type
        if step.workpackage_type:
            wp_type = self._get_workpackage_type(scope_id)
            if wp_type != step.workpackage_type:
                continue  # Don't create ticket for this step

        # Create ticket
        ticket = self._build_ticket(step, scope_id, scope_type, extra_metadata)
        tracker.create_ticket(ticket)
```


---

## 22. Pipeline Definition (Declarative Phase/Step Config)

### 22.1 Design Principle

The pipeline is defined declaratively as an ordered list of phases with steps.
This definition is the single source of truth for what tickets to create, what
agents to assign, what deliverables to expect, and how phases depend on each other.

The framework loads this definition at startup and uses it to:
- Initialize the board (create tickets)
- Resolve dependencies (which tickets block which)
- Create scoped tickets dynamically (when milestone phases complete)
- Configure HITL gates (which steps require human review)

### 22.2 PhaseDefinition Model

```python
from pydantic import BaseModel
from typing import Optional


class DeliverableSpec(BaseModel):
    """Specification for an expected deliverable from a step."""
    name: str                             # Human-readable name
    output_path: str                      # Path template (supports {workpackage_id}, {domain_name})
    file_type: str = "markdown"           # markdown, json, sql, binary, directory
    required: bool = True
    min_size_bytes: int = 100
    per_workpackage: bool = False         # True if path is templated per scope unit


class QualityGateDefinition(BaseModel):
    """Quality gate checks run after all steps in a phase complete."""
    required_deliverables: list[str]      # Names from DeliverableSpec
    require_reviewer_approval: bool = True
    custom_validators: list[str] = []     # Names of registered custom validators


class StepDefinition(BaseModel):
    """Definition of a single step within a phase."""
    step_id: str                          # Unique within the phase
    display_name: str                     # Human-readable
    agent_name: str                       # Maps to AgentRegistry key
    prompt_file: str                      # Relative to prompts/ directory
    expected_deliverables: list[DeliverableSpec] = []
    input_dependencies: list[str] = []    # Paths/globs of files this step needs as input

    # Reviewer configuration
    is_reviewer: bool = False
    reviewer_for: Optional[str] = None    # Step ID this reviewer evaluates
    rework_target: Optional[str] = None   # Step ID to rework on rejection (default: preceding step)
    max_review_iterations: int = 3

    # Conditional execution
    optional: bool = False
    workpackage_type: Optional[str] = None  # "flow", "job", or None (all)

    # HITL configuration
    hitl_after: bool = True               # Require human review after this step completes
    auto_approve_on_validation: bool = False  # Skip HITL if validation passes


class PhaseDefinition(BaseModel):
    """Definition of a migration phase."""
    phase_id: str                         # Unique identifier
    display_name: str                     # Human-readable
    depends_on: list[str] = []            # Phase IDs that must be DONE before this starts
    steps: list[StepDefinition]
    quality_gate: QualityGateDefinition
    execution_scope: str = "global"       # "global", "per_workpackage", "per_domain", "per_pod"
    creates_next_phases: list[str] = []   # Phase IDs to create tickets for when this completes
    post_phase_hook: Optional[str] = None # Python method name to call after phase completes
```

### 22.3 Pipeline Variants

The framework supports multiple pipeline configurations for different use cases:

```python
def build_pipeline(mode: str = "full") -> list[PhaseDefinition]:
    """Build pipeline definition for the given mode.

    Modes:
    - "full": Complete migration pipeline (Phases 1-6)
    - "atx": ATX platform mode (Phase 0 + Phases 1-6)
    - "atx_bre_v2": ATX BRE v2 mode (Phase 0 + Phase 4-ATX + Phase 5 + Phase 6)
    - "single_wp": Single workpackage mode (no pods, direct WP execution)

    Returns ordered list of PhaseDefinitions.
    """
    ...
```

### 22.4 Pipeline as YAML (Optional)

For non-developer configuration, the pipeline can optionally be defined in YAML
and loaded into PhaseDefinition models:

```yaml
# pipeline.yaml
pipeline:
  name: "CardDemo Legacy Migration"
  mode: "full"
  phases:
    - id: "phase_1_analysis"
      name: "Source Code Analysis"
      scope: "global"
      depends_on: []
      creates_next_phases: ["phase_2_planning"]
      steps:
        - id: "phase_1_full_analyzer"
          name: "Full Analysis"
          agent: "full_analyzer"           # ToolExecutor
          prompt: "01_analysis/Sourcecode/01_run_tool_legacy_analyzer.md"
          hitl_after: false
          deliverables:
            - name: "Analysis Database"
              path: "output/analysis/analysis.db"
              type: "binary"
              min_size: 1024
```

The Python definition (in code) is the primary format. YAML is an alternative
loader for the same PhaseDefinition models.

---

## 23. Git Integration

### 23.1 Design Principle

Git provides checkpointing and auditability. Every significant state transition
creates a tag. Deliverables are committed after each step. The git history
becomes a timeline of the migration that can be inspected, diffed, or rolled back.

### 23.2 GitManager Interface

```python
class GitManager:
    """Manages git operations tied to the ticket lifecycle."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def tag(self, tag_name: str, message: str = "") -> None:
        """Create an annotated git tag at HEAD."""
        cmd = ["git", "tag", "-a", tag_name, "-m", message or tag_name]
        subprocess.run(cmd, cwd=self.repo_path, check=True)

    def commit_deliverables(self, paths: list[str], message: str) -> str:
        """Stage and commit deliverable files. Returns commit SHA."""
        for path in paths:
            subprocess.run(["git", "add", path], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.repo_path, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path,
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def commit_phase_outputs(self, phase_id: str, scope_id: str = "") -> str:
        """Commit all outputs for a phase. Stages output/ directory."""
        scope_label = f" ({scope_id})" if scope_id else ""
        message = f"ACM: {phase_id}{scope_label} completed"
        subprocess.run(["git", "add", "output/"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", message, "--allow-empty"],
                       cwd=self.repo_path, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path,
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def create_worktree(self, branch: str, path: str) -> None:
        """Create a git worktree for pod-scoped execution."""
        subprocess.run(["git", "branch", branch, "HEAD"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "worktree", "add", path, branch],
                       cwd=self.repo_path, check=True)

    def remove_worktree(self, path: str) -> None:
        """Remove a git worktree after pod completes."""
        subprocess.run(["git", "worktree", "remove", path, "--force"],
                       cwd=self.repo_path, check=True)

    def merge_worktree(self, branch: str, message: str = "") -> None:
        """Merge a pod branch back to main."""
        msg = message or f"Merge pod branch: {branch}"
        subprocess.run(["git", "merge", branch, "--no-ff", "-m", msg],
                       cwd=self.repo_path, check=True)
```

### 23.3 Tag Naming Convention

| Event | Tag Format | Example |
|-------|-----------|---------|
| Board initialized | `acm/initialized` | `acm/initialized` |
| Step started | `acm/{ticket_id}/started` | `acm/ACM-042/started` |
| Step completed | `acm/{ticket_id}/completed` | `acm/ACM-042/completed` |
| Step approved (human) | `acm/{ticket_id}/approved` | `acm/ACM-042/approved` |
| Phase completed | `acm/{phase_id}/done` | `acm/phase_3_business_spec/done` |
| Rework iteration | `acm/{ticket_id}/rework-{n}` | `acm/ACM-042/rework-2` |

### 23.4 Commit Convention

```
ACM-{ticket_id}: {step_display_name} [{scope_id}] completed

- Agent: {agent_name}
- Deliverables: {count} files
- Iteration: {n}
```

### 23.5 Failure and Recovery

If a commit fails (nothing to commit, merge conflict):
- Nothing to commit: `--allow-empty` flag, log warning, continue
- Merge conflict (worktree merge): ticket → FAILED, human resolves manually
- Git not available: log error, skip git operations, continue (git is optional)

---

## 24. Configuration & Project Setup

### 24.1 ProjectConfig Model

```python
class ProjectConfig(BaseModel):
    """Project-level configuration. Loaded from project-config.json."""

    project_name: str
    project_base_path: Path
    output_base_path: Optional[Path] = None  # Defaults to project_base_path

    # Source paths
    source_code: Path                     # Legacy source code directory
    database_source: Optional[Path] = None

    # Output paths (derived from output_base_path)
    @property
    def source_code_analysis_output(self) -> Path:
        return (self.output_base_path or self.project_base_path) / "output" / "analysis" / "source_code"

    @property
    def database_analysis_output(self) -> Path:
        return (self.output_base_path or self.project_base_path) / "output" / "analysis" / "database"

    @property
    def workpackage_base_path(self) -> Path:
        return (self.output_base_path or self.project_base_path) / "output" / "analysis" / "workpackages"

    # System descriptions
    legacy_system: "LegacySystem"
    target_system: "TargetSystem"

    # Execution settings
    max_pod_concurrency: int = 1
    default_model_id: str = "anthropic.claude-sonnet-4-20250514"
    aws_region: str = "us-east-1"
```

### 24.2 Watcher Configuration

```python
class WatcherConfig(BaseModel):
    """Configuration for the event watcher."""

    # Tracker
    tracker_backend: str = "sqlite"       # sqlite, vikunja, roundup, gitea
    tracker_config: dict = {}             # Backend-specific config

    # Pipeline
    pipeline_mode: str = "full"           # full, atx, atx_bre_v2, single_wp

    # Polling
    poll_interval_seconds: int = 30
    max_concurrent_agents: int = 3

    # HITL
    hitl_default: bool = True             # Default: require human review
    hitl_override_phases: dict[str, bool] = {}  # Per-phase override
    hitl_override_steps: dict[str, bool] = {}   # Per-step override

    # Execution
    executor_timeout_seconds: int = 900   # 15 min max per agent execution
    max_rework_iterations: int = 3        # Global default

    # Git
    git_enabled: bool = True
    git_tag_on_transitions: bool = True
    git_commit_on_completion: bool = True
```

### 24.3 Directory Structure Convention

```
project-root/
├── .acm/
│   ├── tracker.db                # SQLite tracker database
│   ├── project-config.json       # ProjectConfig
│   ├── watcher-config.yaml       # WatcherConfig
│   └── pipeline.yaml             # Optional YAML pipeline (alternative to Python)
├── input/
│   └── legacy/                   # Legacy source code and data
│       ├── legacy_code/          # COBOL, JCL, copybooks, etc.
│       └── atx/                  # ATX-specific inputs
├── output/
│   ├── analysis/                 # Phase 1-2 outputs (read-only after creation)
│   │   ├── source_code/
│   │   ├── database/
│   │   └── workpackages/
│   ├── specifications/           # Phase 3-4 outputs
│   │   ├── business/
│   │   ├── technical/
│   │   └── test_cases/
│   ├── gen_src/                  # Phase 5-6 generated source code
│   ├── gen_src_db/               # Generated database schemas
│   └── gen_src_test/             # Generated test code
├── prompts/                      # Prompt templates per phase
│   ├── 01_analysis/
│   ├── 02_workpackage/
│   ├── 03-business_extraction/
│   ├── 04_test_case_generation/
│   ├── 05_code_preparation/
│   └── 06_code_generation/
├── agents/                       # Agent definition files (markdown + frontmatter)
│   ├── full_analyzer.md
│   ├── business_specialist_requirements.md
│   ├── business_reviewer_logic_extraction.md
│   └── ...
└── tools/                        # Subprocess tools
    └── acm-tools/
        ├── scripts/
        └── tools/
```

### 24.4 Initialization Command

```bash
acm init --project ./project-config.json --tracker sqlite --pipeline full
```

This command:
1. Creates `.acm/` directory with tracker database and config files
2. Loads the pipeline definition
3. Creates initial tickets (Phase 1 only, or all if `--all-phases` flag)
4. Sets Phase 1 tickets to READY (no dependencies)
5. Creates git tag `acm/initialized`

---

## 25. Observability & Logging

### 25.1 Design Principle

Every action in the system is observable. The watcher logs state transitions.
Agents log tool calls and token usage. The dashboard displays real-time status.
Metrics are stored for cost analysis and performance optimization.

### 25.2 Structured Logging

```python
import logging
import json
from datetime import datetime, timezone

class StructuredLogger:
    """JSON-structured logger for machine-parseable event logging."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def event(self, event_type: str, **kwargs):
        """Log a structured event."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **kwargs,
        }
        self.logger.info(json.dumps(record))

# Usage:
log = StructuredLogger("acm.watcher")
log.event("ticket_transition", ticket_id="ACM-042", from_status="ready", to_status="in_progress")
log.event("agent_started", ticket_id="ACM-042", agent_name="business_specialist_requirements")
log.event("agent_completed", ticket_id="ACM-042", agent_name="business_specialist_requirements",
          elapsed_seconds=45.2, input_tokens=12000, output_tokens=3500, cost_usd=0.089)
log.event("deliverable_validated", ticket_id="ACM-042", passed=True, warnings=1)
log.event("hitl_waiting", ticket_id="ACM-042", step_id="phase_3.1_logic_extraction")
```

### 25.3 Per-Step Metrics Storage

```python
class MetricsStore:
    """Stores per-step execution metrics for reporting and cost analysis.

    Backed by the tracker database (additional table) or a separate metrics file.
    """

    def record(self, metrics: StepMetrics) -> None:
        """Store metrics for a completed step."""
        ...

    def get_phase_summary(self, phase_id: str) -> dict:
        """Aggregate metrics for a phase: total tokens, cost, time."""
        ...

    def get_project_summary(self) -> dict:
        """Aggregate metrics for the entire project."""
        ...

    def get_cost_breakdown(self) -> list[dict]:
        """Cost breakdown by phase, agent, and model."""
        ...
```

### 25.4 Dashboard Data Model

The web UI needs the following data from the tracker and metrics store:

| View | Data Source | Content |
|------|-------------|---------|
| Board (Kanban) | Tracker: tickets grouped by status | Columns: Backlog, Ready, In Progress, Review, Done |
| Phase Progress | Tracker: tickets by phase + status | Progress bars per phase |
| Step Detail | Tracker: single ticket | Description, metadata, comments, status history |
| Metrics | MetricsStore | Token usage, cost, timing per step/phase |
| Timeline | Tracker: status_history table | Gantt-like view of step execution over time |
| Cost Report | MetricsStore: aggregated | Total cost, cost per WP, cost per phase |

### 25.5 Human-Readable Console Output

For CLI usage (non-dashboard), the watcher produces human-readable output:

```
▶ ACM Event Watcher started. Polling every 30s...

▶ Ticket ACM-042 (Phase 3.1: WP-001 Logic Extraction) → IN_PROGRESS
    Agent: business_specialist_logic_extraction
    Working directory: ./output/specifications/business/traceability/

    📊 Completed in 45.2s | in=12,000 out=3,500 | $0.089
    ✓ Deliverables validated (1 file, 24KB)
    → AWAITING_REVIEW (human approval required)

▶ Ticket ACM-043 (Phase 3.1: WP-003 Logic Extraction) → IN_PROGRESS
    Agent: business_specialist_logic_extraction
    ...

⏸ Waiting for human review:
    - ACM-042: Phase 3.1 WP-001 Logic Extraction
    - ACM-043: Phase 3.1 WP-003 Logic Extraction

▶ Ticket ACM-042 → APPROVED (by human)
    → Unblocking: ACM-044 (Phase 3.1.1 WP-001 Logic Review)
```

---

## 26. Updated Project Structure

```
acm/
├── __init__.py
├── cli.py                        # CLI entry points (acm init, acm watch, acm serve, acm ticket)
│
├── models/
│   ├── __init__.py
│   ├── config.py                 # ProjectConfig, WatcherConfig
│   ├── enums.py                  # TicketStatus, TicketType, PhaseStatus
│   ├── ticket.py                 # Ticket, TicketMetadata
│   ├── phases.py                 # PhaseDefinition, StepDefinition, DeliverableSpec, QualityGateDefinition
│   ├── state.py                  # MigrationState (for resume support)
│   └── metrics.py                # StepMetrics, cost calculation
│
├── tracker/
│   ├── __init__.py
│   ├── backend.py                # TrackerBackend ABC
│   ├── sqlite_backend.py         # SqliteTracker implementation
│   ├── vikunja_backend.py        # VikunjaTracker implementation
│   └── web/
│       ├── app.py                # FastAPI + HTMX dashboard
│       ├── templates/            # Jinja2/HTMX templates
│       └── static/               # Minimal CSS
│
├── watcher/
│   ├── __init__.py
│   ├── event_watcher.py          # EventWatcher main loop
│   ├── dependency_resolver.py    # Dependency graph, unblock logic
│   └── ticket_creator.py         # DynamicTicketCreator
│
├── executor/
│   ├── __init__.py
│   ├── registry.py               # AgentRegistry
│   ├── base.py                   # AgentExecutor ABC, ExecutionResult, ExecutionContext
│   ├── tool_executor.py          # ToolExecutor base class
│   ├── llm_executor.py           # LLMExecutor base class (with tool loop)
│   ├── hybrid_executor.py        # HybridExecutor base class
│   └── reviewer_executor.py      # ReviewerExecutor (specialized)
│
├── context/
│   ├── __init__.py
│   ├── assembler.py              # ContextAssembler
│   └── prompt_context.py         # PromptContext model
│
├── providers/
│   ├── __init__.py
│   ├── base.py                   # LLMProvider ABC, ModelConfig, LLMResponse
│   └── bedrock.py                # BedrockProvider implementation
│
├── tools/
│   ├── __init__.py
│   ├── base.py                   # AgentTool ABC, ToolContext, ToolSandbox
│   ├── file_ops.py               # ReadFileTool, WriteFileTool, ListFilesTool, SearchFileTool
│   └── subprocess_tool.py        # SubprocessTool for shell commands
│
├── validation/
│   ├── __init__.py
│   ├── validator.py              # DeliverableValidator
│   └── custom_validators.py      # Project-specific validators
│
├── git/
│   ├── __init__.py
│   └── manager.py                # GitManager
│
├── pipeline/
│   ├── __init__.py
│   ├── builder.py                # build_pipeline() — Python phase definitions
│   └── loader.py                 # Optional YAML pipeline loader
│
├── observability/
│   ├── __init__.py
│   ├── logger.py                 # StructuredLogger
│   └── metrics_store.py          # MetricsStore
│
└── agents/                       # Concrete agent implementations (stubs initially)
    ├── __init__.py
    ├── full_analyzer.py          # ToolExecutor: runs legacy analysis tool
    ├── migration_planner.py      # ToolExecutor: runs migration planner tool
    ├── pod_partitioner.py        # ToolExecutor: runs pod partitioning tool
    ├── business_specialist.py    # HybridExecutor: context assembly + LLM
    ├── business_reviewer.py      # ReviewerExecutor: evaluates business specs
    ├── code_generation.py        # LLMExecutor: needs tool access for file ops
    └── ...                       # One file per agent (or grouped by role)
```

---

## 27. CLI Commands (Complete)

```bash
# --- Project Setup ---
acm init --project ./project-config.json --tracker sqlite --pipeline full
acm init --project ./project-config.json --tracker vikunja --pipeline atx_bre_v2

# --- Event Watcher ---
acm watch                                 # Start watcher with defaults from .acm/watcher-config.yaml
acm watch --poll-interval 10              # Override poll interval
acm watch --max-concurrent 5             # Override concurrency
acm watch --no-hitl                       # Disable all HITL gates (full auto mode)
acm watch --hitl-phases phase_3,phase_5   # HITL only for specific phases

# --- Tracker Web UI ---
acm serve                                 # Start dashboard on default port
acm serve --port 8080                     # Custom port

# --- Ticket Operations (manual / scripting / debugging) ---
acm ticket list                           # All tickets
acm ticket list --status ready            # Filter by status
acm ticket list --phase phase_3           # Filter by phase
acm ticket list --workpackage WP-001      # Filter by WP
acm ticket show ACM-042                   # Ticket detail
acm ticket approve ACM-042                # Move to APPROVED
acm ticket reject ACM-042 --comment "Missing error handling for edge case X"
acm ticket pause ACM-042                  # Move to PAUSED
acm ticket resume ACM-042                 # Move back to READY
acm ticket retry ACM-042                  # Move FAILED → READY (retry)

# --- Pipeline Info ---
acm pipeline show                         # Display phase/step tree
acm pipeline agents                       # List all registered agents
acm pipeline validate                     # Check pipeline definition for errors

# --- Metrics ---
acm metrics summary                       # Project-wide cost/token summary
acm metrics phase phase_3                 # Phase-level breakdown
acm metrics export --format csv           # Export for analysis
```

---

## 28. Implementation Priority (Updated)

### Phase 1: Core Framework (MVP — no agents, just the skeleton)

| # | Component | Description |
|---|-----------|-------------|
| 1 | `models/` | All Pydantic models (config, enums, ticket, phases, metrics) |
| 2 | `tracker/backend.py` | TrackerBackend ABC |
| 3 | `tracker/sqlite_backend.py` | SQLite implementation |
| 4 | `executor/base.py` | AgentExecutor ABC, ExecutionResult, ExecutionContext |
| 5 | `executor/registry.py` | AgentRegistry |
| 6 | `executor/tool_executor.py` | ToolExecutor base class |
| 7 | `executor/hybrid_executor.py` | HybridExecutor base class |
| 8 | `executor/llm_executor.py` | LLMExecutor base class |
| 9 | `executor/reviewer_executor.py` | ReviewerExecutor |
| 10 | `context/assembler.py` | ContextAssembler |
| 11 | `providers/base.py` | LLMProvider ABC |
| 12 | `providers/bedrock.py` | BedrockProvider (stub with retry logic) |
| 13 | `tools/base.py` | AgentTool ABC, ToolContext |
| 14 | `tools/file_ops.py` | Built-in tools (read, write, list, search) |
| 15 | `validation/validator.py` | DeliverableValidator |
| 16 | `git/manager.py` | GitManager |
| 17 | `watcher/event_watcher.py` | EventWatcher main loop |
| 18 | `watcher/dependency_resolver.py` | Dependency resolution |
| 19 | `watcher/ticket_creator.py` | Dynamic ticket creation |
| 20 | `pipeline/builder.py` | Pipeline definition (Python) |
| 21 | `cli.py` | `acm init` + `acm watch` + `acm ticket` |

### Phase 2: Dashboard & Observability

| # | Component | Description |
|---|-----------|-------------|
| 22 | `tracker/web/app.py` | FastAPI dashboard |
| 23 | `tracker/web/templates/` | HTMX board view, ticket detail, approve/reject |
| 24 | `observability/logger.py` | Structured logging |
| 25 | `observability/metrics_store.py` | Metrics storage and aggregation |
| 26 | `cli.py` additions | `acm serve` + `acm metrics` |

### Phase 3: Agent Implementations (migrate from current orchestrator)

| # | Component | Description |
|---|-----------|-------------|
| 27 | `agents/full_analyzer.py` | ToolExecutor: legacy analysis |
| 28 | `agents/migration_planner.py` | ToolExecutor: workpackage planning |
| 29 | `agents/pod_partitioner.py` | ToolExecutor: pod assignment |
| 30 | `agents/business_specialist.py` | HybridExecutor: business spec generation |
| 31 | `agents/business_reviewer.py` | ReviewerExecutor: business spec review |
| 32 | `agents/code_generation.py` | LLMExecutor: code gen with tool access |
| 33 | `agents/test_generation.py` | HybridExecutor: test case design |
| 34 | Additional agents | One per agent_name in the pipeline |

### Phase 4: Advanced Features

| # | Component | Description |
|---|-----------|-------------|
| 35 | `tracker/vikunja_backend.py` | Vikunja integration |
| 36 | Webhook listener | Replace polling for Vikunja/Gitea |
| 37 | Concurrent execution | Thread/process pool for parallel agents |
| 38 | Pod worktree integration | Git worktrees for pod-scoped execution |
| 39 | Pipeline YAML loader | Optional YAML pipeline definition |
| 40 | Cost optimization | Model routing (cheap model for simple steps) |

---

## End of Specification

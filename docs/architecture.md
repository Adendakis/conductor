# Architecture

## Component Overview

```
┌─────────────────────────────────────────────┐
│            TRACKER (SQLite)                  │
│  Tickets → Status → Dependencies → Comments │
└──────────────────┬──────────────────────────┘
                   │ poll every N seconds
                   ▼
┌─────────────────────────────────────────────┐
│           EVENT WATCHER                      │
│  Picks up READY tickets                      │
│  Delegates to Agent Registry                 │
│  Validates deliverables                      │
│  Manages HITL gates + rework loops           │
│  Creates next-phase tickets on completion    │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│         AGENT REGISTRY                       │
│  Resolves agent_name → Executor instance     │
│  User agents loaded via agents_module        │
│  Fallback: NoOpExecutor for unregistered     │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│         GIT MANAGER                          │
│  Tags on transitions                         │
│  Commits deliverables                        │
│  Worktrees for parallel pods                 │
└─────────────────────────────────────────────┘
```

## Ticket Lifecycle

```
BACKLOG → READY → IN_PROGRESS → AWAITING_REVIEW → APPROVED → DONE
                       ↓                              ↓
                    FAILED                         REJECTED
                       ↓                              ↓
                  (retry → READY)              (rework → READY)
```

- **BACKLOG**: Dependencies not met yet
- **READY**: All blockers DONE, waiting for agent pickup
- **IN_PROGRESS**: Agent is executing
- **AWAITING_REVIEW**: Agent completed, waiting for human approval
- **APPROVED**: Human approved (transient — moves to DONE immediately)
- **DONE**: Fully complete, dependents unblocked
- **REJECTED**: Human rejected — triggers rework loop
- **FAILED**: Agent error — human can retry
- **PAUSED**: Human paused — watcher ignores until resumed

## Design Principles

1. **Stateless watcher**: If it crashes, restart it. It reads board state and resumes.
2. **Tracker is source of truth**: No state files. The board IS the state.
3. **Git tags are checkpoints**: Every transition creates a tag. Any point is restorable.
4. **Human-in-the-loop = ticket transition**: Approve, reject, pause — all via the tracker.
5. **Tracker-agnostic**: An abstraction layer allows swapping backends.

## Async Watcher

The `AsyncEventWatcher` uses `asyncio` for concurrent agent dispatch:

- `asyncio.Semaphore(max_concurrent_agents)` limits parallelism
- Agents execute in threads via `asyncio.to_thread()` (executors are synchronous)
- In-flight tracking prevents double-dispatch
- Stale ticket detection skips actively running tickets

## Progressive Ticket Creation

Phases define `creates_next_phases`. When all tickets in a phase reach DONE,
the watcher reads the phase's deliverables (e.g., `Workpackage_Planning.json`)
and creates tickets for the next phases. Per-workpackage phases create one set
of tickets per workpackage discovered.

## Pod Worktrees

For parallel pod execution on the same machine:

1. Each pod gets a git worktree (`worktrees/pod-a/`)
2. Agents write deliverables into the worktree
3. After pod completes, merge branch back to main
4. If merge conflicts: ticket → FAILED, human resolves
5. Cleanup: remove worktree and delete branch

## Plugin Architecture

Conductor is a framework — agents live in the user's project:

```
conductor (pip install)     ← framework
my-project/                 ← user's project
├── agents/__init__.py      ← def register(registry): ...
├── pipeline.yaml           ← workflow definition
└── prompts/                ← prompt templates
```

The `agents_module` config tells conductor which Python module to import.
The module's `register()` function adds agents to the registry.

## Package Structure

```
conductor/
├── models/          ← Pydantic data models (Ticket, Config, Phase, Metrics)
├── tracker/         ← TrackerBackend ABC + SQLite implementation + web dashboard
├── watcher/         ← EventWatcher (sync) + AsyncEventWatcher + dependency resolver
├── executor/        ← AgentExecutor ABC + Tool/Hybrid/LLM/Reviewer executors + registry
├── context/         ← ContextAssembler + PromptContext
├── providers/       ← LLMProvider ABC + BedrockProvider
├── tools/           ← AgentTool ABC + file ops (read/write/list/search)
├── validation/      ← DeliverableValidator + custom validators
├── git/             ← GitManager + WorktreeManager
├── pipeline/        ← Pipeline builder + YAML loader + validator
├── observability/   ← StructuredLogger + MetricsStore
└── agents/          ← Built-in generic executors (NoOp, Echo, Shell)
```

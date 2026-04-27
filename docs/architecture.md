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

1. A phase with `post_phase_hook: "setup_and_execute_pods"` completes
2. The watcher reads `Pod_Assignment.json` (generic format)
3. Each pod gets a git worktree (`worktrees/pod-a/`) on branch `pod/pod-a`
4. Each workpackage gets a sub-branch (`wp/pod-a/WP-001`)
5. WPs within a pod execute sequentially; pods execute in parallel
6. After each WP completes → merge WP branch to pod branch
7. After all pods complete → merge pods to main (respecting `merge_order`)
8. Cleanup: remove worktrees and delete branches

**Generic Pod Assignment format** (what conductor reads):

```json
{
  "pods": {
    "pod-a": { "workpackages": ["WP-001", "WP-002"] },
    "pod-b": { "workpackages": ["WP-003", "WP-006"] }
  },
  "merge_order": [["pod-a", "pod-b"]]
}
```

Extra fields in pod objects are ignored — project-specific agents can include
whatever metadata they need. The `merge_order` field is optional; if absent,
pods merge in any order.

**Merge conflict handling**: WP→pod conflict marks the ticket FAILED with the
conflicted file list. Pod→main conflict stops further merges and marks the
relevant ticket FAILED. In both cases the merge is aborted and the repo stays clean.

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

## LLM Agent Loop

When an `LLMExecutor` agent runs, the framework applies several optimizations
before and during the LLM tool loop:

```
LLMExecutor.execute()
  ├── get_user_prompt()
  ├── _resolve_prompt_references()     ← scan prompt for file paths, inline content
  ├── _inline_preloaded_files()        ← inline agent-declared files
  ├── append _READ_ONCE_INSTRUCTION    ← "don't re-read files" instruction
  └── provider.run_agent_loop()
        ├── build sandbox (with agent overrides)
        └── for each iteration:
              ├── check sliding window → truncate if over threshold
              ├── call LLM (converse)
              ├── log reasoning + tool calls
              ├── execute tools (with sandbox)
              └── repeat until end_turn or max_iterations
```

**Default tools** provided to every LLM agent: `read_file`, `read_files` (batch,
200KB budget), `write_file`, `list_files`, `search_file`.

**Optional tools** agents can opt into: `execute_command` (sandboxed shell with
allowlist and timeout).

**Sandbox**: file access is controlled by `ToolSandbox`. The default blocks writes
to `output/analysis/*` and `input/*`. Agents override via `get_sandbox_config()`.

**Sliding window**: the provider estimates conversation token count before each
LLM call. If it exceeds `history_trigger_tokens`, oldest messages are dropped
(keeping the original prompt and recent context). A context note is inserted so
the LLM knows history was trimmed.

See the [LLM Usage Guide](llm-usage.md) for configuration details.

## Package Structure

```
conductor/
├── models/          ← Pydantic data models (Ticket, Config, Phase, Metrics)
├── tracker/         ← TrackerBackend ABC + SQLite implementation + web dashboard
├── watcher/         ← EventWatcher (sync) + AsyncEventWatcher + dependency resolver
├── executor/        ← AgentExecutor ABC + Tool/Hybrid/LLM/Reviewer executors + registry
├── context/         ← ContextAssembler + PromptContext
├── providers/       ← LLMProvider ABC + BedrockProvider + sliding window history
├── tools/           ← AgentTool ABC + file ops (read/write/list/search/batch) + shell
├── validation/      ← DeliverableValidator + custom validators
├── git/             ← GitManager + WorktreeManager
├── pipeline/        ← Pipeline builder + YAML loader + validator
├── observability/   ← StructuredLogger + MetricsStore
└── agents/          ← Built-in generic executors (NoOp, Echo, Shell)
```

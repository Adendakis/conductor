<p align="center">
  <img src="docs/conductor_logo.png" alt="Conductor" width="200">
</p>

# Conductor

An event-driven orchestration framework where an issue board is the control plane.
You define pipelines in YAML, plug in any agent (scripts, LLMs, tools, remote workers),
and conductor handles scheduling, dependencies, human review gates, rework loops,
and git checkpointing — all visible on a real-time Kanban dashboard.

## Quick Start

```bash
# Install
pip install -e .

# Create a new project
conductor new-project my-pipeline
cd my-pipeline

# Initialize the board, start the dashboard, start the watcher
conductor init
conductor serve --port 8080
conductor watch-async
```

Open http://localhost:8080 to see the Kanban board.

## How It Works

1. **Define a pipeline** in `pipeline.yaml` — phases, steps, agents, dependencies
2. **Write agents** in Python — subclass one of four executor types
3. **Run `conductor init`** — creates tickets from the pipeline
4. **Run `conductor watch-async`** — picks up READY tickets, runs agents, manages lifecycle
5. **Approve/reject on the dashboard** — human-in-the-loop gates control quality

Tickets flow through: BACKLOG → READY → IN_PROGRESS → AWAITING_REVIEW → DONE

## Agent Types

| Type | Use Case | LLM? |
|------|----------|------|
| `ToolExecutor` | Run a script or subprocess | No |
| `HybridExecutor` | Assemble context + single LLM call | Yes (1 call) |
| `LLMExecutor` | Autonomous agent with tool loop | Yes (multi-turn) |
| `ReviewerExecutor` | Evaluate deliverables, return verdict | Yes |

Agents can be anything: a shell script, a Python function, an LLM call to
Bedrock/OpenAI, a [CAO](https://github.com/awslabs/cli-agent-orchestrator/)
tmux session, or a remote HTTP worker.

## Key Features

- **Progressive ticket creation** — later phases are created as earlier ones complete
- **Parallel execution** — async watcher with semaphore-limited concurrency
- **Human-in-the-loop** — approve/reject on a dark-themed Kanban dashboard
- **Rework loops** — reviewer rejects → specialist re-runs with feedback
- **Stale detection** — stuck tickets are automatically reset
- **Git checkpointing** — tags and commits at every transition
- **Pod worktrees** — parallel pods with merge conflict detection
- **Pipeline validation** — catches cycles, bad references, duplicates
- **Plugin system** — agents live in your project, not in conductor

## Documentation

- [Getting Started](docs/getting-started.md)
- [Pipeline Reference](docs/pipeline-reference.md)
- [Writing Agents](docs/writing-agents.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)

## CLI Reference

```bash
conductor new-project <name>       # Scaffold a project
conductor init [--reset]           # Create tickets from pipeline
conductor serve --port 8080        # Start the dashboard
conductor watch-async              # Start the async watcher
conductor watch                    # Start the sync watcher

conductor ticket list [--status ready] [--phase phase_3]
conductor ticket show COND-001
conductor ticket approve COND-001
conductor ticket reject COND-001 -c "Needs more detail"
conductor ticket pause/resume/retry COND-001

conductor pipeline show            # Display phase/step tree
conductor pipeline agents          # List all agents
conductor pipeline validate        # Check for errors
```

## Project Structure

```
conductor/                    ← this repo
├── conductor/                ← Python package
│   ├── models/               ← Data models (Pydantic)
│   ├── tracker/              ← SQLite backend + web dashboard
│   ├── watcher/              ← Event loop + dependency resolver
│   ├── executor/             ← Agent base classes + registry
│   ├── context/              ← Prompt assembly
│   ├── providers/            ← LLM abstraction (Bedrock)
│   ├── tools/                ← File ops for LLM agents
│   ├── validation/           ← Deliverable validators
│   ├── git/                  ← Git manager + worktrees
│   ├── pipeline/             ← Builder + YAML loader + validator
│   ├── observability/        ← Structured logging + metrics
│   └── agents/               ← Built-in generic executors
├── tests/                    ← 20 pytest tests
├── examples/
│   ├── demo-project/         ← Minimal scaffold
│   └── code-migration/       ← Full demo (5-min video recording)
└── docs/                     ← Documentation
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT

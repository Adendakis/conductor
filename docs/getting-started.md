# Getting Started

## Installation

```bash
pip install -e .
```

This installs the `conductor` CLI and Python library.

**Dependencies**: Python 3.11+, pydantic, click, fastapi, uvicorn, boto3, jinja2.

## Create Your First Project

```bash
conductor new-project my-pipeline
cd my-pipeline
```

This creates:

```
my-pipeline/
├── .conductor/config.yaml    ← project configuration
├── pipeline.yaml             ← pipeline definition
├── agents/
│   ├── __init__.py           ← agent registration
│   └── example_agent.py      ← starter agent
├── prompts/example.md        ← prompt template
└── output/                   ← deliverables directory
```

## Run It

```bash
# Create tickets from the pipeline
conductor init

# Start the dashboard (open http://localhost:8080)
conductor serve --port 8080

# Start the watcher (processes tickets)
conductor watch-async
```

The watcher picks up READY tickets, runs agents, validates deliverables,
and moves tickets through the lifecycle.

## What Happens

1. `conductor init` reads `pipeline.yaml` and creates tickets for the first phase
2. The watcher picks up READY tickets and runs the assigned agent
3. If `hitl_after: true`, the ticket moves to AWAITING_REVIEW — approve on the dashboard
4. When a phase completes, the next phase's tickets are created automatically
5. Repeat until all phases are done

## Reset and Re-run

```bash
conductor init --reset    # Wipes the board and starts fresh
```

## Try the Full Demo

For a richer example with parallel workpackages and human review gates:

```bash
cd examples/code-migration
conductor init --reset
conductor serve --port 8080    # in another terminal
conductor watch-async          # in another terminal
```

See [examples/code-migration/README.md](../examples/code-migration/README.md) for the full walkthrough.

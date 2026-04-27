# Getting Started

## Installation

```bash
pip install -e .
```

This installs the `conductor` CLI and Python library.

**Optional extras:**
```bash
pip install -e ".[bedrock]"    # AWS Bedrock LLM provider (adds boto3)
pip install -e ".[dev]"        # pytest for running tests
```

**Core dependencies**: Python 3.11+, pydantic, click, fastapi, uvicorn, jinja2.

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
│   └── example/
│       ├── __init__.py
│       ├── agent.py          ← starter agent
│       └── prompts/
│           └── task.md       ← prompt template
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

## Delete Individual Tickets

```bash
conductor ticket delete COND-004              # delete one ticket (asks for confirmation)
conductor ticket delete COND-004 -y           # skip confirmation
conductor ticket delete --phase phase_3       # delete all tickets in a phase
conductor ticket delete --phase phase_3 -y    # skip confirmation
```

Deleting a ticket removes its comments, status history, metrics, and dependency
links. Other tickets that depended on the deleted ticket will have those links
removed.

## Try the Full Demo

For a richer example with parallel workpackages and human review gates:

```bash
cd examples/code-migration
conductor init --reset
conductor serve --port 8080    # in another terminal
conductor watch-async          # in another terminal
```

For an AI-powered example with dynamic fan-out and Bedrock LLM calls:

```bash
pip install -e ".[bedrock]"
cd examples/daily-briefing
conductor init --reset
conductor serve --port 8080    # in another terminal
conductor watch-async          # in another terminal
# Then approve the setup ticket on the dashboard with your city + topic selections
```

See [examples/code-migration/README.md](../examples/code-migration/README.md) and
[examples/daily-briefing/README.md](../examples/daily-briefing/README.md) for full walkthroughs.

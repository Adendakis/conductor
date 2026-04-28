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

---

## Setting Up a Real Project

The scaffolded project is a starting point. Here's how to build a real pipeline.

### Step 1: Define Your Pipeline

Edit `pipeline.yaml` to describe your workflow. A pipeline is a list of phases,
each containing steps that map to agent executions.

```yaml
pipeline:
  name: "My Data Pipeline"
  phases:
    - id: "phase_1"
      name: "Data Collection"
      scope: "global"
      creates_next_phases: ["phase_2"]
      steps:
        - id: "collect"
          name: "Collect Data"
          agent: "data_collector"
          prompt: "agents/collector/prompts/collect.md"
          deliverables:
            - path: "output/raw_data.json"
              type: "json"
          hitl_after: true

    - id: "phase_2"
      name: "Processing"
      scope: "per_workpackage"
      steps:
        - id: "process"
          name: "Process Batch"
          agent: "batch_processor"
          prompt: "agents/processor/prompts/process.md"
          deliverables:
            - path: "output/processed/{wp_id}/result.json"
              type: "json"
          hitl_after: false
```

See [Pipeline Reference](pipeline-reference.md) for all available fields.

### Step 2: Write Your Agents

Each agent is a Python class in `agents/`. Choose the right base class:

| Base Class | When to use |
|------------|-------------|
| `AgentExecutor` | Direct — do anything |
| `ToolExecutor` | Run a subprocess |
| `HybridExecutor` | Context assembly + single LLM call |
| `LLMExecutor` | Autonomous agent with tool loop |
| `ReviewerExecutor` | Evaluate deliverables, return verdict |

```python
# agents/collector/agent.py
from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult

class DataCollectorAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "data_collector"

    def execute(self, ticket, context):
        # Your logic here — call APIs, read files, run tools
        output_path = context.working_directory / "output/raw_data.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"data": []}')
        return ExecutionResult(
            success=True,
            summary="Data collected",
            deliverables_produced=["output/raw_data.json"],
        )
```

See [Writing Agents](writing-agents.md) for all executor types and override points.

### Step 3: Register Agents

Your `agents/__init__.py` must expose a `register()` function:

```python
# agents/__init__.py
from conductor.executor.registry import AgentRegistry
from .collector.agent import DataCollectorAgent
from .processor.agent import BatchProcessorAgent

def register(registry: AgentRegistry):
    registry.register(DataCollectorAgent())
    registry.register(BatchProcessorAgent())
```

### Step 4: Add Scope Discovery (if using per-workpackage phases)

If your pipeline has phases with `scope: per_workpackage`, `per_pod`, or
`per_domain`, conductor needs to know how to discover those scope units.
Implement the `ScopeDiscovery` interface:

```python
# agents/scope_discovery.py
import json
from pathlib import Path
from conductor.watcher.scope_discovery import ScopeDiscovery

class MyScopeDiscovery(ScopeDiscovery):
    def discover_workpackages(self, working_directory: Path) -> list[str]:
        # Read your project's data to find workpackage IDs
        data_path = working_directory / "output/raw_data.json"
        data = json.loads(data_path.read_text())
        return [item["id"] for item in data.get("batches", [])]

    def discover_pods(self, working_directory: Path) -> list[str]:
        return []  # No pods in this project
```

Register it in `agents/__init__.py`:

```python
from .scope_discovery import MyScopeDiscovery

def register(registry: AgentRegistry):
    registry.register(DataCollectorAgent())
    registry.register(BatchProcessorAgent())
    registry.set_scope_discovery(MyScopeDiscovery())
```

If you don't register a `ScopeDiscovery`, conductor uses a default that returns
empty lists — per-workpackage phases will create no tickets.

### Step 5: Add Custom Validators (optional)

If your deliverables need project-specific validation beyond file existence
and size checks, register custom validators:

```python
# agents/validators.py
from conductor.validation.validator import ValidationResult

def validate_data_schema(ticket, context):
    """Check that output JSON matches expected schema."""
    # Your validation logic
    return ValidationResult(passed=True)
```

Register in `agents/__init__.py`:

```python
def register(registry: AgentRegistry):
    registry.register(DataCollectorAgent())
    registry.register_validator("validate_data_schema", validate_data_schema)
```

Reference the validator in `pipeline.yaml`:

```yaml
quality_gate:
  custom_validators: ["validate_data_schema"]
```

### Step 6: Configure and Run

Edit `.conductor/config.yaml`:

```yaml
agents_module: "agents"
pipeline: "pipeline.yaml"

tracker:
  backend: "sqlite"

# Optional: LLM provider for agents that use LLMExecutor or HybridExecutor
providers:
  type: "bedrock"
  region: "us-east-1"

settings:
  poll_interval_seconds: 10
  hitl_default: true
```

Then:

```bash
conductor init
conductor serve --port 8080
conductor watch-async
```

### Project Structure (complete example)

```
my-pipeline/
├── .conductor/
│   └── config.yaml
├── pipeline.yaml
├── agents/
│   ├── __init__.py              ← register(registry) — agents, scope, validators
│   ├── scope_discovery.py       ← ScopeDiscovery implementation (if needed)
│   ├── validators.py            ← custom validators (if needed)
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── prompts/
│   │       └── collect.md
│   └── processor/
│       ├── __init__.py
│       ├── agent.py
│       └── prompts/
│           └── process.md
└── output/
```

---

## Reference Examples

| Example | What it demonstrates |
|---------|---------------------|
| [demo-project](../examples/demo-project/) | Minimal scaffold — single phase, one agent |
| [code-migration](../examples/code-migration/) | Progressive phases, parallel WPs, HITL gates, reviewer pattern, ScopeDiscovery + custom validators |
| [daily-briefing](../examples/daily-briefing/) | Dynamic fan-out/fan-in, human input form, AI agents via Bedrock |

The `code-migration` example includes a complete `ScopeDiscovery` implementation
(`agents/scope_discovery.py`) and custom validators (`agents/validators.py`) that
serve as a reference for building your own.

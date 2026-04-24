# Configuration

## Project Configuration

Located at `.conductor/config.yaml` in your project root.

```yaml
agents_module: "agents"
pipeline: "pipeline.yaml"

tracker:
  backend: "sqlite"

# LLM Provider (optional)
providers:
  type: "bedrock"
  region: "us-east-1"

settings:
  poll_interval_seconds: 30
  hitl_default: true
  stale_ticket_threshold_seconds: 1800
```

## LLM Providers

Conductor creates the LLM provider from config at watcher startup and passes
it to all agents via `context.llm_provider`. Agents using `HybridExecutor` or
`LLMExecutor` get the provider automatically.

### Single Provider

```yaml
providers:
  type: "bedrock"
  region: "us-east-1"
```

Requires: `pip install conductor[bedrock]`

### Provider Pool (multi-region failover)

```yaml
providers:
  pool:
    strategy: "fallback"       # try in order, next on failure
    providers:
      - type: "bedrock"
        region: "us-east-1"
        label: "primary"
      - type: "bedrock"
        region: "us-west-2"
        label: "secondary"
```

Strategies: `fallback` (try in order) or `round_robin` (distribute evenly).

### Per-Agent Provider Selection

Agents can request a specific provider from the pool by overriding `get_model_config()`:

```python
def get_model_config(self):
    from conductor.providers.base import ModelConfig
    return ModelConfig(
        model_id="anthropic.claude-haiku-3-20250310",
        preferred_provider="secondary",  # label from pool config
    )
```

The pool tries the preferred provider first, falls back to others if unavailable.

### No Provider

If `providers` is not set, `context.llm_provider` is `None`. Agents that need
an LLM must create their own provider internally.

## WatcherConfig Fields

| Field | Default | Description |
|-------|---------|-------------|
| `poll_interval_seconds` | 30 | Seconds between poll cycles |
| `max_concurrent_agents` | 3 | Max agents running simultaneously (async watcher) |
| `hitl_default` | true | Default HITL setting for all tickets |
| `hitl_override_phases` | {} | Per-phase HITL override: `{"phase_1": false}` |
| `hitl_override_steps` | {} | Per-step HITL override: `{"step_1_1": false}` |
| `stale_ticket_threshold_seconds` | 1800 | Reset IN_PROGRESS tickets older than this |
| `executor_timeout_seconds` | 900 | Max time for a single agent execution |
| `max_rework_iterations` | 3 | Max rework cycles before escalation |
| `git_enabled` | true | Enable git tagging and commits |
| `git_tag_on_transitions` | true | Create git tags on status changes |
| `git_commit_on_completion` | true | Commit deliverables after agent completes |

## HITL (Human-in-the-Loop) Configuration

HITL is configured at multiple levels (highest priority first):

1. **Per-step override** in WatcherConfig: `hitl_override_steps`
2. **Per-phase override** in WatcherConfig: `hitl_override_phases`
3. **Per-ticket** in pipeline.yaml: `hitl_after: true/false`
4. **Global default** in WatcherConfig: `hitl_default`

A human can always override by moving a ticket directly to APPROVED or REJECTED
on the dashboard, regardless of configuration.

## Agent Module Loading

The `agents_module` field specifies a Python module path that conductor imports
at startup. The module must expose:

```python
def register(registry: AgentRegistry) -> None:
    registry.register(MyAgent())
```

If `agents_module` is not set or the module can't be imported, conductor falls
back to built-in generic executors (NoOp, Echo, Shell).

## Git Configuration

Git operations are optional. If git is not available or the working directory
is not a git repo, conductor logs a warning and continues without git.

Tags follow the convention: `conductor/{ticket_id}/started`, `conductor/{ticket_id}/completed`, `conductor/{ticket_id}/approved`.

## Dashboard

```bash
conductor serve --port 8080
```

The dashboard auto-refreshes every 5 seconds. It pauses auto-refresh when a
ticket detail modal is open and resumes when closed.

Filters (phase, workpackage, status) are preserved during auto-refresh.

## Logging

Conductor logs to console (human-readable) and `.conductor/conductor.log` (JSON).

```bash
conductor watch-async --log-level DEBUG          # verbose console
conductor watch-async --log-json                 # JSON console output
conductor watch-async --log-file my.log          # custom log file path
```

All modules using `logging.getLogger(__name__)` inherit the config — including
user agents. No setup needed in agent code:

```python
import logging
log = logging.getLogger(__name__)

class MyAgent(AgentExecutor):
    def execute(self, ticket, context):
        log.info(f"Processing {ticket.id}")
```

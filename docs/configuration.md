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
  poll_interval_seconds: 10
  max_concurrent_agents: 3
  hitl_default: true
  stale_ticket_threshold_seconds: 1800
```

Settings from `config.yaml` are used as defaults by the CLI. Explicit CLI flags
override config.yaml values:

```bash
# Uses config.yaml poll_interval_seconds (e.g., 10)
conductor watch-async

# Overrides config.yaml — uses 5 seconds
conductor watch-async --poll-interval 5
```

Priority: **CLI flag** > **config.yaml** > **built-in default**

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

### ModelConfig Defaults

All `LLMExecutor` and `HybridExecutor` agents use `ModelConfig` for LLM settings.
Agents override via `get_model_config()`.

| Field | Default | Description |
|-------|---------|-------------|
| `model_id` | `anthropic.claude-sonnet-4-20250514` | Bedrock model ID |
| `region` | `us-east-1` | AWS region |
| `temperature` | `0.2` | Sampling temperature |
| `max_output_tokens` | `64_000` | Max tokens per LLM response |
| `max_tool_iterations` | `50` | Max tool call turns in agent loop |
| `retry_max_attempts` | `5` | Retries on throttling/transient errors |
| `retry_base_delay` | `2.0` | Base delay for exponential backoff (seconds) |
| `preferred_provider` | `None` | Label from pool config for provider preference |
| `history_strategy` | `"truncate"` | Sliding window strategy: `truncate`, `summarize`, `none` |
| `history_trigger_tokens` | `120_000` | Start truncating when history exceeds this |
| `history_keep_tokens` | `60_000` | Keep this many recent tokens after truncation |

### Sliding Window History

Long-running agents accumulate large conversation histories. The provider
manages this automatically based on `ModelConfig` settings:

- **truncate** (default): drops oldest tool-result messages, keeps system prompt
  and recent messages. Fast, no extra LLM call.
- **summarize**: uses a cheap model to summarize dropped messages. *(Future)*
- **none**: no history management. Use for short-lived agents.

Agents tune thresholds via `get_model_config()`:

```python
def get_model_config(self):
    from conductor.providers.base import ModelConfig
    return ModelConfig(
        history_strategy="truncate",
        history_trigger_tokens=100_000,  # tighter for this agent
        history_keep_tokens=40_000,
    )
```

See the [LLM Usage Guide](llm-usage.md) for full details.

### No Provider

If `providers` is not set, `context.llm_provider` is `None`. Agents that need
an LLM must create their own provider internally.

## WatcherConfig Fields

| Field | Default | Description |
|-------|---------|-------------|
| `poll_interval_seconds` | 10 | Seconds between poll cycles |
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
| `worktrees_directory` | `worktrees` | Directory for pod worktrees (relative to project base) |
| `pod_assignment_path` | `output/analysis/workpackages/Pod_Assignment.json` | Path to pod assignment JSON |

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
at startup. The module must expose a `register()` function:

```python
def register(registry: AgentRegistry) -> None:
    # Register agents
    registry.register(MyAgent())

    # Register scope discovery (for per-workpackage/pod phases)
    registry.set_scope_discovery(MyScopeDiscovery())

    # Register custom validators (for project-specific checks)
    registry.register_validator("my_check", my_validator_fn)
```

The registry is the single integration point between conductor and your project.
Through it you provide:

| What | Method | Purpose |
|------|--------|---------|
| Agents | `registry.register(executor)` | Map agent names to executor instances |
| Scope discovery | `registry.set_scope_discovery(impl)` | How to discover workpackages, pods, domains |
| Custom validators | `registry.register_validator(name, fn)` | Project-specific deliverable checks |

If `agents_module` is not set or the module can't be imported, conductor falls
back to built-in generic executors (NoOp, Echo, Shell) and `DefaultScopeDiscovery`
(returns empty lists for all scopes).

## Git Configuration

Git operations are optional. If git is not available or the working directory
is not a git repo, conductor logs a warning and continues without git.

Tags follow the convention: `conductor/{ticket_id}/started`, `conductor/{ticket_id}/completed`, `conductor/{ticket_id}/approved`.

### Pod Worktrees

For pod-scoped phases, conductor creates git worktrees in `worktrees_directory`.
On watcher restart, the `WorktreeManager` automatically restores its state from
existing worktree directories on disk — no manual recovery needed.

The pod setup hook (`post_phase_hook: "setup_and_execute_pods"`) is idempotent:
calling it twice skips pods that already have worktrees.

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

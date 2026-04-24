# Conductor Improvement Proposals

## 1. Multi-Provider Pool with Agent-Level Selection

### Problem

The current architecture passes a single `LLMProvider` (or `None`) to all agents via `context.llm_provider`. When running many agents concurrently or processing large codebases, a single Bedrock region's rate limits become a bottleneck. There's no mechanism for agents to fail over to alternative providers or regions, and no way for different agents to use different models based on task complexity.

### Current State

- `LLMProvider` is an abstract base class in `conductor/providers/base.py`
- `BedrockProvider` is the only implementation, with built-in retry and exponential backoff for single-region throttling
- The `watch` CLI command doesn't create a provider at all — `context.llm_provider` is `None`
- Agents that need an LLM must create their own provider internally (as we did for `ValueStreamAnalystAgent`)
- `LLMExecutor.get_model_config()` allows per-agent model selection, but only within a single provider

### Proposed Design: ProviderPool

A new `ProviderPool` class that implements `LLMProvider` and wraps multiple providers with configurable selection strategies.

```
conductor/providers/
├── base.py          # LLMProvider ABC (existing)
├── bedrock.py       # BedrockProvider (existing)
└── pool.py          # ProviderPool (new)
```

#### Interface

```python
class ProviderPool(LLMProvider):
    """Routes LLM calls across multiple providers based on strategy."""

    def __init__(
        self,
        providers: list[LLMProvider],
        strategy: str = "fallback",
    ):
        self.providers = providers
        self.strategy = strategy
        self._metrics: dict[str, ProviderMetrics] = {}

    def call(self, system_prompt, user_prompt, model_config) -> LLMResponse:
        provider = self._select(model_config)
        try:
            return provider.call(system_prompt, user_prompt, model_config)
        except ThrottlingError:
            return self._failover(system_prompt, user_prompt, model_config, exclude=provider)

    def run_agent_loop(self, ...) -> AgentLoopResponse:
        provider = self._select(model_config)
        # Same failover pattern
```

#### Selection Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `fallback` | Try providers in order; move to next on throttle/failure | Simple HA across regions |
| `round_robin` | Distribute calls evenly across providers | Maximize aggregate throughput |
| `latency` | Track rolling average latency; prefer fastest | Minimize response time |
| `cost` | Route based on model pricing tiers | Budget optimization |
| `agent_preference` | Agent declares preferred provider via `get_model_config()`; pool respects preference but falls back if unavailable | Per-task model selection |

#### Configuration

```yaml
# .conductor/config.yaml
providers:
  pool:
    strategy: "fallback"
    providers:
      - type: "bedrock"
        region: "us-east-1"
        label: "primary"
      - type: "bedrock"
        region: "us-west-2"
        label: "secondary"
      - type: "bedrock"
        region: "eu-west-1"
        label: "eu-fallback"
```

#### Per-Agent Model Override

Agents already override `get_model_config()`. Extend `ModelConfig` with a `preferred_provider` field:

```python
@dataclass
class ModelConfig:
    model_id: str = "anthropic.claude-sonnet-4-20250514"
    region: str = "us-east-1"
    preferred_provider: str | None = None  # label from pool config
    # ... existing fields
```

The pool checks `model_config.preferred_provider` first, falls back to strategy-based selection if the preferred provider is unavailable.

#### Metrics Tracking

Each provider in the pool tracks:
- Call count
- Success/failure rate
- Rolling average latency
- Throttle count
- Last throttle timestamp

This data feeds the selection strategies and can be exposed via the dashboard.

### Implementation Notes

- The pool is transparent to agents — they still see a single `LLMProvider` interface
- The `watch` CLI should create the pool from config and pass it as `llm_provider` to the watcher
- Existing agents that create their own `BedrockProvider` internally would continue to work but wouldn't benefit from the pool
- The pool should log provider selection decisions at DEBUG level for troubleshooting

### Priority

Low — not needed for MVP. The current single-provider approach with Bedrock's built-in retry handles moderate workloads. The pool becomes valuable when running many concurrent agents or processing very large codebases where single-region rate limits are hit.

### Extension: Agent-Driven Provider Selection

Beyond static preferences, agents can actively choose their provider at runtime based on task characteristics, pool metrics, or previous failures.

#### Design

Add a `get_provider()` method to `LLMExecutor` that agents can override:

```python
class LLMExecutor(AgentExecutor):
    def get_provider(
        self, ticket: Ticket, context: ExecutionContext
    ) -> LLMProvider:
        """Select the LLM provider for this execution.

        Default: return context.llm_provider.
        Override to inspect the pool and choose based on runtime conditions.
        """
        return context.llm_provider

    def execute(self, ticket, context):
        provider = self.get_provider(ticket, context)
        # ... use provider for the agent loop
```

The `LLMExecutor.execute()` method calls `get_provider()` instead of reading `context.llm_provider` directly. This gives agents a hook to make runtime decisions.

#### Example: Task-Size-Aware Selection

```python
class ValueStreamAnalystAgent(LLMExecutor):
    def get_provider(self, ticket, context):
        pool = context.llm_provider
        if not isinstance(pool, ProviderPool):
            return pool  # single provider, no choice

        # Large codebases → use high-throughput region
        flows_path = self._resolve_flows_path()
        if flows_path.stat().st_size > 1_000_000:  # >1MB of flows
            provider = pool.get_by_label("high-throughput")
            if provider:
                return provider

        # Default: let the pool's strategy decide
        return pool.select_best()
```

#### Example: Retry-Aware Selection

```python
class ResilientAgent(LLMExecutor):
    def get_provider(self, ticket, context):
        pool = context.llm_provider
        if not isinstance(pool, ProviderPool):
            return pool

        iteration = ticket.metadata.iteration
        if iteration > 1:
            # Previous attempt failed — try a different provider
            return pool.select_least_recently_failed()

        return pool.select_best()
```

#### ProviderPool Query Methods

The pool exposes methods agents can use for selection:

```python
class ProviderPool(LLMProvider):
    def get_by_label(self, label: str) -> LLMProvider | None:
        """Get a specific provider by its config label."""

    def select_best(self) -> LLMProvider:
        """Select based on the pool's configured strategy."""

    def select_least_loaded(self) -> LLMProvider:
        """Select the provider with the lowest recent call count."""

    def select_fastest(self) -> LLMProvider:
        """Select the provider with the lowest rolling average latency."""

    def select_least_recently_failed(self) -> LLMProvider:
        """Select the provider that hasn't failed recently."""

    def get_metrics(self, label: str) -> ProviderMetrics:
        """Get metrics for a specific provider."""

    def list_available(self) -> list[str]:
        """List labels of all healthy providers."""
```

#### Two Levels of Agent Control

| Level | Mechanism | Agent Code | Use Case |
|-------|-----------|------------|----------|
| Static | `get_model_config()` returns `preferred_provider` label | No pool awareness needed | "Always use Sonnet for this agent" |
| Dynamic | `get_provider()` inspects pool metrics and picks | Agent queries pool state | Task-size routing, retry failover, cost optimization |

Static selection is handled by the pool transparently. Dynamic selection requires the agent to be pool-aware but gives full control over provider choice at runtime.

---

## 2. CLI Watch Command Should Create LLM Provider

### Problem

The `conductor watch` command creates the `EventWatcher` without an `llm_provider`. Any agent extending `LLMExecutor` gets `context.llm_provider = None` and must create its own provider. This defeats the purpose of centralized provider configuration.

### Current State

```python
# conductor/cli.py — watch command
watcher = EventWatcher(
    tracker=trk,
    registry=registry,
    git=git,
    config=watcher_config,
    project_config=project_config,
    # llm_provider is NOT passed — defaults to None
)
```

### Proposed Fix

```python
# Create provider from config
from conductor.providers.bedrock import BedrockProvider
provider = BedrockProvider(region=project_config.aws_region)

watcher = EventWatcher(
    tracker=trk,
    registry=registry,
    git=git,
    config=watcher_config,
    project_config=project_config,
    llm_provider=provider,
)
```

When the ProviderPool is implemented, this becomes:
```python
provider = build_provider_from_config(watcher_config)  # returns Pool or single
```

### Priority

Medium — this is a prerequisite for agents to use `LLMExecutor` without creating their own providers. Currently each agent must handle the `None` case.

---

## 3. Dashboard Status Display and HITL Approve Button

See `issues/conductor-dashboard-stale-status.md` for full details.

### Summary

The web dashboard shows tickets as "Ready" when they're actually in `awaiting_review` status. No approve button is rendered. The CLI correctly shows the status and the `conductor ticket approve` command works.

### Priority

High — blocks the HITL workflow through the UI. Operators must use the CLI to approve tickets.

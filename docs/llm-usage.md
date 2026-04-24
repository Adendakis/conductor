# LLM Usage Guide

How Conductor manages LLM agents, what the framework provides automatically,
and what agent authors control.

---

## Framework-Level Features

These apply to every `LLMExecutor`-based agent automatically. No agent code changes needed.

### Read-Once Instruction

Every user prompt sent to the LLM is appended with an instruction telling the model
not to re-read files it has already loaded. This eliminates redundant tool calls and
reduces token usage across all agents.

The instruction is appended in `LLMExecutor.execute()` before the prompt reaches the
provider. Agents do not need to include this in their own prompts.

### Per-Tool-Call Logging

`BedrockProvider.run_agent_loop()` logs detailed information during execution:

- Per-turn summary: turn number, elapsed time, tool call count, token usage
- LLM reasoning text (first 300 chars, `💭` prefix)
- Each tool call with argument summary (`🔧 Tool: name(args)`)
- Tool result preview (name, elapsed, char count)
- Completion summary with cost estimate
- Files written

This is always on. No configuration needed.

### Prompt Pre-Loading

The framework scans the user prompt for file path references and inlines their
content before sending to the LLM. This saves tool call round-trips on predictable,
static file reads.

Two levels:

1. **Automatic** — `LLMExecutor` scans the prompt text for paths that exist relative
   to the working directory and appends their content.
2. **Explicit** — agents override `get_preloaded_files()` to declare additional files:

```python
class MyAgent(LLMExecutor):
    def get_preloaded_files(self, ticket, context) -> list[str]:
        return [
            "templates/schema.json",
            "output/analysis/summary.md",
        ]
```

### Sliding Window History

Long-running agents accumulate large conversation histories from tool calls.
The framework manages this at the provider level to prevent context window overflow.

Configuration via `ModelConfig`:

```python
def get_model_config(self):
    from conductor.providers.base import ModelConfig
    return ModelConfig(
        history_strategy="truncate",       # "truncate", "summarize", or "none"
        history_trigger_tokens=120_000,    # start truncating above this
        history_keep_tokens=60_000,        # keep this many recent tokens
    )
```

**Strategy: truncate** (default) — drops oldest tool-result messages, keeping the
system prompt, user prompt, and most recent messages. Fast and free (no extra LLM call).

**Strategy: summarize** — uses a cheap model to summarize dropped messages into a
single context message. More expensive but preserves information. *(Future)*

**Strategy: none** — no history management. Use for short-lived agents that won't
hit context limits.

### Default Tool Set

Every `LLMExecutor` agent receives these tools by default:

| Tool | Description |
|------|-------------|
| `read_file` | Read a single file with optional line range |
| `read_files` | Batch read multiple files (newline-separated paths, 200KB budget) |
| `write_file` | Write content to a file (sandbox-controlled) |
| `list_files` | List directory contents |
| `search_file` | Regex search within a file |

### Default Model Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model_id` | `anthropic.claude-sonnet-4-20250514` | Bedrock model ID |
| `temperature` | `0.2` | Sampling temperature |
| `max_output_tokens` | `64_000` | Max tokens per LLM response |
| `max_tool_iterations` | `50` | Max tool call turns |
| `history_strategy` | `"truncate"` | History management strategy |
| `history_trigger_tokens` | `120_000` | Token threshold to trigger truncation |
| `history_keep_tokens` | `60_000` | Tokens to keep after truncation |

---

## Agent-Level Responsibilities

These are decisions and overrides that belong in your agent code, not the framework.

### Choose Your Base Class

| Type | When to use |
|------|-------------|
| `LLMExecutor` | Full autonomous agent with tool loop (most agents) |
| `HybridExecutor` | Context assembly → single LLM call → post-processing |
| `ReviewerExecutor` | Evaluate deliverables, return structured verdict |
| `ToolExecutor` | No LLM — run a script or command |

Pick the simplest one that fits. Not every agent needs a tool loop.

### Prompt Design

The framework appends the read-once instruction and inlines pre-loaded files,
but the actual prompt content is entirely the agent author's responsibility:

- `get_system_prompt()` — role definition, constraints, output format
- `get_user_prompt()` — task instructions, context, deliverable expectations

This is where agent quality lives.

### Override `get_tools()` for Extra Tools

The default tool set covers file operations. If your agent needs additional tools
(e.g., shell execution for database queries), opt in:

```python
def get_tools(self):
    tools = super().get_tools()
    tools.append(ExecuteCommandTool())
    return tools
```

The `ExecuteCommandTool` is provided by the framework but not included by default.
It runs sandboxed shell commands with an allowlist and timeout.

### Override `get_sandbox_config()` for Write Permissions

The default sandbox blocks writes to `output/analysis/*` and `input/*`. If your
agent needs to write to blocked paths, configure exceptions:

```python
def get_sandbox_config(self) -> dict:
    return {
        "write_allowed_exceptions": ["output/analysis/value_streams/*"]
    }
```

### Override `get_model_config()` for Agent-Specific Tuning

```python
def get_model_config(self):
    from conductor.providers.base import ModelConfig
    return ModelConfig(
        max_tool_iterations=80,            # complex agent needs more turns
        history_trigger_tokens=100_000,    # tighter window
        temperature=0.0,                   # deterministic output
    )
```

### Override `get_preloaded_files()` for Static Dependencies

If your agent always needs certain files that aren't referenced in the prompt text,
declare them explicitly to avoid unnecessary tool calls:

```python
def get_preloaded_files(self, ticket, context) -> list[str]:
    return [
        "templates/Value_Streams.json",
        "output/analysis/reports/analysis_summary.md",
    ]
```

---

## Summary: Framework vs. Agent

| Framework provides | Agent decides |
|---|---|
| Read-once instruction (auto-appended) | Prompt content (`get_system_prompt`, `get_user_prompt`) |
| Default tools (file ops) | Extra tools (`get_tools()`) |
| Default sandbox rules | Write permission exceptions (`get_sandbox_config()`) |
| Sliding window mechanism | Trigger/keep thresholds (`get_model_config()`) |
| Prompt pre-loading engine | Additional files to pre-load (`get_preloaded_files()`) |
| Default model config (64K tokens, sonnet) | Agent-specific model overrides |
| Per-tool-call logging | — (always on) |

---

## Optional Tools

### ExecuteCommandTool

Sandboxed shell execution for agents that need to query databases or run
analysis commands. Located in `conductor/tools/shell.py`.

```python
from conductor.tools.shell import ExecuteCommandTool

# Default allowlist: sqlite3, grep, wc, find, head, tail, cat, sort,
#                    uniq, awk, sed, cut, diff, ls, tree
# Default timeout: 30 seconds

# Use defaults:
tool = ExecuteCommandTool()

# Or customize:
tool = ExecuteCommandTool(
    allowed_commands=frozenset({"sqlite3", "grep", "python"}),
    timeout=60,
)
```

Add to your agent:

```python
def get_tools(self):
    from conductor.tools.shell import ExecuteCommandTool
    tools = super().get_tools()
    tools.append(ExecuteCommandTool())
    return tools
```

---

## See Also

- [Writing Agents](writing-agents.md) — executor types, registration, override points
- [Configuration](configuration.md) — ModelConfig defaults, provider setup, sliding window config
- [Architecture](architecture.md) — LLM agent loop flow diagram

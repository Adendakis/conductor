# Conductor Agent Improvements

Findings from migrating the pydantic-ai orchestrator (`OLD_PYACM/pydantic-acm/`) to Conductor agents. These are framework-level improvements that would benefit all agents, not just the ACM migration pipeline.

Reference implementation: `OLD_PYACM/pydantic-acm/orchestrator/agents/providers.py` (PydanticAIProvider, AgentRunner) and `OLD_PYACM/pydantic-acm/orchestrator/core/orchestrator.py` (_resolve_prompt, _invoke_specialist).

---

## 1. Read-Once Instruction (Auto-Append to All Prompts)

### Problem

LLM agents frequently re-read files they've already loaded in previous tool calls. Each redundant read wastes a full round-trip (LLM request → tool execution → LLM response) and inflates token usage since the file content appears multiple times in the conversation history.

### What the Old System Does

`_resolve_prompt()` in `orchestrator/core/orchestrator.py` appends this to every prompt:

```
============================================================
CRITICAL FILE READING RULE
Read each file ONCE only. Do NOT re-read files you have already read.
The file content is in your conversation history from the previous read.
Reference it from memory. Re-reading the same file wastes time and budget.
After reading all needed files, proceed directly to writing your output.
============================================================
```

### Proposed Fix

Add this to `LLMExecutor.execute()` in `conductor/executor/llm_executor.py`, appended to the user prompt before calling `provider.run_agent_loop()`. No agent code changes needed — all agents get it automatically.

```python
def execute(self, ticket, context):
    user_prompt = self.get_user_prompt(ticket, context)
    user_prompt += _READ_ONCE_INSTRUCTION  # constant string
    ...
```

### Priority: High — minimal effort, immediate impact on all agents.

---

## 2. Per-Tool-Call Logging in the Agent Loop

### Problem

`BedrockProvider.run_agent_loop()` is silent during execution. When an agent runs for 5-10 minutes, the operator sees nothing in the log until it completes or fails. There's no visibility into which tools are being called, how long each call takes, or what the LLM is reasoning about.

### What the Old System Does

The pydantic-ai provider logs:
- Each tool call name and duration
- LLM reasoning text between tool calls (first 500 chars)
- Token usage per request
- Total cost at completion

```
🤖 PydanticAI invoking: value_stream_analyst (role=specialist, model=..., prompt=12,345 chars, tools=6)
💭 [value_stream_analyst] I'll start by reading the Business_Flows.json to understand...
✅ value_stream_analyst completed: input=45,000 output=8,200 total=53,200 (12 requests, $0.2340)
```

### Proposed Fix

Add logging inside the `for iteration in range(max_iterations)` loop in `BedrockProvider.run_agent_loop()`:

```python
# After each tool call:
logger.info(f"  🔧 Tool: {tool_name}({', '.join(f'{k}={v!r:.50}' for k,v in arguments.items())})")

# After each LLM response with text:
for block in content_blocks:
    if "text" in block:
        text = block["text"][:200]
        logger.info(f"  💭 {text}")

# At completion:
logger.info(f"  ✅ Agent loop complete: {tool_calls_made} tool calls, {iteration+1} turns")
```

### Priority: High — essential for debugging and operator confidence.

---

## 3. Conversation History Management (Sliding Window)

### Problem

Long-running agents accumulate large conversation histories from tool calls. Each tool result (especially file reads) adds thousands of tokens. Eventually the conversation exceeds the model's context window, causing failures or degraded output quality.

### What the Old System Does

Uses `pydantic-ai-summarization` (a pydantic-ai extension) to implement a sliding window:

```python
from pydantic_ai_summarization import create_sliding_window_processor
sliding_window = create_sliding_window_processor(
    trigger=('tokens', 80_000),
    keep=('tokens', 40_000),
)
```

When the conversation exceeds 80K tokens, it summarizes older messages and keeps the most recent 40K tokens.

### Proposed Fix — Provider-Level Implementation

This belongs in the provider, not in `LLMExecutor`, because different providers have different context limits and different ways to count tokens.

**Option A: Simple truncation (no LLM call)**

In `BedrockProvider.run_agent_loop()`, before each LLM call, estimate the conversation size. If it exceeds a threshold, drop the oldest tool-result messages (keeping the system prompt, user prompt, and most recent N messages):

```python
# Before each converse() call:
if self._estimate_tokens(messages) > 120_000:
    messages = self._truncate_history(messages, keep_recent=60_000)
```

This is fast and free (no extra LLM call) but loses context from early tool calls.

**Option B: Summarization (extra LLM call)**

Use a cheap/fast model to summarize the dropped messages into a single "context so far" message. More expensive but preserves information:

```python
if self._estimate_tokens(messages) > 120_000:
    old_messages, recent_messages = self._split_history(messages, keep_recent=60_000)
    summary = self._summarize(old_messages)  # cheap model call
    messages = [summary_message] + recent_messages
```

**Option C: Provider-configurable strategy**

Add a `history_strategy` field to `ModelConfig`:

```python
@dataclass
class ModelConfig:
    history_strategy: str = "truncate"  # "truncate", "summarize", "none"
    history_trigger_tokens: int = 120_000
    history_keep_tokens: int = 60_000
```

Each provider implements the strategy in its own `run_agent_loop()`. This keeps the interface clean and allows different providers to handle it differently (e.g., a provider with 200K context might use a higher trigger).

### Priority: Medium — critical for large codebases, not needed for small projects.

---

## 4. Batch File Read Tool (`read_files`)

### Problem

Agents frequently need to read multiple files. With only `read_file`, each file requires a separate tool call round-trip. For an agent that needs to read 5 files, that's 5 LLM requests just for reading.

### What the Old System Does

Has a `read_files` tool that accepts newline-separated paths and returns all file contents in a single response:

```python
class ReadFilesTool(AgentTool):
    """Batch read multiple files in a single tool call."""
    # Accepts: paths (newline-separated list)
    # Returns: concatenated file contents with headers
    # Budget: 200KB total across all files
```

### Proposed Fix

Add `ReadFilesTool` to `conductor/tools/file_ops.py` (it already exists in the old system — port it). Add it to the default tool set in `LLMExecutor.get_tools()`.

### Priority: Medium — reduces tool call count by 3-5x for multi-file reads.

---

## 5. Prompt Pre-Loading (Referenced File Inlining)

### Problem

Prompt files often reference other files (templates, specs, examples). The LLM reads the prompt, sees the references, and makes tool calls to read each one. This wastes turns on predictable, static content.

### What the Old System Does

`_resolve_prompt()` scans the prompt text for `./prompts/.../*.md` patterns and inlines their content directly:

```python
pattern = re.compile(r'\./prompts/[^\s\)\"\']+\.md')
referenced_paths = pattern.findall(prompt_text)
for ref_path in referenced_paths:
    content = full_path.read_text()
    prompt_text += f"\n\n## [PRE-LOADED] {ref_path}\n{content}"
```

### Proposed Fix

Two levels:

**Level 1 (Conductor framework)**: Add a `_resolve_prompt_references()` method to `LLMExecutor` that scans the user prompt for file path patterns and inlines them. Called automatically in `execute()` before passing to the agent loop.

```python
# In LLMExecutor.execute():
user_prompt = self.get_user_prompt(ticket, context)
user_prompt = self._resolve_prompt_references(user_prompt, context.working_directory)
```

**Level 2 (Agent override)**: Agents can override `get_preloaded_files()` to declare additional files to inline:

```python
class MyAgent(LLMExecutor):
    def get_preloaded_files(self, ticket, context) -> list[str]:
        return [
            "templates/Value_Streams.json",
            "output/analysis/reports/analysis_summary.md",
        ]
```

The base class inlines these into the prompt automatically.

### Priority: Medium — saves 2-5 tool calls per agent, significant for agents that reference many files.

---

## 6. Default Output Token Limit

### Problem

`ModelConfig.max_output_tokens` defaults to 16,000. Many agents need to produce large outputs (JSON files, detailed reports). The old system uses 64,000.

### Proposed Fix

Change the default in `conductor/providers/base.py`:

```python
@dataclass
class ModelConfig:
    max_output_tokens: int = 64_000  # was 16_000
```

### Priority: Low — easy change, agents can already override via `get_model_config()`.

---

## 7. Custom Sandbox Per Agent

### Problem

The `ToolSandbox` in `conductor/tools/base.py` has hardcoded blocked/allowed patterns. Different agents need different write permissions (e.g., value stream agent writes to `output/analysis/value_streams/` which is blocked by the default `output/analysis/*` pattern). Currently agents must subclass the write tool to change the sandbox.

### Proposed Fix

Make the sandbox configurable via `LLMExecutor`:

```python
class LLMExecutor(AgentExecutor):
    def get_sandbox_config(self) -> dict:
        """Override to customize file access rules."""
        return {}  # uses defaults

    def get_tools(self):
        sandbox_config = self.get_sandbox_config()
        return build_tools_with_sandbox(sandbox_config)
```

Or simpler: add `write_allowed_patterns` to `ModelConfig` or a new `AgentConfig` dataclass that the tool reads at execution time.

### Priority: Medium — currently requires boilerplate subclassing per agent.

---

## 8. Execute Shell Tool

### Problem

Some agents need to query `analysis.db` (SQLite) for additional data during their analysis. The old system provides an `execute_shell` tool for this. Conductor's default tool set doesn't include it.

### What the Old System Does

```python
class ExecuteShellTool:
    """Execute a shell command and return stdout/stderr."""
    # Sandboxed: only allows specific commands (sqlite3, grep, wc, etc.)
    # Timeout: 30 seconds
    # Working directory: project root
```

### Proposed Fix

Add an `ExecuteCommandTool` to `conductor/tools/` with:
- Allowlist of safe commands (sqlite3, grep, wc, find, head, tail)
- Timeout (30s default)
- Working directory from context
- Not included in default tool set — agents opt in via `get_tools()`

### Priority: Low — only needed by agents that query databases or run analysis commands.

---

## Summary: Implementation Order

| # | Improvement | Effort | Impact | Priority |
|---|---|---|---|---|
| 1 | Read-once instruction | 5 min | All agents | High |
| 2 | Per-tool-call logging | 30 min | All agents | High |
| 6 | Default output token limit | 1 min | All agents | High |
| 4 | Batch read_files tool | 1 hour | All agents | Medium |
| 5 | Prompt pre-loading | 2 hours | All agents | Medium |
| 7 | Custom sandbox per agent | 1 hour | LLM agents | Medium |
| 3 | Sliding window history | 4 hours | Long-running agents | Medium |
| 8 | Execute shell tool | 2 hours | DB-querying agents | Low |

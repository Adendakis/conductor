# Writing Agents

Agents are Python classes that execute work for a ticket. They live in your
project's `agents/` directory and are registered with conductor at startup.

## Registration

```python
# agents/__init__.py
from conductor.executor.registry import AgentRegistry
from .my_agent import MyAgent

def register(registry: AgentRegistry):
    registry.register(MyAgent())
```

The `agent_name` property must match the `agent` field in `pipeline.yaml`.

## Project Layout

Each agent lives in its own directory with its prompts:

```
agents/
├── analyzer/
│   ├── __init__.py          ← exports AnalyzerAgent
│   ├── agent.py             ← the executor class
│   └── prompts/
│       └── analyze.md       ← prompt template for this agent
├── planner/
│   ├── __init__.py
│   ├── agent.py
│   └── prompts/
│       └── plan.md
└── __init__.py              ← register() imports from each subpackage
```

This makes each agent self-contained — code + prompts in one directory.
You can copy an agent directory to another project and it works.

The `prompt` field in `pipeline.yaml` points to the agent's prompt file:

```yaml
steps:
  - id: "analyze"
    agent: "my_analyzer"
    prompt: "agents/analyzer/prompts/analyze.md"
```

## Executor Types

### ToolExecutor — Run a Script

For agents that run a subprocess (no LLM involved).

```python
from conductor.executor.tool_executor import ToolExecutor

class MyAnalyzer(ToolExecutor):
    @property
    def agent_name(self) -> str:
        return "my_analyzer"

    def build_command(self, ticket, context):
        return "python scripts/analyze.py --output output/report.md", \
               str(context.working_directory)
```

### HybridExecutor — Context Assembly + LLM

For agents that gather context deterministically, then make a single LLM call.
This is the recommended pattern for most agents — cheaper and more predictable
than the autonomous pattern.

```python
from conductor.executor.hybrid_executor import HybridExecutor
from conductor.context.prompt_context import PromptContext

class MySpecialist(HybridExecutor):
    @property
    def agent_name(self) -> str:
        return "my_specialist"

    def assemble_context(self, ticket, context):
        # Read inputs, build prompt — no LLM here
        system = "You are a business analyst."
        user = f"Analyze {ticket.metadata.workpackage}..."
        return PromptContext(system_prompt=system, user_prompt=user)

    def post_process(self, llm_output, ticket, context):
        # Transform LLM output into deliverable files
        return {ticket.metadata.deliverable_paths[0]: llm_output}
```

### LLMExecutor — Autonomous Agent with Tools

For agents that need to read/write files in a loop (multi-turn LLM interaction).

```python
from conductor.executor.llm_executor import LLMExecutor

class MyCodeGen(LLMExecutor):
    @property
    def agent_name(self) -> str:
        return "code_generator"

    def get_system_prompt(self, ticket, context):
        return "You are a code generation agent. Write Java code."

    def get_user_prompt(self, ticket, context):
        return f"Generate code for {ticket.metadata.workpackage}..."
```

The LLM gets file read/write/search tools automatically. The framework also:

- Appends a **read-once instruction** to every prompt (prevents redundant file reads)
- **Pre-loads referenced files** found in the prompt text (saves tool call round-trips)
- Manages **sliding window history** to prevent context overflow on long runs
- Provides a **configurable sandbox** for file access control

See the [LLM Usage Guide](llm-usage.md) for full details on these features.

### ReviewerExecutor — Quality Review

For agents that evaluate deliverables and return APPROVED/REJECTED.

```python
from conductor.executor.reviewer_executor import ReviewerExecutor

reviewer = ReviewerExecutor(
    name="my_reviewer",
    reviewer_for="step_3_1",    # step ID this reviewer evaluates
    max_iterations=3,
)
```

The reviewer parses the LLM output for `## Verdict: APPROVED` or `REJECTED`.

## Direct Executor (Simplest)

If none of the base classes fit, subclass `AgentExecutor` directly:

```python
from conductor.executor.base import AgentExecutor, ExecutionContext, ExecutionResult

class MyAgent(AgentExecutor):
    @property
    def agent_name(self) -> str:
        return "my_agent"

    def execute(self, ticket, context):
        # Do anything — call an API, run a tool, write files
        Path(context.working_directory / "output/result.md").write_text("# Done")
        return ExecutionResult(
            success=True,
            summary="Task completed",
            deliverables_produced=["output/result.md"],
        )
```

## CAO Integration

To delegate work to a [CAO](https://github.com/awslabs/cli-agent-orchestrator/)
tmux session (Kiro, Claude Code, Q CLI, etc.):

```python
from agents.example_cao_using_agent import CaoUsingAgent

registry.register(CaoUsingAgent(
    name="code_generator",
    provider="kiro_cli",
    agent_profile="developer",
    timeout=900,
))
```

See `examples/demo-project/agents/example_cao_using_agent.py` for the full boilerplate.

## LLMExecutor Override Points

`LLMExecutor` agents have several optional overrides beyond the required
`get_system_prompt()` and `get_user_prompt()`. All have sensible defaults —
override only what you need.

### Custom Tools

The default tool set includes `read_file`, `read_files` (batch), `write_file`,
`list_files`, and `search_file`. To add extra tools:

```python
def get_tools(self):
    from conductor.tools.shell import ExecuteCommandTool
    tools = super().get_tools()
    tools.append(ExecuteCommandTool())
    return tools
```

The `ExecuteCommandTool` runs sandboxed shell commands (sqlite3, grep, etc.)
with an allowlist and timeout. It is not included by default — agents opt in.

### Custom Sandbox

The default sandbox blocks writes to `output/analysis/*` and `input/*`.
Override `get_sandbox_config()` to adjust:

```python
def get_sandbox_config(self) -> dict:
    return {
        "write_allowed_exceptions": ["output/analysis/value_streams/*"],
        "read_blocked_patterns": ["*.dat", "*.ps"],
    }
```

Available keys: `read_blocked_patterns`, `write_blocked_patterns`,
`write_allowed_exceptions`. Omitted keys use defaults.

### Model Configuration

Override `get_model_config()` for agent-specific tuning:

```python
def get_model_config(self):
    from conductor.providers.base import ModelConfig
    return ModelConfig(
        model_id="anthropic.claude-haiku-3-20250310",  # cheaper model
        max_tool_iterations=80,                         # more turns
        temperature=0.0,                                # deterministic
        history_trigger_tokens=100_000,                 # tighter window
        history_keep_tokens=40_000,
    )
```

See [LLM Usage Guide — Default Model Configuration](llm-usage.md#default-model-configuration)
for all available fields.

### Pre-Loaded Files

If your agent always needs certain files that aren't referenced in the prompt
text, declare them to avoid unnecessary tool calls:

```python
def get_preloaded_files(self, ticket, context) -> list[str]:
    return [
        "templates/Value_Streams.json",
        "output/analysis/reports/analysis_summary.md",
    ]
```

Files referenced in the prompt text are inlined automatically — this override
is for additional files the framework can't detect from the prompt.

## Execution Context

Every agent receives an `ExecutionContext` with:

| Field | Type | Description |
|-------|------|-------------|
| `project_config` | ProjectConfig | Project settings |
| `working_directory` | Path | Where to read/write files |
| `llm_provider` | LLMProvider or None | Configured from `.conductor/config.yaml` providers section |
| `tracker` | TrackerBackend | Access to the ticket tracker |
| `git` | GitManager | Git operations |
| `workpackage_id` | str | Current workpackage (for per-WP steps) |
| `pod_id` | str | Current pod (for per-pod steps) |

The `llm_provider` is created by the CLI from the `providers` config. If a
provider pool is configured, agents get the pool (which implements `LLMProvider`).
If no provider is configured, `llm_provider` is `None`.

## Fallback Behavior

If no agent is registered for a ticket's `agent_name`, conductor uses the
`NoOpExecutor` fallback — it creates placeholder deliverables and succeeds.
This lets you test pipeline structure before writing real agents.

## Prompt File

The `prompt` field in `pipeline.yaml` points to a markdown file with template
variables (`{workpackage_id}`, `{phase}`, `{step}`, etc.). The `ContextAssembler`
reads and renders it. If omitted, the agent receives only the ticket description.

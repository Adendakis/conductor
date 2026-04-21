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

The LLM gets file read/write/search tools automatically.

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

## Execution Context

Every agent receives an `ExecutionContext` with:

| Field | Type | Description |
|-------|------|-------------|
| `project_config` | ProjectConfig | Project settings |
| `working_directory` | Path | Where to read/write files |
| `llm_provider` | LLMProvider | For HybridExecutor/LLMExecutor |
| `tracker` | TrackerBackend | Access to the ticket tracker |
| `git` | GitManager | Git operations |
| `workpackage_id` | str | Current workpackage (for per-WP steps) |
| `pod_id` | str | Current pod (for per-pod steps) |

## Fallback Behavior

If no agent is registered for a ticket's `agent_name`, conductor uses the
`NoOpExecutor` fallback — it creates placeholder deliverables and succeeds.
This lets you test pipeline structure before writing real agents.

## Prompt File

The `prompt` field in `pipeline.yaml` points to a markdown file with template
variables (`{workpackage_id}`, `{phase}`, `{step}`, etc.). The `ContextAssembler`
reads and renders it. If omitted, the agent receives only the ticket description.

# Conductor Generic Refactor Plan

## Goal

Remove all ACM-specific knowledge from conductor's core. Conductor should be a
generic pipeline orchestration framework that knows about tickets, phases, steps,
scopes, and execution — but nothing about COBOL, workpackage planning formats,
pod assignment formats, or migration-specific file structures.

Projects (like ACM) provide their own discovery logic, data formats, validators,
and pipeline definitions. Conductor provides the interfaces and default behaviors.

---

## Current ACM Leakage (what needs to move out)

### 1. Config Models (`conductor/models/config.py`)

**Problem**: `LegacySystem` and `TargetSystem` are ACM concepts (COBOL→Java).
`ProjectConfig` has ACM-specific properties (`source_code_analysis_output`,
`database_analysis_output`, `workpackage_base_path`).

**Fix**: Remove `LegacySystem`, `TargetSystem`, and the ACM-specific path
properties from `ProjectConfig`. Replace with a generic `project_metadata: dict`
field that projects can populate with whatever they need. The ACM project puts
its legacy/target system info there.

```python
class ProjectConfig(BaseModel):
    project_name: str = "project"
    project_base_path: Path = Path(".")
    output_base_path: Optional[Path] = None
    default_model_id: str = "anthropic.claude-sonnet-4-20250514"
    aws_region: str = "us-east-1"
    max_pod_concurrency: int = 1
    project_metadata: dict = Field(default_factory=dict)  # project-specific data
```

**Move to**: `examples/code-migration/` — ACM project can define its own config
model that extends or wraps `ProjectConfig`.

### 2. Scope Discovery (`conductor/watcher/ticket_creator.py`)

**Problem**: `_discover_workpackages()`, `_discover_domains()`, `_discover_pods()`,
and `_get_workpackage_type()` all read ACM-specific file formats and paths.

**Fix**: Define a `ScopeDiscovery` interface that projects implement. Conductor
calls it; the project provides the logic.

```python
class ScopeDiscovery(ABC):
    """Interface for discovering scope units (workpackages, domains, pods).
    
    Projects implement this to read their own data formats.
    Conductor calls these methods during ticket creation.
    """

    @abstractmethod
    def discover_workpackages(self, working_directory: Path) -> list[str]:
        """Return ordered list of workpackage IDs."""
        ...

    @abstractmethod
    def discover_pods(self, working_directory: Path) -> list[str]:
        """Return ordered list of pod IDs."""
        ...

    def discover_domains(self, working_directory: Path) -> list[str]:
        """Return ordered list of domain names. Default: empty."""
        return []

    def get_workpackage_type(self, wp_id: str, working_directory: Path) -> Optional[str]:
        """Return the type of a workpackage for conditional step filtering.
        Default: None (no type filtering)."""
        return None

    def get_pod_assignment_path(self, working_directory: Path) -> Optional[Path]:
        """Return path to pod assignment JSON (generic format).
        Default: uses WatcherConfig.pod_assignment_path."""
        return None
```

**Default implementation**: `DefaultScopeDiscovery` — reads the generic pod
assignment format (`{"pods": {...}}`), returns empty lists for workpackages
and domains (projects must override).

**ACM implementation**: `AcmScopeDiscovery` — reads `Workpackage_Planning.json`
with `migrationSequence`, reads `bre_v2_output` for domains, reads pod assignment
with the ACM-specific format and maps it to the generic format.

**Registration**: The `ScopeDiscovery` instance is passed to `DynamicTicketCreator`
and `AsyncEventWatcher` at startup, loaded from the project's agents module or
config.

### 3. Pipeline Builder (`conductor/pipeline/builder.py`)

**Problem**: `build_pipeline("full")` is a hardcoded ACM migration pipeline with
ACM-specific phase IDs, agent names, prompt paths, and deliverable paths.

**Fix**: Remove the `"full"` pipeline entirely from conductor core. Keep only
`"minimal"` as a demo/test pipeline. The ACM pipeline lives in
`examples/code-migration/pipeline.yaml` (which already exists and works).

The `build_pipeline()` function becomes:

```python
def build_pipeline(mode: str = "minimal") -> list[PhaseDefinition]:
    if mode == "minimal":
        return _build_minimal()
    if mode.startswith("yaml:"):
        from conductor.pipeline.loader import load_pipeline_yaml
        return load_pipeline_yaml(Path(mode[5:]))
    raise ValueError(f"Unknown pipeline mode: {mode}. Use 'minimal' or 'yaml:/path'")
```

**Move to**: The full ACM pipeline definition stays in
`examples/code-migration/pipeline.yaml` where it already is.

### 4. Custom Validators (`conductor/validation/custom_validators.py`)

**Problem**: `validate_dag_no_cycles()` reads `Workpackage_Planning.json` with
ACM format. `validate_pod_assignment_completeness()` reads both ACM-format files.

**Fix**: Make custom validators pluggable. Conductor provides the validator
registry; projects register their own validators.

```python
# In conductor core:
CUSTOM_VALIDATOR_REGISTRY: dict[str, Callable] = {}

def register_validator(name: str, fn: Callable) -> None:
    CUSTOM_VALIDATOR_REGISTRY[name] = fn

# In ACM project's agents/__init__.py:
from conductor.validation.custom_validators import register_validator
from .validators import validate_dag_no_cycles, validate_pod_assignment

def register(registry):
    registry.register(...)
    register_validator("validate_dag_no_cycles", validate_dag_no_cycles)
    register_validator("validate_pod_assignment_completeness", validate_pod_assignment)
```

**Move to**: `examples/code-migration/agents/validators.py` — the ACM-specific
validator implementations.

### 5. Tool Sandbox Defaults (`conductor/tools/base.py`)

**Problem**: `ToolSandbox.write_allowed_exceptions` includes `*/Pod_Assignment.json`
which is ACM-specific.

**Fix**: Remove the ACM-specific exception. The default sandbox should have empty
`write_allowed_exceptions`. Projects configure exceptions via
`LLMExecutor.get_sandbox_config()` on their agents.

```python
write_allowed_exceptions: list[str] = field(default_factory=list)
```

### 6. Default Paths in WatcherConfig

**Problem**: `pod_assignment_path` defaults to
`output/analysis/workpackages/Pod_Assignment.json` — an ACM path convention.

**Fix**: Default to empty string. If not set, the post-phase hook looks for the
file in the phase's deliverable paths (the agent that produced it declared where
it wrote it).

Alternatively, keep a sensible generic default like `Pod_Assignment.json` (just
the filename, resolved relative to working directory) and let projects override.

---

## Implementation Order

| # | Change | Effort | Risk | Impact |
|---|--------|--------|------|--------|
| 1 | Remove `"full"` pipeline from builder, keep `"minimal"` only | 15 min | Low | Removes biggest chunk of ACM code |
| 2 | Extract `ScopeDiscovery` interface, create `DefaultScopeDiscovery` | 1 hour | Medium | Core architectural change |
| 3 | Move ACM discovery logic to `examples/code-migration/` | 30 min | Low | Depends on #2 |
| 4 | Clean up `ProjectConfig` — remove `LegacySystem`/`TargetSystem`, ACM properties | 30 min | Low | Config model cleanup |
| 5 | Make custom validators pluggable, move ACM validators to example | 30 min | Low | Validation cleanup |
| 6 | Clean up `ToolSandbox` defaults | 5 min | Low | Remove ACM exception |
| 7 | Update `WatcherConfig` defaults | 5 min | Low | Generic defaults |
| 8 | Update documentation | 30 min | Low | Reflect new architecture |
| 9 | Update `examples/code-migration/` to use the new interfaces | 1 hour | Medium | Prove it works |

**Total estimate**: ~4-5 hours

---

## Registration Flow (after refactor)

```python
# examples/code-migration/agents/__init__.py

from conductor.executor.registry import AgentRegistry
from conductor.validation.custom_validators import register_validator
from .scope_discovery import AcmScopeDiscovery
from .validators import validate_dag_no_cycles, validate_pod_assignment

def register(registry: AgentRegistry):
    # Register agents
    registry.register(AnalyzerAgent())
    registry.register(PlannerAgent())
    ...

    # Register ACM-specific scope discovery
    registry.set_scope_discovery(AcmScopeDiscovery())

    # Register ACM-specific validators
    register_validator("validate_dag_no_cycles", validate_dag_no_cycles)
    register_validator("validate_pod_assignment_completeness", validate_pod_assignment)
```

The `set_scope_discovery()` method on the registry (or a separate config object)
tells conductor how to discover scopes for this project. If not set, conductor
uses `DefaultScopeDiscovery` which reads the generic pod format and returns empty
lists for workpackages/domains.

---

## What Stays in Conductor Core

- Pipeline YAML loader (generic)
- `"minimal"` demo pipeline
- Ticket lifecycle (statuses, transitions, dependency resolution)
- Executor base classes (Tool, Hybrid, LLM, Reviewer)
- Agent registry with pluggable scope discovery
- WorktreeManager with generic pod assignment format
- Tool system with configurable sandbox
- Validator framework with pluggable custom validators
- Git manager, dashboard, CLI
- `ScopeDiscovery` interface + `DefaultScopeDiscovery`
- `ProjectConfig` with generic `project_metadata` dict

## What Moves to Project Level

- ACM pipeline definition (`pipeline.yaml`)
- ACM scope discovery (`AcmScopeDiscovery` — reads `Workpackage_Planning.json`, `bre_v2_output`)
- ACM custom validators
- ACM config extensions (`LegacySystem`, `TargetSystem`)
- ACM-specific sandbox exceptions
- ACM-specific path conventions

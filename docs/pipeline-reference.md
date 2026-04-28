# Pipeline Reference

Pipelines are defined in `pipeline.yaml`. A pipeline is a list of phases,
each containing steps that map to agent executions.

## Static Definition vs. Dynamic Execution

A conductor pipeline has two parts:

**Static (pipeline.yaml)** — defines the *structure*: what phases exist, what
steps each phase contains, which agents run, what deliverables are expected,
and how phases chain together via `creates_next_phases`. This is fixed at
design time and doesn't change during execution.

**Dynamic (runtime)** — determines *how many tickets* get created and *what
they're scoped to*. When a phase with `scope: per_workpackage` needs tickets,
conductor calls your project's `ScopeDiscovery` implementation to discover
the actual workpackage IDs. The number and identity of scope units comes from
agent deliverables produced earlier in the pipeline — not from the YAML.

Example flow:

```
pipeline.yaml (static)              Runtime (dynamic)
─────────────────────               ─────────────────
Phase 1: Analysis                   → 2 tickets (global scope)
  scope: global
  creates_next_phases: [phase_2]

Phase 2: Planning                   → 1 ticket (global scope)
  scope: global                       Agent produces Workpackage_Planning.json
  creates_next_phases: [phase_3]      with 5 workpackages

Phase 3: Specification              → 5 × 3 = 15 tickets
  scope: per_workpackage              ScopeDiscovery reads the planning JSON
  steps: [spec, review, finalize]     and returns ["WP-001", ..., "WP-005"]
```

The YAML defines that phase 3 has 3 steps per workpackage. The runtime
discovers there are 5 workpackages. Together they produce 15 tickets.

This separation keeps the pipeline definition clean and reusable — the same
`pipeline.yaml` works whether your project has 3 workpackages or 300.

See [Writing Agents — Scope Discovery](writing-agents.md#scope-discovery) for
how to implement the dynamic part.

## Minimal Example

```yaml
pipeline:
  name: "My Workflow"
  phases:
    - id: "phase_1"
      name: "Analysis"
      scope: "global"
      steps:
        - id: "analyze"
          name: "Run Analysis"
          agent: "my_analyzer"
          deliverables:
            - path: "output/report.md"
              type: "markdown"
          hitl_after: true
```

## Phase Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique phase identifier |
| `name` | string | Yes | Display name |
| `scope` | string | No | `global` (default), `per_workpackage`, `per_domain`, `per_pod` |
| `depends_on` | list | No | Phase IDs that must complete before this phase starts |
| `creates_next_phases` | list | No | Phase IDs to create when this phase completes |
| `post_phase_hook` | string | No | Hook to run when phase completes (e.g., `setup_and_execute_pods`) |
| `steps` | list | Yes | Steps within this phase |

## Step Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique step identifier |
| `name` | string | Yes | Display name |
| `agent` | string | Yes | Agent name (must be registered in the agent registry) |
| `prompt` | string | No | Path to prompt template file (optional) |
| `depends_on` | list | No | Step IDs this step depends on (within or across phases) |
| `hitl_after` | bool | No | Require human approval after completion (default: true) |
| `type` | string | No | `task` (default) or `reviewer_step` |
| `deliverables` | list | No | Expected output files |
| `input_dependencies` | list | No | Files from previous phases needed as input |

## Deliverable Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Output file path (supports `{wp_id}`, `{pod_id}` templates) |
| `type` | string | No | `markdown`, `json`, `sql`, `binary`, `directory` |
| `required` | bool | No | Whether the file must exist (default: true) |
| `min_size` | int | No | Minimum file size in bytes (default: 100) |

## Scope Types

| Scope | Tickets Created | How scope units are discovered |
|-------|----------------|-------------------------------|
| `global` | One set for the whole project | No discovery needed — always one scope |
| `per_workpackage` | One set per workpackage | `ScopeDiscovery.discover_workpackages()` |
| `per_domain` | One set per domain | `ScopeDiscovery.discover_domains()` |
| `per_pod` | One set per pod | `ScopeDiscovery.discover_pods()` |

Conductor doesn't know what your workpackage IDs look like or where they're
stored. Your project's `ScopeDiscovery` implementation reads your data format
and returns the list of IDs. See [Writing Agents — Scope Discovery](writing-agents.md#scope-discovery).

## Progressive Creation

Phases are created progressively. At `conductor init`, only root phases (those
not referenced by any `creates_next_phases`) get tickets. Later phases appear
when their parent phase completes and the watcher creates the next batch.

For scoped phases, the watcher calls `ScopeDiscovery` to determine how many
tickets to create:

```yaml
phases:
  - id: "analysis"
    scope: "global"
    creates_next_phases: ["planning"]    # planning created after analysis completes
    steps: [...]

  - id: "planning"
    scope: "global"
    creates_next_phases: ["specification"]
    steps: [...]
    # Agent in this phase produces data that ScopeDiscovery reads later

  - id: "specification"
    scope: "per_workpackage"             # ScopeDiscovery.discover_workpackages() called
    steps: [...]                         # tickets created for each discovered WP
```

## Deliverable Path Templates

For `per_workpackage` scopes, paths support `{wp_id}` placeholders:

```yaml
deliverables:
  - path: "output/specs/{wp_id}/report.md"
```

Resolved to: `output/specs/WP-001/report.md`, `output/specs/WP-002/report.md`, etc.

## Step Patterns

### Pattern 1: Agent → Human Review

```yaml
- id: "analyze"
  agent: "analyzer"
  hitl_after: true        # human approves before next step
```

### Pattern 2: Specialist → Reviewer → Human

```yaml
- id: "extract"
  agent: "specialist"
  hitl_after: false        # auto-approve, goes to reviewer

- id: "review"
  type: "reviewer_step"
  agent: "reviewer"
  depends_on: ["extract"]
  hitl_after: true         # human approves after reviewer
```

### Pattern 3: Fully Automated (No Human)

```yaml
- id: "validate"
  agent: "validator"
  hitl_after: false        # auto-approve, unblock next step
```

### Pattern 4: Pod-Scoped Parallel Execution

```yaml
- id: "phase_2_5"
  name: "Pod Assignment"
  scope: "global"
  post_phase_hook: "setup_and_execute_pods"  # triggers worktree creation
  creates_next_phases: ["phase_3"]
  steps:
    - id: "assign_pods"
      agent: "pod_assigner"
      deliverables:
        - path: "output/analysis/workpackages/Pod_Assignment.json"
          type: "json"

- id: "phase_3"
  name: "Business Specification"
  scope: "per_workpackage"
  steps:
    - id: "spec"
      agent: "business_spec"
      deliverables:
        - path: "output/specs/{wp_id}/Business_Spec.md"
```

When phase_2_5 completes, the watcher creates pod worktrees and creates
per-WP tickets with sequential ordering within each pod.

## Prompt Field

The `prompt` field is optional. Agents that don't need a prompt file (e.g.,
ToolExecutors running a subprocess) can omit it. The agent receives the ticket
description and metadata regardless.

## Validation

```bash
conductor pipeline validate
```

Checks for: duplicate step IDs, invalid dependency references, cycles within phases.

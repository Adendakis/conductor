# Pipeline Reference

Pipelines are defined in `pipeline.yaml`. A pipeline is a list of phases,
each containing steps that map to agent executions.

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

| Scope | Tickets Created | When |
|-------|----------------|------|
| `global` | One set for the whole project | At init or when parent phase completes |
| `per_workpackage` | One set per workpackage | When `Workpackage_Planning.json` is produced |
| `per_domain` | One set per domain | When domain discovery completes |
| `per_pod` | One set per pod | When `Pod_Assignment.json` is produced |

## Progressive Creation

Phases are created progressively. Only root phases (not referenced by any
`creates_next_phases`) are created at `conductor init`. Later phases appear
when their parent phase completes.

```yaml
phases:
  - id: "analysis"
    creates_next_phases: ["planning"]    # planning created after analysis completes
    steps: [...]

  - id: "planning"
    creates_next_phases: ["specification"]
    steps: [...]

  - id: "specification"
    scope: "per_workpackage"             # creates tickets for each WP
    steps: [...]
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

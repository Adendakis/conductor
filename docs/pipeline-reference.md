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
| `hitl_fields` | list | No | Editable fields shown to the reviewer (see below) |
| `type` | string | No | `task` (default) or `reviewer_step` |
| `deliverables` | list | No | Expected output files |
| `input_dependencies` | list | No | Files from previous phases needed as input |

## HITL Editable Fields

Steps can declare structured fields that a human reviewer can edit before
approving. Field definitions and values are embedded in the ticket description
as a YAML block, so the feature works with any tracker backend (SQLite, Jira,
Gitea, etc.).

```yaml
steps:
  - id: "project_setup"
    name: "Project Configuration"
    agent: "__noop__"
    hitl_after: true
    hitl_fields:
      - name: "target_language"
        label: "Target language for code generation"
        type: "select"
        options: ["Java", "C#", "Python"]
        default: "Java"
      - name: "calibration_run"
        label: "Run calibration pass"
        type: "boolean"
        default: false
      - name: "source_path"
        label: "Path to legacy source"
        type: "text"
        default: "input/legacy"
      - name: "max_pods"
        label: "Max parallel pods"
        type: "number"
        default: 3
```

### Field Types

| Type | Dashboard renders as | Value format |
|------|---------------------|-------------|
| `boolean` | Checkbox | `true` / `false` |
| `text` | Text input | String |
| `number` | Number input | Integer |
| `select` | Dropdown | One of `options` |

### How It Works

1. When tickets are created, the field schema and default values are embedded
   in the ticket description between `<!-- HITL_FIELDS_START -->` and
   `<!-- HITL_FIELDS_END -->` markers as a fenced YAML block.

2. In conductor's dashboard, the YAML block renders as a proper form
   (checkboxes, dropdowns, text inputs). In Jira/Gitea, the reviewer edits
   the YAML values directly in the ticket body.

3. When the reviewer approves, the dashboard updates the YAML block with the
   form values. Agents read the values via `parse_hitl_fields(ticket.description)`.

4. The context assembler automatically injects HITL field values into the
   agent's prompt under a "Human Clarifications" section.

### Agent-Generated Clarifications

Agents can also generate HITL fields at runtime when they need human input.
Return `clarifications` in the `ExecutionResult`:

```python
from conductor.models.phases import HitlFieldDefinition

return ExecutionResult(
    success=False,
    summary="Need clarification on 2 items",
    clarifications=[
        HitlFieldDefinition(name="auth_type", label="Is auth LDAP or SAML?",
                            type="select", options=["LDAP", "SAML"]),
        HitlFieldDefinition(name="include_audit", label="Migrate audit tables?",
                            type="boolean", default=False),
    ],
)
```

The watcher embeds the fields in the ticket description and transitions to
`AWAITING_REVIEW`. After the human fills in the form and approves, the agent
re-runs with the answers injected into its prompt context.

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

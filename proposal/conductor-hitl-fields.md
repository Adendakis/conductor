# Conductor Feature: HITL Editable Fields

## Goal

Allow pipeline steps to declare structured fields that a human reviewer can
edit before approving. The field definitions and responses live in the ticket
description (not in tracker-specific metadata), so the feature works with any
tracker backend — SQLite, Jira, Gitea, etc.

## Design Principles

1. **Ticket is the interface** — field schema and values are embedded in the
   ticket description as a parseable YAML block. Any tracker that supports
   editing a ticket body supports this feature.
2. **Dashboard enhances, doesn't require** — conductor's built-in dashboard
   renders the YAML block as a proper form (checkboxes, dropdowns, text inputs).
   In Jira/Gitea, the user edits the YAML directly in the ticket body.
3. **Generic** — conductor doesn't know what the fields mean. It stores and
   parses them. Projects define whatever fields they need.

---

## Pipeline YAML

Steps declare `hitl_fields` — a list of field definitions:

```yaml
- id: "project_setup"
  name: "Project Configuration"
  agent: "config_agent"
  hitl_after: true
  hitl_fields:
    - name: "calibration_run"
      label: "Run calibration pass"
      type: "boolean"
      default: false
    - name: "input_folder"
      label: "Legacy source input folder"
      type: "text"
      default: "input/legacy/legacy_code"
    - name: "target_language"
      label: "Target language"
      type: "select"
      options: ["Java", "C#", "Python", "Go"]
      default: "Java"
    - name: "max_pod_concurrency"
      label: "Max parallel pods"
      type: "number"
      default: 3
```

### Field Types

| Type | Renders as | Value format |
|------|-----------|-------------|
| `boolean` | Checkbox | `true` / `false` |
| `text` | Text input | String |
| `number` | Number input | Integer |
| `select` | Dropdown | One of `options` |

---

## Ticket Description Format

When the ticket creator builds the ticket, it embeds the field schema and
current values as a YAML block in the description, fenced with markers:

```markdown
## Task: Project Configuration

Review the settings below. Edit values as needed, then approve.

<!-- HITL_FIELDS_START -->
```yaml
calibration_run: false        # Run calibration pass (boolean)
input_folder: input/legacy/legacy_code  # Legacy source input folder
target_language: Java         # Target language (options: Java, C#, Python, Go)
max_pod_concurrency: 3        # Max parallel pods
```
<!-- HITL_FIELDS_END -->
```

The markers (`HITL_FIELDS_START` / `HITL_FIELDS_END`) let conductor parse the
block reliably regardless of what other content is in the description.

### In Jira / Gitea

The reviewer opens the ticket, sees the YAML block, edits the values directly
in the ticket body, and transitions the ticket to Approved. The watcher reads
the updated description and parses the YAML block.

### In Conductor's SQLite Dashboard

The dashboard detects the YAML block and renders it as a proper form:
- `boolean` → checkbox
- `text` → text input
- `number` → number input
- `select` → dropdown

When the user clicks Approve, the dashboard updates the YAML block in the
ticket description with the form values before transitioning.

---

## Data Flow

```
Pipeline YAML                    Ticket Description              Agent reads
─────────────                    ──────────────────              ───────────
hitl_fields:                     <!-- HITL_FIELDS_START -->      parse_hitl_fields(ticket)
  - name: calibration_run        calibration_run: false          → {"calibration_run": true,
    type: boolean                 input_folder: input/legacy        "input_folder": "input/legacy",
    default: false                target_language: Java             "target_language": "Java",
  - name: input_folder            max_pod_concurrency: 3            "max_pod_concurrency": 3}
    type: text                   <!-- HITL_FIELDS_END -->
    default: input/legacy
                                 ↓ human edits ↓

                                 <!-- HITL_FIELDS_START -->
                                 calibration_run: true    ← changed
                                 input_folder: input/legacy
                                 target_language: Java
                                 max_pod_concurrency: 3
                                 <!-- HITL_FIELDS_END -->
```

---

## Implementation Plan

### 1. StepDefinition model — add hitl_fields

**File**: `conductor/models/phases.py`

```python
class HitlFieldDefinition(BaseModel):
    name: str
    label: str = ""
    type: str = "text"  # boolean, text, number, select
    default: Any = ""
    options: list[str] = Field(default_factory=list)  # for select type

class StepDefinition(BaseModel):
    # ... existing fields ...
    hitl_fields: list[HitlFieldDefinition] = Field(default_factory=list)
```

**Effort**: 10 min

### 2. Pipeline YAML loader — parse hitl_fields

**File**: `conductor/pipeline/loader.py`

Parse `hitl_fields` from step YAML into `HitlFieldDefinition` objects.

**Effort**: 10 min

### 3. DynamicTicketCreator — embed HITL fields in description

**File**: `conductor/watcher/ticket_creator.py`

When building the ticket description, if the step has `hitl_fields`, append
the YAML block with markers:

```python
if step.hitl_fields:
    fields_yaml = "\n".join(
        f"{f.name}: {f.default}  # {f.label}"
        for f in step.hitl_fields
    )
    description += f"\n\n<!-- HITL_FIELDS_START -->\n```yaml\n{fields_yaml}\n```\n<!-- HITL_FIELDS_END -->"
```

**Effort**: 20 min

### 4. HITL field parser utility

**File**: `conductor/context/hitl_fields.py` (new)

```python
def parse_hitl_fields(description: str) -> dict[str, Any]:
    """Extract HITL field values from a ticket description."""
    # Find content between HITL_FIELDS_START and HITL_FIELDS_END
    # Parse the YAML block
    # Return dict of name → value

def update_hitl_fields(description: str, values: dict[str, Any]) -> str:
    """Update HITL field values in a ticket description."""
    # Replace the YAML block between markers with new values
```

Agents call `parse_hitl_fields(ticket.description)` to read the values.

**Effort**: 30 min

### 5. Dashboard — render HITL fields as form

**File**: `conductor/tracker/web/templates/partials/ticket_panel.html`

When the ticket is in AWAITING_REVIEW and the description contains
`HITL_FIELDS_START`, render the fields as form controls instead of raw YAML.
On approve, serialize the form values back into the YAML block and update
the ticket description.

**Effort**: 1 hour

### 6. Board initializer — embed fields for init-time tickets

**File**: `conductor/board_initializer.py`

Same logic as the ticket creator — embed HITL fields in the description
for tickets created at init time.

**Effort**: 15 min

---

## Total Effort

~2.5 hours

## What Stays Generic

- Conductor doesn't interpret field values — it stores and renders them
- Field definitions come from pipeline.yaml — projects define their own
- Data lives in the ticket description — works with any tracker
- The dashboard form is a convenience — not required for the feature to work
- Agents parse the fields themselves — conductor provides the utility function

## Example: ACM Project Setup

```yaml
- id: "project_setup"
  name: "Project Configuration"
  agent: "config_agent"
  hitl_after: true
  hitl_fields:
    - name: "calibration_run"
      label: "Run calibration pass before full analysis"
      type: "boolean"
      default: false
    - name: "source_code_path"
      label: "Path to legacy source code"
      type: "text"
      default: "input/legacy/legacy_code"
    - name: "database_source_path"
      label: "Path to database source (leave empty to skip)"
      type: "text"
      default: ""
    - name: "target_language"
      label: "Target language for code generation"
      type: "select"
      options: ["Java", "C#", "Python"]
      default: "Java"
    - name: "target_framework"
      label: "Target framework"
      type: "select"
      options: ["Spring Boot", "Quarkus", "Micronaut"]
      default: "Spring Boot"
```

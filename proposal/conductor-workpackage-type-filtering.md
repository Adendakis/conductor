# Conductor Issue: DynamicTicketCreator Should Respect step.workpackage_type

## Summary

When creating per-workpackage tickets for pod-scoped phases, the `DynamicTicketCreator` creates tickets for ALL steps regardless of the step's `workpackage_type` field. Steps with a `workpackage_type` that doesn't match the workpackage's type should be skipped at ticket creation time.

This is a **generic framework feature** — Conductor doesn't need to know what the type values mean (e.g., "flow" vs "job"). It just needs to compare `step.workpackage_type` against the workpackage's type (determined by `_get_workpackage_type()`) and skip non-matching steps.

## How It Works

The pipeline YAML declares conditional steps:
```yaml
- id: "step_3_1_logic_extraction"
  workpackage_type: "flow"        # only for workpackages of type "flow"

- id: "step_3_1J_job_logic_extraction"
  workpackage_type: "job"         # only for workpackages of type "job"

- id: "step_3_0_context_discovery"
  # no workpackage_type            # runs for all workpackages
```

The `StepDefinition` model already has `workpackage_type: Optional[str]`. The `DynamicTicketCreator` already has `_get_workpackage_type(wp_id)` which reads the planning JSON to determine a workpackage's type.

The missing piece: the ticket creation loop doesn't check `step.workpackage_type` before creating a ticket.

## Proposed Fix

In `_create_pod_scoped_tickets()`, before creating a ticket for a step, check if the step has a `workpackage_type` and if so, verify it matches the workpackage's type:

```python
for step in phase.steps:
    # Skip steps that don't match this workpackage's type
    if step.workpackage_type:
        wp_type = self._get_workpackage_type(wp_id)
        if wp_type and wp_type != step.workpackage_type:
            continue
    
    # ... create ticket as before
```

## Impact

For the ACM migration pipeline, this reduces Phase 3 tickets from 312 → ~174 (44% reduction). The savings scale with the number of conditional steps and workpackages.

## Note on Type Determination

The `_get_workpackage_type()` method is project-specific — it reads the project's planning JSON to determine the type. Conductor provides the hook; the project provides the data. The current implementation in `_get_workpackage_type()` reads `Workpackage_Planning.json` and checks the `flowId` prefix, which is ACM-specific. Other projects using Conductor would implement their own type determination logic.

## Priority

High — directly impacts cost and execution time for pod-scoped phases with conditional steps.

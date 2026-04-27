# Conductor Issue: Sequential Workpackage Execution Within Scope Groups

## Problem

When `_create_pod_scoped_tickets()` creates per-workpackage tickets for a pod-scoped phase, all workpackages within the same pod run concurrently. This causes race conditions when multiple workpackages in the same pod write to shared files (e.g., a pod-level glossary).

**Observed**: WP-004 and WP-005 (both in pod-a) run their "Business Context Discovery" step simultaneously. Both attempt to write/update `business-glossary-pod-a.md`. The last writer wins.

**Expected**: Within a pod, workpackages execute sequentially — WP-001 completes all its steps, then WP-002 starts, etc. Different pods can run in parallel.

## Root Cause

`_create_pod_scoped_tickets()` creates tickets for all workpackages but doesn't set:
1. The `pod` field on ticket metadata — it's `None` for all tickets
2. Inter-WP blocking within the same pod — all first-step tickets have `blocked_by: none`

Without these, the async watcher treats all per-WP tickets as independent and dispatches them concurrently (up to `max_concurrent`).

## What Already Exists

The `_create_pod_scoped_tickets()` method was designed to handle this — the implementation description says "sequential within pod (each WP's first step blocked by previous WP's last step)." The `WorktreeManager` already provides:
- `get_pod_for_workpackage(wp_id)` — returns the pod ID for a workpackage
- `get_pod_workpackages(pod_id)` — returns the ordered list of WPs in a pod
- `get_all_pod_ids()` — returns all pod IDs

The `TicketMetadata` model already has a `pod` field.

## Fix

In `_create_pod_scoped_tickets()`:

### 1. Set the `pod` field on every ticket

```python
# When building ticket metadata:
pod_id = worktree_manager.get_pod_for_workpackage(wp_id) if worktree_manager else None
metadata.pod = pod_id
```

### 2. Wire inter-WP sequential blocking within each pod

Group workpackages by pod. Within each pod, the first step of WP[n+1] should be blocked by the last step of WP[n]:

```python
# Group WPs by pod
pods: dict[str, list[str]] = {}
for wp_id in all_workpackages:
    pod_id = worktree_manager.get_pod_for_workpackage(wp_id)
    pods.setdefault(pod_id or "default", []).append(wp_id)

# For each pod, create tickets sequentially
for pod_id, pod_wps in pods.items():
    prev_last_ticket_id = None
    
    for wp_id in pod_wps:
        wp_ticket_ids = create_tickets_for_wp(wp_id, phase, ...)
        
        # Block this WP's first step by the previous WP's last step
        if prev_last_ticket_id and wp_ticket_ids:
            first_ticket = tracker.get_ticket(wp_ticket_ids[0])
            first_ticket.blocked_by.append(prev_last_ticket_id)
            first_ticket.status = TicketStatus.BACKLOG
            tracker.update_ticket(first_ticket)
            
            # Reverse link
            prev_ticket = tracker.get_ticket(prev_last_ticket_id)
            prev_ticket.blocks.append(wp_ticket_ids[0])
            tracker.update_ticket(prev_ticket)
        
        prev_last_ticket_id = wp_ticket_ids[-1] if wp_ticket_ids else None
```

### 3. Result

```
Pod-A (sequential):
  WP-001: Context → Review → Logic → Review → Spec → Review
  WP-002: Context → Review → Logic → Review → Spec → Review  (blocked by WP-001's last step)
  WP-004: Context → Review → ...                               (blocked by WP-002's last step)

Pod-B (sequential, parallel with Pod-A):
  WP-003: Context → Review → Logic → Review → Spec → Review
  WP-006: Context → Review → ...                               (blocked by WP-003's last step)
```

Pods run in parallel (up to `max_concurrent`). Workpackages within a pod run sequentially. Steps within a workpackage run sequentially (already handled by intra-step `depends_on`).

## Generic Framing

This is not ACM-specific. The pattern is: **per-scope-group sequential execution**. When a phase has `scope: per_workpackage` and workpackages are grouped into scope groups (pods), workpackages in the same group execute sequentially to avoid shared-resource conflicts. The scope group data comes from the `WorktreeManager` which reads a generic JSON format.

## Priority

High — without this, any pod-scoped phase with shared per-pod resources (glossaries, consolidated reports, git worktree branches) will have race conditions.

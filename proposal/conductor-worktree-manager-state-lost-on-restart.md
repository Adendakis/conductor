# WorktreeManager state lost on watcher restart

## Status: Open

## Summary

The `AsyncEventWatcher` holds the `WorktreeManager` instance in memory (`self._worktree_manager`). When the watcher process is restarted — intentionally or due to a crash — this state is lost. If a `post_phase_hook: "setup_and_execute_pods"` phase has already completed and worktrees exist on disk, the new watcher instance has no knowledge of them. This causes Phase 3 (or any subsequent per-workpackage phase) ticket creation to silently produce 0 tickets.

## Reproduction

1. Start the watcher. Let it run through Phase 2.5 (pod partitioning).
2. Phase 2.5 completes → `_execute_pod_setup_hook` creates 6 pod worktrees, `_worktree_manager` is populated in memory.
3. COND-008 (pod refinement) goes to `AWAITING_REVIEW`.
4. Stop the watcher (Ctrl+C or crash).
5. Restart the watcher.
6. Approve COND-008 via the dashboard.
7. The new watcher calls `_handle_approved` → `_check_phase_completion` → `_execute_pod_setup_hook`.
8. `_execute_pod_setup_hook` tries to create worktrees again, but they already exist on disk → git errors or partial state.
9. `DynamicTicketCreator._create_pod_scoped_tickets` iterates pods but `get_worktree_path()` returns `None` for all → 0 tickets created.
10. Pipeline stalls silently.

## Root Cause

`_worktree_manager` is initialized to `None` in `__init__` and only populated during `_execute_pod_setup_hook`. There is no mechanism to:

- Persist the worktree manager state to disk
- Reconstruct it from existing worktrees on startup
- Detect that worktrees already exist and reuse them

The `post_phase_hook` is designed as a one-shot operation but the watcher lifecycle doesn't guarantee it runs exactly once.

## Impact

- **Silent failure**: No error is raised. The log shows "Pod setup complete: 0 worktrees created" and "No worktree for pod X, skipping" but the watcher continues polling with nothing to do.
- **Manual recovery required**: The only fix is to run a script that manually creates the tickets (as we did with `scripts/retry_phase3_creation.py`).
- **Affects any pod-scoped phase**: Not specific to Phase 3 — any phase created by a `setup_and_execute_pods` hook is affected.

## Proposed Fix

### Option A: Reconstruct WorktreeManager on startup (recommended)

On watcher startup, check if `worktrees/` directory exists with pod subdirectories. If so, reconstruct the `WorktreeManager` state from disk:

```python
# In AsyncEventWatcher.__init__ or _async_run:
worktrees_base = project_config.project_base_path / config.worktrees_directory
if worktrees_base.exists() and any(worktrees_base.iterdir()):
    pod_path = project_config.project_base_path / config.pod_assignment_path
    if pod_path.exists():
        self._worktree_manager = WorktreeManager(git=self.git, worktrees_base=worktrees_base)
        self._worktree_manager.restore_from_disk(pod_path)
```

This requires adding a `restore_from_disk()` method to `WorktreeManager` that reads `Pod_Assignment.json` and maps existing worktree directories to pod IDs without trying to create them.

### Option B: Make post_phase_hook idempotent

Modify `_execute_pod_setup_hook` to detect existing worktrees and reuse them instead of failing:

```python
def _execute_pod_setup_hook(self, phase_def):
    # If worktree manager already exists and has pods, skip setup
    if self._worktree_manager and self._worktree_manager.get_all_pod_ids():
        log.info("Pod worktrees already set up, reusing")
        return
    # ... existing setup logic, but with idempotent worktree creation
```

### Option C: Persist hook execution state in tracker

Record in the SQLite tracker that the hook has already executed for a given phase. On restart, skip re-execution:

```python
if tracker.get_phase_metadata(phase_id, "pod_setup_complete"):
    # Reconstruct worktree manager from disk instead of re-running hook
    ...
```

## Related

- `conductor-src/conductor/watcher/async_watcher.py` — `_execute_pod_setup_hook`, `_check_phase_completion`
- `conductor-src/conductor/git/worktree_manager.py` — `WorktreeManager`, `setup_pod_worktrees`
- `conductor-src/conductor/watcher/ticket_creator.py` — `_create_pod_scoped_tickets`

## Workaround

Run `scripts/retry_phase3_creation.py` to manually create the worktrees and Phase 3 tickets after the watcher has stalled.

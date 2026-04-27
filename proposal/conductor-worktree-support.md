# Conductor Feature: Git Worktree Support for Pod-Scoped Phases

## Context

The ACM migration pipeline has pod-scoped phases (Phase 3: Business Specification, Phase 4: Test Generation) that run per-workpackage within isolated git worktrees. Each pod gets its own worktree so multiple pods can execute in parallel without branch conflicts.

The old pydantic-acm system implements this in `orchestrator/git/operations.py` (GitOperationsManager) and `structure/tools/create_pod_worktrees.py`. This needs to be ported to Conductor's GitManager and watcher.

Reference implementation: `OLD_PYACM/pydantic-acm/orchestrator/git/operations.py` and `OLD_PYACM/pydantic-acm/orchestrator/pods/pod_orchestrator.py`.

---

## What Needs to Change

### 1. GitManager — New Worktree Methods

**File**: `conductor/git/manager.py`

Add these methods to the existing `GitManager` class:

```python
def create_pod_worktrees(self, pod_assignment_path: Path, worktrees_dir: Path) -> dict[str, Path]:
    """Create git worktrees for each pod defined in Pod_Assignment.json.
    
    Pre-conditions:
    - All Phase 1-2.5 outputs must be committed to main
    - Pod_Assignment.json must exist and be valid
    
    For each pod:
    1. Create branch pod/{pod_id} from main (or current branch)
    2. Create worktree at worktrees_dir/{pod_id} on that branch
    3. Create WP branches wp/{pod_id}/{wp_id} from pod branch
    
    Args:
        pod_assignment_path: Path to Pod_Assignment.json
        worktrees_dir: Directory to create worktrees in (e.g., ./worktrees/)
    
    Returns:
        Dict mapping pod_id → worktree Path
    """

def merge_wp_to_pod(self, wp_id: str, pod_id: str, worktree_path: Path) -> None:
    """Merge a workpackage branch back to the pod branch.
    
    Runs inside the pod's worktree:
    1. git checkout pod/{pod_id}
    2. git merge --no-ff wp/{pod_id}/{wp_id} -m "Merge WP {wp_id}"
    """

def merge_pod_to_main(self, pod_id: str) -> None:
    """Merge a pod branch back to main.
    
    Runs in the main repo:
    1. git checkout main
    2. git merge --no-ff pod/{pod_id} -m "Merge pod {pod_id}"
    """

def checkout_wp_branch(self, wp_id: str, pod_id: str, worktree_path: Path) -> None:
    """Checkout a workpackage branch in the pod's worktree.
    
    git checkout wp/{pod_id}/{wp_id}  (cwd=worktree_path)
    """

def cleanup_worktrees(self, worktrees_dir: Path) -> None:
    """Remove all pod worktrees and their branches.
    
    For each worktree:
    1. git worktree remove worktrees/{pod_id}
    2. git branch -D pod/{pod_id}
    3. git branch -D wp/{pod_id}/* (all WP branches)
    """
```

**Pod_Assignment.json format** (the input to `create_pod_worktrees`):
```json
{
  "pod_assignments": {
    "pod-a": {
      "workpackages": ["WP-001", "WP-003", "WP-007"],
      "domain_cluster": "User Administration"
    },
    "pod-b": {
      "workpackages": ["WP-002", "WP-005"],
      "domain_cluster": "Transaction Processing"
    }
  }
}
```

### 2. Post-Phase Hook in EventWatcher

**File**: `conductor/watcher/event_watcher.py`

After a phase with `post_phase_hook` completes (all tickets in the phase are DONE), the watcher should execute the hook. For `setup_and_execute_pods`:

```python
def _handle_phase_completion(self, phase_id: str) -> None:
    """Called when all tickets in a phase reach DONE status."""
    # Check if the phase definition has a post_phase_hook
    phase_def = self._get_phase_definition(phase_id)
    if phase_def and phase_def.post_phase_hook == "setup_and_execute_pods":
        self._setup_pod_worktrees()

def _setup_pod_worktrees(self) -> None:
    """Create git worktrees for each pod and create per-WP tickets."""
    pod_path = self.project_config.project_base_path / "output" / "analysis" / "workpackages" / "Pod_Assignment.json"
    worktrees_dir = self.project_config.project_base_path / "worktrees"
    
    # 1. Commit all outputs to main
    self.git.add_and_commit("Phase 2.5 outputs committed before pod branching")
    
    # 2. Create worktrees
    pod_worktrees = self.git.create_pod_worktrees(pod_path, worktrees_dir)
    
    # 3. Create per-WP tickets for the next phase(s)
    #    Each ticket's working_directory = worktree path
    #    Each ticket's metadata includes pod_id and workpackage_id
```

### 3. DynamicTicketCreator — Per-WP Ticket Creation with Worktree Paths

**File**: `conductor/watcher/ticket_creator.py`

When creating tickets for `per_workpackage` scoped phases, the ticket's `working_directory` should point to the pod's worktree:

```python
def _create_phase_tickets(self, phase, tracker, scope_id, scope_type):
    # ... existing code ...
    
    # For per_workpackage scope, set working_directory to the pod's worktree
    if scope_type == "workpackage" and scope_id:
        pod_id = self._get_pod_for_workpackage(scope_id)
        if pod_id:
            worktree_path = self.working_directory / "worktrees" / pod_id
            metadata.working_directory = str(worktree_path)
```

### 4. EventWatcher — WP Branch Checkout Before Dispatch

**File**: `conductor/watcher/event_watcher.py`

Before dispatching a per-WP ticket to an agent, checkout the WP branch in the worktree:

```python
def _dispatch_ticket(self, ticket):
    # If this is a per-WP ticket with a worktree working directory
    if ticket.metadata.workpackage and "worktrees/" in ticket.metadata.working_directory:
        pod_id = ticket.metadata.pod
        wp_id = ticket.metadata.workpackage
        worktree_path = Path(ticket.metadata.working_directory)
        self.git.checkout_wp_branch(wp_id, pod_id, worktree_path)
    
    # ... existing dispatch logic ...
```

### 5. After WP Ticket Completes — Merge WP Branch

After a per-WP ticket reaches DONE:

```python
def _handle_ticket_completion(self, ticket):
    # ... existing completion logic ...
    
    # If this is a per-WP ticket, merge WP branch to pod branch
    if ticket.metadata.workpackage and ticket.metadata.pod:
        worktree_path = Path(ticket.metadata.working_directory)
        self.git.merge_wp_to_pod(
            ticket.metadata.workpackage,
            ticket.metadata.pod,
            worktree_path,
        )
```

### 6. After All Pods Complete — Merge to Main

After all per-WP tickets in a pod-scoped phase are DONE:

```python
def _handle_pod_phase_completion(self, phase_id):
    # Get all pods from Pod_Assignment.json
    for pod_id in pod_ids:
        self.git.merge_pod_to_main(pod_id)
    
    # Cleanup worktrees
    self.git.cleanup_worktrees(worktrees_dir)
```

---

## Pipeline YAML Support

The pipeline.yaml loader already parses `scope`, `creates_next_phases`, and step-level `workpackage_type`. The new field needed:

```yaml
- id: "phase_2_5"
  post_phase_hook: "setup_and_execute_pods"  # ← triggers worktree creation
```

The `_parse_phase` function in `conductor/pipeline/loader.py` already reads `post_phase_hook` — it just needs to be wired to the watcher's hook execution.

---

## Workpackage Type Routing

Steps with `workpackage_type: "flow"` or `workpackage_type: "job"` should only execute for matching workpackages. The type is determined by the flowId prefix in Workpackage_Planning.json:

- `FLOW_*` → type = "flow"
- `JOB_*` → type = "job"

The DynamicTicketCreator already has `_get_workpackage_type()` that reads the planning JSON. Steps with a `workpackage_type` that doesn't match should not have tickets created for them.

---

## Remote Agent Variant (Future)

For remote agents, the worktree is replaced by a clone:

```python
def create_pod_clone(self, pod_id: str, clone_dir: Path, remote_url: str) -> Path:
    """Clone the repo for a remote agent."""
    clone_path = clone_dir / pod_id
    subprocess.run(["git", "clone", "--branch", f"pod/{pod_id}", remote_url, str(clone_path)])
    return clone_path
```

After the remote agent completes:
```python
def merge_remote_pod(self, pod_id: str, remote_url: str) -> None:
    """Fetch and merge a remote pod branch."""
    subprocess.run(["git", "fetch", remote_url, f"pod/{pod_id}"])
    subprocess.run(["git", "merge", "--no-ff", f"pod/{pod_id}"])
```

This is not needed for the initial implementation — document it for future work.

---

## Testing

1. Create a test with 2 pods, 3 workpackages each
2. Verify worktrees are created at `worktrees/pod-a`, `worktrees/pod-b`
3. Verify WP branches exist: `wp/pod-a/WP-001`, etc.
4. Verify files written in worktree are on the WP branch
5. Verify merge WP → pod works (no conflicts for non-overlapping WPs)
6. Verify merge pod → main works
7. Verify cleanup removes worktrees and branches

---

## Reference: Old System Implementation

- **GitOperationsManager**: `OLD_PYACM/pydantic-acm/orchestrator/git/operations.py`
- **create_pod_worktrees.py**: `OLD_PYACM/pydantic-acm/testing/tools/create_pod_worktrees.py`
- **PodOrchestrator**: `OLD_PYACM/pydantic-acm/orchestrator/pods/pod_orchestrator.py`
- **Phase definitions**: `OLD_PYACM/pydantic-acm/orchestrator/core/phases.py` (`_phase_3_business_spec`)

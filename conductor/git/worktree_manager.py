"""Worktree manager — manages pod worktree lifecycle."""

import json
import logging
from pathlib import Path
from typing import Optional

from conductor.git.manager import GitManager, MergeResult

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Manages git worktrees for pod-scoped parallel execution.

    Lifecycle:
    1. setup_pod_worktrees() — read Pod_Assignment.json, create pod worktrees + WP branches
    2. Agents write deliverables into the worktree directory on WP branches
    3. After each WP completes → merge WP branch to pod branch
    4. When all pods are done → merge pods to main (respecting merge_order)
    5. Cleanup: remove worktrees and delete branches

    If merge conflicts occur, the merge is aborted and the relevant
    ticket is marked FAILED with the list of conflicted files.
    """

    def __init__(
        self,
        git: GitManager,
        worktrees_base: Optional[Path] = None,
    ):
        self.git = git
        self.worktrees_base = worktrees_base or (git.repo_path / "worktrees")
        self._active_worktrees: dict[str, Path] = {}  # pod_id → worktree path
        self._pod_assignment: dict = {}  # cached pod assignment data
        self._merge_order: list[list[str]] = []  # [before, after] pairs

    # ------------------------------------------------------------------
    # Pod setup
    # ------------------------------------------------------------------

    def setup_pod_worktrees(
        self, pod_assignment_path: Path
    ) -> dict[str, Path]:
        """Create worktrees and WP branches for all pods.

        Reads the generic pod assignment JSON:
        {
            "pods": {
                "pod-a": {"workpackages": ["WP-001", "WP-002"]},
                "pod-b": {"workpackages": ["WP-003"]}
            },
            "merge_order": [["pod-a", "pod-b"]]
        }

        For each pod:
        1. Create branch pod/{pod_id} from HEAD
        2. Create worktree at worktrees_base/{pod_id}
        3. Create WP branches wp/{pod_id}/{wp_id} from pod branch

        Returns dict mapping pod_id → worktree Path.
        """
        data = json.loads(pod_assignment_path.read_text(encoding="utf-8"))
        pods = data.get("pods", {})
        self._merge_order = data.get("merge_order", [])
        self._pod_assignment = pods

        result: dict[str, Path] = {}

        for pod_id, pod_data in pods.items():
            worktree_path = self.get_or_create_worktree(pod_id)
            if not worktree_path:
                logger.error(f"Failed to create worktree for pod {pod_id}")
                continue

            result[pod_id] = worktree_path

            # Create WP branches from the pod branch
            workpackages = pod_data.get("workpackages", [])
            for wp_id in workpackages:
                wp_branch = f"wp/{pod_id}/{wp_id}"
                self.git.create_branch_at(
                    wp_branch, f"pod/{pod_id}", cwd=worktree_path
                )
                logger.info(f"Created WP branch {wp_branch}")

        logger.info(
            f"Pod worktrees ready: {len(result)} pods, "
            f"merge_order={self._merge_order}"
        )
        return result

    def get_pod_for_workpackage(self, wp_id: str) -> Optional[str]:
        """Look up which pod owns a workpackage."""
        for pod_id, pod_data in self._pod_assignment.items():
            if wp_id in pod_data.get("workpackages", []):
                return pod_id
        return None

    def get_pod_workpackages(self, pod_id: str) -> list[str]:
        """Get the ordered list of workpackages for a pod."""
        pod_data = self._pod_assignment.get(pod_id, {})
        return pod_data.get("workpackages", [])

    def get_all_pod_ids(self) -> list[str]:
        """Get all pod IDs."""
        return list(self._pod_assignment.keys())

    # ------------------------------------------------------------------
    # WP branch operations
    # ------------------------------------------------------------------

    def checkout_wp_branch(
        self, wp_id: str, pod_id: str
    ) -> bool:
        """Checkout a WP branch in the pod's worktree."""
        worktree_path = self._active_worktrees.get(pod_id)
        if not worktree_path:
            logger.warning(f"No worktree for pod {pod_id}")
            return False
        wp_branch = f"wp/{pod_id}/{wp_id}"
        return self.git.checkout_branch(wp_branch, worktree_path)

    def merge_wp_to_pod(
        self, wp_id: str, pod_id: str
    ) -> MergeResult:
        """Merge a WP branch back to the pod branch.

        Checks out the pod branch, merges the WP branch, then
        returns to the pod branch.
        """
        worktree_path = self._active_worktrees.get(pod_id)
        if not worktree_path:
            return MergeResult(
                success=False,
                branch=f"wp/{pod_id}/{wp_id}",
                error=f"No worktree for pod {pod_id}",
            )

        pod_branch = f"pod/{pod_id}"
        wp_branch = f"wp/{pod_id}/{wp_id}"

        # Checkout pod branch
        if not self.git.checkout_branch(pod_branch, worktree_path):
            return MergeResult(
                success=False,
                branch=wp_branch,
                error=f"Could not checkout {pod_branch}",
            )

        # Merge WP branch into pod branch
        result = self.git.merge_branch(
            wp_branch,
            message=f"Merge {wp_id} into {pod_id}",
            cwd=worktree_path,
        )

        if result.success:
            logger.info(f"Merged {wp_branch} → {pod_branch}")
        else:
            logger.warning(
                f"Failed to merge {wp_branch} → {pod_branch}: {result.error}"
            )

        return result

    # ------------------------------------------------------------------
    # Pod → main merge
    # ------------------------------------------------------------------

    def merge_pods_to_main(self) -> list[MergeResult]:
        """Merge all pod branches to main, respecting merge_order.

        Returns list of MergeResults (one per pod).
        """
        # Build topological order from merge_order constraints
        pod_ids = list(self._active_worktrees.keys())
        ordered = self._topological_sort(pod_ids, self._merge_order)

        results: list[MergeResult] = []
        for pod_id in ordered:
            result = self.merge_pod(pod_id)
            results.append(result)
            if not result.success:
                logger.error(
                    f"Pod {pod_id} merge to main failed — "
                    f"stopping further merges"
                )
                break

        return results

    @staticmethod
    def _topological_sort(
        items: list[str], order_pairs: list[list[str]]
    ) -> list[str]:
        """Sort items respecting [before, after] ordering constraints.

        Items not mentioned in constraints keep their original order.
        """
        from collections import defaultdict, deque

        # Build adjacency list and in-degree count
        graph: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {item: 0 for item in items}

        for pair in order_pairs:
            if len(pair) != 2:
                continue
            before, after = pair[0], pair[1]
            if before in in_degree and after in in_degree:
                graph[before].append(after)
                in_degree[after] += 1

        # Kahn's algorithm
        queue = deque(
            item for item in items if in_degree[item] == 0
        )
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If cycle detected, append remaining items
        remaining = [item for item in items if item not in result]
        if remaining:
            logger.warning(
                f"Cycle detected in merge_order, appending: {remaining}"
            )
            result.extend(remaining)

        return result

    # ------------------------------------------------------------------
    # Existing methods (preserved)
    # ------------------------------------------------------------------

    def get_or_create_worktree(self, pod_id: str) -> Optional[Path]:
        """Get existing worktree path for a pod, or create one."""
        if pod_id in self._active_worktrees:
            path = self._active_worktrees[pod_id]
            if path.exists():
                return path

        branch = f"pod/{pod_id}"
        worktree_path = self.worktrees_base / pod_id

        if self.git.create_worktree(branch, str(worktree_path)):
            self._active_worktrees[pod_id] = worktree_path
            logger.info(f"Created worktree for pod {pod_id}: {worktree_path}")
            return worktree_path

        logger.error(f"Failed to create worktree for pod {pod_id}")
        return None

    def commit_in_pod(
        self, pod_id: str, paths: list[str], message: str
    ) -> Optional[str]:
        """Commit deliverables within a pod's worktree."""
        worktree_path = self._active_worktrees.get(pod_id)
        if not worktree_path or not worktree_path.exists():
            logger.warning(f"No worktree for pod {pod_id}")
            return None
        return self.git.commit_in_worktree(worktree_path, paths, message)

    def merge_pod(self, pod_id: str) -> MergeResult:
        """Merge a pod's branch back to main and clean up."""
        branch = f"pod/{pod_id}"
        worktree_path = self._active_worktrees.get(pod_id)

        # Remove worktree first (required before merge in some git versions)
        if worktree_path and worktree_path.exists():
            self.git.remove_worktree(str(worktree_path))

        # Merge pod branch to main
        result = self.git.merge_worktree(branch)

        # Cleanup branches on success
        if result.success:
            self.git.delete_branch(branch)
            # Delete WP branches for this pod
            for wp_id in self.get_pod_workpackages(pod_id):
                self.git.force_delete_branch(f"wp/{pod_id}/{wp_id}")
            self._active_worktrees.pop(pod_id, None)
            logger.info(f"Pod {pod_id} merged to main and cleaned up")
        else:
            logger.warning(
                f"Pod {pod_id} merge failed: {result.error}. "
                f"Conflicted files: {result.conflicted_files}"
            )

        return result

    def get_worktree_path(self, pod_id: str) -> Optional[Path]:
        """Get the worktree path for a pod, if it exists."""
        return self._active_worktrees.get(pod_id)

    def cleanup_all(self) -> None:
        """Remove all active worktrees. Called on shutdown."""
        for pod_id in list(self._active_worktrees.keys()):
            path = self._active_worktrees[pod_id]
            if path.exists():
                self.git.remove_worktree(str(path))
            self._active_worktrees.pop(pod_id, None)

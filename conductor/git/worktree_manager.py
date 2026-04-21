"""Worktree manager — manages pod worktree lifecycle."""

import logging
from pathlib import Path
from typing import Optional

from conductor.git.manager import GitManager, MergeResult

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Manages git worktrees for pod-scoped parallel execution.

    Lifecycle:
    1. When a pod's first ticket starts → create worktree
    2. Agents write deliverables into the worktree directory
    3. After each agent completes → commit in worktree
    4. When all pod tickets are done → merge worktree back to main
    5. Cleanup: remove worktree directory and delete branch

    If merge conflicts occur, the merge is aborted and the pod's
    final ticket is marked FAILED with the list of conflicted files.
    """

    def __init__(
        self,
        git: GitManager,
        worktrees_base: Optional[Path] = None,
    ):
        self.git = git
        self.worktrees_base = worktrees_base or (git.repo_path / "worktrees")
        self._active_worktrees: dict[str, Path] = {}  # pod_id → worktree path

    def get_or_create_worktree(self, pod_id: str) -> Optional[Path]:
        """Get existing worktree path for a pod, or create one.

        Returns the worktree directory path, or None if creation failed.
        """
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
        """Commit deliverables within a pod's worktree.

        Returns commit SHA or None.
        """
        worktree_path = self._active_worktrees.get(pod_id)
        if not worktree_path or not worktree_path.exists():
            logger.warning(f"No worktree for pod {pod_id}")
            return None

        return self.git.commit_in_worktree(worktree_path, paths, message)

    def merge_pod(self, pod_id: str) -> MergeResult:
        """Merge a pod's branch back to main and clean up.

        Returns MergeResult with success/failure and conflicted files.
        """
        branch = f"pod/{pod_id}"
        worktree_path = self._active_worktrees.get(pod_id)

        # Remove worktree first (required before merge in some git versions)
        if worktree_path and worktree_path.exists():
            self.git.remove_worktree(str(worktree_path))

        # Merge
        result = self.git.merge_worktree(branch)

        # Cleanup branch on success
        if result.success:
            self.git.delete_branch(branch)
            self._active_worktrees.pop(pod_id, None)
            logger.info(f"Pod {pod_id} merged and cleaned up")
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

"""Git manager — manages git operations tied to ticket lifecycle."""

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Result of a worktree merge operation."""

    success: bool
    branch: str
    conflicted_files: list[str] = field(default_factory=list)
    error: Optional[str] = None


class GitManager:
    """Manages git operations tied to the ticket lifecycle."""

    def __init__(self, repo_path: Path = Path("."), enabled: bool = True):
        self.repo_path = repo_path
        self.enabled = enabled

    def _run(
        self,
        args: list[str],
        check: bool = True,
        cwd: Optional[Path] = None,
    ) -> Optional[subprocess.CompletedProcess]:
        """Run a git command. Returns None if git is disabled or fails gracefully."""
        if not self.enabled:
            return None

        run_cwd = cwd or self.repo_path

        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                check=check,
            )
            return result
        except FileNotFoundError:
            logger.warning("Git not found on PATH — skipping git operations")
            self.enabled = False
            return None
        except subprocess.CalledProcessError as e:
            if check:
                logger.warning(
                    f"Git command failed: git {' '.join(args)}: {e.stderr}"
                )
            return None

    def tag(self, tag_name: str, message: str = "") -> bool:
        """Create an annotated git tag at HEAD."""
        msg = message or tag_name
        result = self._run(["tag", "-a", tag_name, "-m", msg], check=False)
        if result and result.returncode == 0:
            logger.debug(f"Created tag: {tag_name}")
            return True
        return False

    def commit_deliverables(
        self, paths: list[str], message: str
    ) -> Optional[str]:
        """Stage and commit deliverable files. Returns commit SHA or None."""
        if not self.enabled:
            return None

        for path in paths:
            full_path = self.repo_path / path
            if full_path.exists():
                self._run(["add", path], check=False)

        result = self._run(
            ["commit", "-m", message, "--allow-empty"], check=False
        )
        if result and result.returncode == 0:
            sha_result = self._run(["rev-parse", "HEAD"], check=False)
            if sha_result and sha_result.returncode == 0:
                return sha_result.stdout.strip()
        return None

    def commit_phase_outputs(
        self, phase_id: str, scope_id: str = ""
    ) -> Optional[str]:
        """Commit all outputs for a phase."""
        scope_label = f" ({scope_id})" if scope_id else ""
        message = f"conductor: {phase_id}{scope_label} completed"
        self._run(["add", "output/"], check=False)
        result = self._run(
            ["commit", "-m", message, "--allow-empty"], check=False
        )
        if result and result.returncode == 0:
            sha_result = self._run(["rev-parse", "HEAD"], check=False)
            if sha_result and sha_result.returncode == 0:
                return sha_result.stdout.strip()
        return None

    # --- Worktree lifecycle ---

    def create_worktree(self, branch: str, path: str) -> bool:
        """Create a git worktree for pod-scoped execution.

        Creates a new branch from HEAD and checks it out in a separate directory.
        """
        if not self.enabled:
            return False

        worktree_path = Path(path)
        if not worktree_path.is_absolute():
            worktree_path = self.repo_path / path

        # Create branch from current HEAD
        result = self._run(["branch", branch, "HEAD"], check=False)
        if not result or result.returncode != 0:
            # Branch might already exist
            logger.warning(f"Could not create branch {branch}: {result.stderr if result else 'disabled'}")

        # Create worktree
        result = self._run(
            ["worktree", "add", str(worktree_path), branch], check=False
        )
        if result and result.returncode == 0:
            logger.info(f"Created worktree: {worktree_path} on branch {branch}")
            return True

        logger.warning(
            f"Failed to create worktree: {result.stderr if result else 'disabled'}"
        )
        return False

    def commit_in_worktree(
        self, worktree_path: Path, paths: list[str], message: str
    ) -> Optional[str]:
        """Stage and commit files within a worktree directory.

        Args:
            worktree_path: Path to the worktree directory
            paths: Relative paths within the worktree to stage
            message: Commit message

        Returns:
            Commit SHA or None
        """
        if not self.enabled:
            return None

        for path in paths:
            full = worktree_path / path
            if full.exists():
                self._run(["add", path], check=False, cwd=worktree_path)

        result = self._run(
            ["commit", "-m", message, "--allow-empty"],
            check=False,
            cwd=worktree_path,
        )
        if result and result.returncode == 0:
            sha_result = self._run(
                ["rev-parse", "HEAD"], check=False, cwd=worktree_path
            )
            if sha_result and sha_result.returncode == 0:
                return sha_result.stdout.strip()
        return None

    def merge_worktree(
        self, branch: str, message: str = ""
    ) -> MergeResult:
        """Merge a pod branch back to the main branch.

        Returns MergeResult with success/failure and any conflicted files.
        """
        if not self.enabled:
            return MergeResult(success=False, branch=branch, error="Git disabled")

        msg = message or f"Merge pod branch: {branch}"

        result = self._run(
            ["merge", branch, "--no-ff", "-m", msg], check=False
        )

        if result and result.returncode == 0:
            logger.info(f"Merged branch {branch} successfully")
            return MergeResult(success=True, branch=branch)

        # Merge failed — check for conflicts
        if result and "CONFLICT" in (result.stdout + result.stderr):
            # Get list of conflicted files
            conflict_result = self._run(
                ["diff", "--name-only", "--diff-filter=U"], check=False
            )
            conflicted = []
            if conflict_result and conflict_result.stdout:
                conflicted = [
                    f.strip()
                    for f in conflict_result.stdout.splitlines()
                    if f.strip()
                ]

            # Abort the merge to leave repo in clean state
            self._run(["merge", "--abort"], check=False)

            logger.warning(
                f"Merge conflict on branch {branch}: {conflicted}"
            )
            return MergeResult(
                success=False,
                branch=branch,
                conflicted_files=conflicted,
                error=f"Merge conflict in {len(conflicted)} file(s)",
            )

        # Other merge failure
        error_msg = result.stderr.strip() if result else "Unknown error"
        return MergeResult(
            success=False, branch=branch, error=error_msg
        )

    def remove_worktree(self, path: str) -> bool:
        """Remove a git worktree and optionally its branch."""
        if not self.enabled:
            return False

        worktree_path = Path(path)
        if not worktree_path.is_absolute():
            worktree_path = self.repo_path / path

        result = self._run(
            ["worktree", "remove", str(worktree_path), "--force"],
            check=False,
        )
        if result and result.returncode == 0:
            logger.info(f"Removed worktree: {worktree_path}")
            return True
        return False

    def delete_branch(self, branch: str) -> bool:
        """Delete a branch after merge."""
        result = self._run(["branch", "-d", branch], check=False)
        return result is not None and result.returncode == 0

    def list_worktrees(self) -> list[dict[str, str]]:
        """List all active worktrees."""
        if not self.enabled:
            return []

        result = self._run(["worktree", "list", "--porcelain"], check=False)
        if not result or result.returncode != 0:
            return []

        worktrees = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "bare":
                current["bare"] = "true"
        if current:
            worktrees.append(current)

        return worktrees

    def is_repo(self) -> bool:
        """Check if the working directory is a git repository."""
        result = self._run(["rev-parse", "--git-dir"], check=False)
        return result is not None and result.returncode == 0

    # --- WP branch operations (for pod worktree support) ---

    def checkout_branch(self, branch: str, cwd: Path) -> bool:
        """Checkout a branch in a given directory (worktree).

        Args:
            branch: Branch name to checkout
            cwd: Directory to run the checkout in (worktree path)
        """
        if not self.enabled:
            return False
        result = self._run(["checkout", branch], check=False, cwd=cwd)
        if result and result.returncode == 0:
            logger.debug(f"Checked out {branch} in {cwd}")
            return True
        logger.warning(
            f"Failed to checkout {branch} in {cwd}: "
            f"{result.stderr if result else 'disabled'}"
        )
        return False

    def create_branch_at(
        self, branch: str, base: str = "HEAD", cwd: Optional[Path] = None
    ) -> bool:
        """Create a branch from a given base ref.

        Args:
            branch: New branch name
            base: Base ref (default HEAD)
            cwd: Directory to run in (default repo_path)
        """
        if not self.enabled:
            return False
        result = self._run(["branch", branch, base], check=False, cwd=cwd)
        if result and result.returncode == 0:
            logger.debug(f"Created branch {branch} from {base}")
            return True
        # Branch might already exist
        logger.debug(
            f"Could not create branch {branch}: "
            f"{result.stderr.strip() if result else 'disabled'}"
        )
        return False

    def merge_branch(
        self, branch: str, message: str = "", cwd: Optional[Path] = None
    ) -> MergeResult:
        """Merge a branch in a given directory. Aborts on conflict.

        Args:
            branch: Branch to merge
            message: Merge commit message
            cwd: Directory to run in (default repo_path)
        """
        if not self.enabled:
            return MergeResult(success=False, branch=branch, error="Git disabled")

        msg = message or f"Merge {branch}"
        result = self._run(
            ["merge", branch, "--no-ff", "-m", msg], check=False, cwd=cwd
        )

        if result and result.returncode == 0:
            logger.info(f"Merged {branch} successfully")
            return MergeResult(success=True, branch=branch)

        # Check for conflicts
        if result and "CONFLICT" in (result.stdout + result.stderr):
            conflict_result = self._run(
                ["diff", "--name-only", "--diff-filter=U"], check=False, cwd=cwd
            )
            conflicted = []
            if conflict_result and conflict_result.stdout:
                conflicted = [
                    f.strip()
                    for f in conflict_result.stdout.splitlines()
                    if f.strip()
                ]
            self._run(["merge", "--abort"], check=False, cwd=cwd)
            logger.warning(f"Merge conflict on {branch}: {conflicted}")
            return MergeResult(
                success=False,
                branch=branch,
                conflicted_files=conflicted,
                error=f"Merge conflict in {len(conflicted)} file(s)",
            )

        error_msg = result.stderr.strip() if result else "Unknown error"
        return MergeResult(success=False, branch=branch, error=error_msg)

    def add_and_commit(
        self, message: str, cwd: Optional[Path] = None
    ) -> Optional[str]:
        """Stage all changes and commit. Returns SHA or None."""
        if not self.enabled:
            return None
        run_cwd = cwd or self.repo_path
        self._run(["add", "-A"], check=False, cwd=run_cwd)
        result = self._run(
            ["commit", "-m", message, "--allow-empty"],
            check=False,
            cwd=run_cwd,
        )
        if result and result.returncode == 0:
            sha_result = self._run(
                ["rev-parse", "HEAD"], check=False, cwd=run_cwd
            )
            if sha_result and sha_result.returncode == 0:
                return sha_result.stdout.strip()
        return None

    def force_delete_branch(self, branch: str) -> bool:
        """Force-delete a branch (even if not fully merged)."""
        result = self._run(["branch", "-D", branch], check=False)
        return result is not None and result.returncode == 0

"""Tests for git worktree integration and merge strategy."""

import subprocess
from pathlib import Path

import pytest

from conductor.git.manager import GitManager, MergeResult
from conductor.git.worktree_manager import WorktreeManager


def _init_git_repo(path: Path) -> GitManager:
    """Create a fresh git repo with an initial commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True, check=True,
    )
    # Initial commit
    (path / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path, capture_output=True, check=True,
    )
    return GitManager(repo_path=path, enabled=True)


class TestGitManagerWorktrees:
    """Test GitManager worktree operations."""

    def test_create_and_remove_worktree(self, tmp_path):
        """Can create a worktree and remove it."""
        git = _init_git_repo(tmp_path / "repo")
        wt_path = tmp_path / "repo" / "worktrees" / "pod-a"

        assert git.create_worktree("pod-a", str(wt_path))
        assert wt_path.exists()
        assert (wt_path / "README.md").exists()

        assert git.remove_worktree(str(wt_path))
        assert not wt_path.exists()

    def test_commit_in_worktree(self, tmp_path):
        """Can commit files within a worktree."""
        git = _init_git_repo(tmp_path / "repo")
        wt_path = tmp_path / "repo" / "worktrees" / "pod-a"
        git.create_worktree("pod-a", str(wt_path))

        # Write a file in the worktree
        (wt_path / "output").mkdir()
        (wt_path / "output" / "result.md").write_text("# Result\n\nFrom pod-a.\n")

        sha = git.commit_in_worktree(
            wt_path, ["output/result.md"], "pod-a: result"
        )
        assert sha is not None
        assert len(sha) == 40  # full SHA

    def test_merge_no_conflict(self, tmp_path):
        """Merge succeeds when changes don't overlap."""
        git = _init_git_repo(tmp_path / "repo")
        repo = tmp_path / "repo"

        # Create two worktrees
        wt_a = repo / "worktrees" / "pod-a"
        wt_b = repo / "worktrees" / "pod-b"
        git.create_worktree("pod-a", str(wt_a))
        git.create_worktree("pod-b", str(wt_b))

        # Write non-overlapping files
        (wt_a / "output").mkdir(exist_ok=True)
        (wt_a / "output" / "wp-001.md").write_text("# WP-001\n")
        git.commit_in_worktree(wt_a, ["output/wp-001.md"], "pod-a: wp-001")

        (wt_b / "output").mkdir(exist_ok=True)
        (wt_b / "output" / "wp-002.md").write_text("# WP-002\n")
        git.commit_in_worktree(wt_b, ["output/wp-002.md"], "pod-b: wp-002")

        # Remove worktrees before merge
        git.remove_worktree(str(wt_a))
        git.remove_worktree(str(wt_b))

        # Merge pod-a
        result_a = git.merge_worktree("pod-a")
        assert result_a.success, f"Merge pod-a failed: {result_a.error}"

        # Merge pod-b
        result_b = git.merge_worktree("pod-b")
        assert result_b.success, f"Merge pod-b failed: {result_b.error}"

        # Both files should exist on main
        assert (repo / "output" / "wp-001.md").exists()
        assert (repo / "output" / "wp-002.md").exists()

    def test_merge_conflict_detected(self, tmp_path):
        """Merge conflict is detected and reported gracefully."""
        git = _init_git_repo(tmp_path / "repo")
        repo = tmp_path / "repo"

        # Create two worktrees
        wt_a = repo / "worktrees" / "pod-a"
        wt_b = repo / "worktrees" / "pod-b"
        git.create_worktree("pod-a", str(wt_a))
        git.create_worktree("pod-b", str(wt_b))

        # Write CONFLICTING content to the same file
        (wt_a / "shared.txt").write_text("Content from pod-a\n")
        git.commit_in_worktree(wt_a, ["shared.txt"], "pod-a: shared")

        (wt_b / "shared.txt").write_text("Content from pod-b\n")
        git.commit_in_worktree(wt_b, ["shared.txt"], "pod-b: shared")

        # Remove worktrees
        git.remove_worktree(str(wt_a))
        git.remove_worktree(str(wt_b))

        # Merge pod-a (should succeed — first merge)
        result_a = git.merge_worktree("pod-a")
        assert result_a.success

        # Merge pod-b (should FAIL — conflict on shared.txt)
        result_b = git.merge_worktree("pod-b")
        assert not result_b.success
        assert "shared.txt" in result_b.conflicted_files
        assert result_b.error is not None

        # Repo should be clean (merge was aborted)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True,
        )
        assert status.stdout.strip() == "", "Repo should be clean after abort"

    def test_list_worktrees(self, tmp_path):
        """Can list active worktrees."""
        git = _init_git_repo(tmp_path / "repo")
        repo = tmp_path / "repo"

        wt = repo / "worktrees" / "pod-a"
        git.create_worktree("pod-a", str(wt))

        worktrees = git.list_worktrees()
        assert len(worktrees) >= 2  # main + pod-a
        branches = [w.get("branch", "") for w in worktrees]
        assert any("pod-a" in b for b in branches)


class TestWorktreeManager:
    """Test the WorktreeManager lifecycle."""

    def test_full_lifecycle(self, tmp_path):
        """Create worktree → commit → merge → cleanup."""
        git = _init_git_repo(tmp_path / "repo")
        repo = tmp_path / "repo"
        mgr = WorktreeManager(git, worktrees_base=repo / "worktrees")

        # Create
        wt_path = mgr.get_or_create_worktree("pod-a")
        assert wt_path is not None
        assert wt_path.exists()

        # Write and commit
        (wt_path / "output").mkdir(exist_ok=True)
        (wt_path / "output" / "result.md").write_text("# Done\n")
        sha = mgr.commit_in_pod("pod-a", ["output/result.md"], "done")
        assert sha is not None

        # Merge
        result = mgr.merge_pod("pod-a")
        assert result.success

        # File should be on main
        assert (repo / "output" / "result.md").exists()

        # Worktree should be cleaned up
        assert not wt_path.exists()

    def test_get_existing_worktree(self, tmp_path):
        """Getting a worktree twice returns the same path."""
        git = _init_git_repo(tmp_path / "repo")
        mgr = WorktreeManager(git, worktrees_base=tmp_path / "repo" / "wt")

        path1 = mgr.get_or_create_worktree("pod-x")
        path2 = mgr.get_or_create_worktree("pod-x")
        assert path1 == path2

    def test_merge_conflict_lifecycle(self, tmp_path):
        """Merge conflict leaves repo clean and returns error details."""
        git = _init_git_repo(tmp_path / "repo")
        repo = tmp_path / "repo"
        mgr = WorktreeManager(git, worktrees_base=repo / "worktrees")

        # Two pods write to the same file
        wt_a = mgr.get_or_create_worktree("pod-a")
        wt_b = mgr.get_or_create_worktree("pod-b")

        (wt_a / "config.yaml").write_text("setting: from-a\n")
        mgr.commit_in_pod("pod-a", ["config.yaml"], "pod-a config")

        (wt_b / "config.yaml").write_text("setting: from-b\n")
        mgr.commit_in_pod("pod-b", ["config.yaml"], "pod-b config")

        # First merge succeeds
        result_a = mgr.merge_pod("pod-a")
        assert result_a.success

        # Second merge fails with conflict
        result_b = mgr.merge_pod("pod-b")
        assert not result_b.success
        assert len(result_b.conflicted_files) > 0

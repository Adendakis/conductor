"""Tests for WorktreeManager state recovery after restart."""

import json
import subprocess
from pathlib import Path

import pytest

from conductor.git.manager import GitManager
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
    (path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path, capture_output=True, check=True,
    )
    return GitManager(repo_path=path, enabled=True)


def _write_pod_assignment(path: Path) -> None:
    """Write a sample pod assignment JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "pods": {
            "pod-a": {"workpackages": ["WP-001", "WP-002"]},
            "pod-b": {"workpackages": ["WP-003"]},
        },
        "merge_order": [["pod-a", "pod-b"]],
    }))


def test_restore_from_disk(tmp_path):
    """WorktreeManager restores state from existing worktree directories."""
    repo = tmp_path / "repo"
    git = _init_git_repo(repo)
    pod_json = repo / "Pod_Assignment.json"
    _write_pod_assignment(pod_json)

    # First run: create worktrees
    mgr1 = WorktreeManager(git=git, worktrees_base=repo / "worktrees")
    result = mgr1.setup_pod_worktrees(pod_json)
    assert len(result) == 2
    assert mgr1.get_all_pod_ids() == ["pod-a", "pod-b"]
    assert mgr1.get_pod_for_workpackage("WP-001") == "pod-a"

    # Simulate restart: new manager with no state
    mgr2 = WorktreeManager(git=git, worktrees_base=repo / "worktrees")
    assert mgr2.get_all_pod_ids() == []  # no state yet

    # Restore from disk
    restored = mgr2.restore_from_disk(pod_json)
    assert restored is True
    assert set(mgr2.get_all_pod_ids()) == {"pod-a", "pod-b"}
    assert mgr2.get_pod_for_workpackage("WP-001") == "pod-a"
    assert mgr2.get_worktree_path("pod-a") is not None
    assert mgr2.get_worktree_path("pod-a").exists()


def test_restore_no_worktrees(tmp_path):
    """Restore returns False when no worktrees exist."""
    repo = tmp_path / "repo"
    git = _init_git_repo(repo)
    pod_json = repo / "Pod_Assignment.json"
    _write_pod_assignment(pod_json)

    mgr = WorktreeManager(git=git, worktrees_base=repo / "worktrees")
    assert mgr.restore_from_disk(pod_json) is False


def test_setup_idempotent(tmp_path):
    """Calling setup_pod_worktrees twice doesn't fail or duplicate."""
    repo = tmp_path / "repo"
    git = _init_git_repo(repo)
    pod_json = repo / "Pod_Assignment.json"
    _write_pod_assignment(pod_json)

    mgr = WorktreeManager(git=git, worktrees_base=repo / "worktrees")

    # First setup
    result1 = mgr.setup_pod_worktrees(pod_json)
    assert len(result1) == 2

    # Second setup (idempotent — should reuse existing)
    result2 = mgr.setup_pod_worktrees(pod_json)
    assert len(result2) == 2
    assert result1 == result2


def test_is_setup_complete(tmp_path):
    """is_setup_complete reflects actual state."""
    repo = tmp_path / "repo"
    git = _init_git_repo(repo)
    pod_json = repo / "Pod_Assignment.json"
    _write_pod_assignment(pod_json)

    mgr = WorktreeManager(git=git, worktrees_base=repo / "worktrees")
    assert mgr.is_setup_complete() is False

    mgr.setup_pod_worktrees(pod_json)
    assert mgr.is_setup_complete() is True

    # After restore
    mgr2 = WorktreeManager(git=git, worktrees_base=repo / "worktrees")
    assert mgr2.is_setup_complete() is False
    mgr2.restore_from_disk(pod_json)
    assert mgr2.is_setup_complete() is True

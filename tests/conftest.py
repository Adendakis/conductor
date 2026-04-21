"""Shared test fixtures for conductor tests."""

import tempfile
from pathlib import Path

import pytest

from conductor.executor.registry import AgentRegistry
from conductor.agents import build_default_registry
from conductor.agents.generic import NoOpExecutor
from conductor.git.manager import GitManager
from conductor.models.config import ProjectConfig, WatcherConfig
from conductor.tracker.sqlite_backend import SqliteTracker


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory as Path."""
    return tmp_path


@pytest.fixture
def tracker(tmp_path):
    """Provide a fresh SQLite tracker in a temp directory."""
    db_path = str(tmp_path / "tracker.db")
    trk = SqliteTracker(db_path=db_path)
    trk.connect({})
    return trk


@pytest.fixture
def git_manager(tmp_path):
    """Provide a GitManager with git disabled (for unit tests)."""
    return GitManager(repo_path=tmp_path, enabled=False)


@pytest.fixture
def project_config(tmp_path):
    """Provide a ProjectConfig pointing to a temp directory."""
    return ProjectConfig(project_base_path=tmp_path)


@pytest.fixture
def watcher_config():
    """Provide a default WatcherConfig."""
    return WatcherConfig(poll_interval_seconds=1, hitl_default=False)


@pytest.fixture
def registry():
    """Provide a registry with generic executors and NoOp fallback."""
    reg = build_default_registry()
    reg.set_fallback(NoOpExecutor("__fallback__"))
    return reg

"""Project and watcher configuration models."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class LegacySystem(BaseModel):
    """Description of the legacy system being migrated."""

    language: str = "COBOL"
    framework: str = "CICS"
    platform: str = "z/OS Mainframe"
    database: str = "VSAM"


class TargetSystem(BaseModel):
    """Description of the target system."""

    language: str = "Java"
    framework: str = "Spring Boot"
    platform: str = "Cloud Native"
    database: str = "PostgreSQL"


class ProjectConfig(BaseModel):
    """Project-level configuration. Loaded from project-config.json."""

    project_name: str = "project"
    project_base_path: Path = Path(".")
    output_base_path: Optional[Path] = None

    source_code: Path = Path("input/legacy/legacy_code")
    database_source: Optional[Path] = None

    legacy_system: LegacySystem = Field(default_factory=LegacySystem)
    target_system: TargetSystem = Field(default_factory=TargetSystem)

    max_pod_concurrency: int = 1
    default_model_id: str = "anthropic.claude-sonnet-4-20250514"
    aws_region: str = "us-east-1"

    @property
    def effective_output_base(self) -> Path:
        return self.output_base_path or self.project_base_path

    @property
    def source_code_analysis_output(self) -> Path:
        return self.effective_output_base / "output" / "analysis" / "source_code"

    @property
    def database_analysis_output(self) -> Path:
        return self.effective_output_base / "output" / "analysis" / "database"

    @property
    def workpackage_base_path(self) -> Path:
        return self.effective_output_base / "output" / "analysis" / "workpackages"


class WatcherConfig(BaseModel):
    """Configuration for the event watcher."""

    tracker_backend: str = "sqlite"
    tracker_config: dict = Field(default_factory=dict)

    pipeline_mode: str = "full"
    pipeline_file: str = ""  # Path to pipeline.yaml (if using YAML loader)
    agents_module: str = ""  # Python module path with register() function

    poll_interval_seconds: int = 30
    max_concurrent_agents: int = 3

    hitl_default: bool = True
    hitl_override_phases: dict[str, bool] = Field(default_factory=dict)
    hitl_override_steps: dict[str, bool] = Field(default_factory=dict)

    executor_timeout_seconds: int = 900
    max_rework_iterations: int = 3
    stale_ticket_threshold_seconds: int = 1800  # 30 min — reset IN_PROGRESS tickets older than this

    git_enabled: bool = True
    git_tag_on_transitions: bool = True
    git_commit_on_completion: bool = True

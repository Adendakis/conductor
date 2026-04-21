"""Pipeline phase and step definition models."""

from typing import Optional

from pydantic import BaseModel, Field


class DeliverableSpec(BaseModel):
    """Specification for an expected deliverable from a step."""

    name: str = ""
    output_path: str = ""
    file_type: str = "markdown"  # markdown, json, sql, binary, directory
    required: bool = True
    min_size_bytes: int = 100
    per_workpackage: bool = False


class QualityGateDefinition(BaseModel):
    """Quality gate checks run after all steps in a phase complete."""

    required_deliverables: list[str] = Field(default_factory=list)
    require_reviewer_approval: bool = True
    custom_validators: list[str] = Field(default_factory=list)


class StepDefinition(BaseModel):
    """Definition of a single step within a phase."""

    step_id: str
    display_name: str = ""
    agent_name: str = ""
    prompt_file: str = ""
    expected_deliverables: list[DeliverableSpec] = Field(default_factory=list)
    input_dependencies: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    # Reviewer configuration
    is_reviewer: bool = False
    reviewer_for: Optional[str] = None
    rework_target: Optional[str] = None
    max_review_iterations: int = 3

    # Conditional execution
    optional: bool = False
    workpackage_type: Optional[str] = None

    # HITL configuration
    hitl_after: bool = True
    auto_approve_on_validation: bool = False


class PhaseDefinition(BaseModel):
    """Definition of a migration phase."""

    phase_id: str
    display_name: str = ""
    depends_on: list[str] = Field(default_factory=list)
    steps: list[StepDefinition] = Field(default_factory=list)
    quality_gate: QualityGateDefinition = Field(
        default_factory=QualityGateDefinition
    )
    execution_scope: str = "global"
    creates_next_phases: list[str] = Field(default_factory=list)
    post_phase_hook: Optional[str] = None

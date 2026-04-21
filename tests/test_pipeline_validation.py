"""Tests for pipeline validation."""

from conductor.models.phases import PhaseDefinition, StepDefinition, QualityGateDefinition
from conductor.pipeline.builder import build_pipeline
from conductor.pipeline.validator import validate_pipeline


def test_full_pipeline_is_valid():
    """The built-in full pipeline passes validation."""
    phases = build_pipeline("full")
    errors = validate_pipeline(phases)
    assert errors == [], f"Full pipeline has errors: {errors}"


def test_minimal_pipeline_is_valid():
    """The built-in minimal pipeline passes validation."""
    phases = build_pipeline("minimal")
    errors = validate_pipeline(phases)
    assert errors == [], f"Minimal pipeline has errors: {errors}"


def test_detects_duplicate_step_ids():
    """Duplicate step IDs are caught."""
    phases = [
        PhaseDefinition(
            phase_id="p1", display_name="P1",
            steps=[
                StepDefinition(step_id="dup", display_name="A", agent_name="a"),
                StepDefinition(step_id="dup", display_name="B", agent_name="b"),
            ],
            quality_gate=QualityGateDefinition(),
        ),
    ]
    errors = validate_pipeline(phases)
    assert any("Duplicate step ID" in e for e in errors)


def test_detects_invalid_depends_on():
    """References to non-existent steps are caught."""
    phases = [
        PhaseDefinition(
            phase_id="p1", display_name="P1",
            steps=[
                StepDefinition(
                    step_id="s1", display_name="S1", agent_name="a",
                    depends_on=["nonexistent_step"],
                ),
            ],
            quality_gate=QualityGateDefinition(),
        ),
    ]
    errors = validate_pipeline(phases)
    assert any("non-existent step" in e for e in errors)


def test_detects_cycle():
    """Dependency cycles within a phase are caught."""
    phases = [
        PhaseDefinition(
            phase_id="p1", display_name="P1",
            steps=[
                StepDefinition(
                    step_id="a", display_name="A", agent_name="x",
                    depends_on=["b"],
                ),
                StepDefinition(
                    step_id="b", display_name="B", agent_name="x",
                    depends_on=["a"],
                ),
            ],
            quality_gate=QualityGateDefinition(),
        ),
    ]
    errors = validate_pipeline(phases)
    assert any("cycle" in e.lower() for e in errors)


def test_detects_invalid_phase_depends_on():
    """References to non-existent phases in depends_on are caught."""
    phases = [
        PhaseDefinition(
            phase_id="p1", display_name="P1",
            depends_on=["nonexistent_phase"],
            steps=[StepDefinition(step_id="s1", display_name="S1", agent_name="a")],
            quality_gate=QualityGateDefinition(),
        ),
    ]
    errors = validate_pipeline(phases)
    assert any("non-existent" in e and "phase" in e for e in errors)

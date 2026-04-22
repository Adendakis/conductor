"""YAML pipeline loader — loads pipeline definition from YAML file."""

from pathlib import Path
from typing import Any

from conductor.models.phases import (
    DeliverableSpec,
    PhaseDefinition,
    QualityGateDefinition,
    StepDefinition,
)


def load_pipeline_yaml(yaml_path: Path) -> list[PhaseDefinition]:
    """Parse a pipeline.yaml file into a list of PhaseDefinitions.

    Requires PyYAML (optional dependency).
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required to load pipeline YAML files. "
            "Install with: pip install pyyaml"
        )

    content = yaml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    pipeline_data = data.get("pipeline", data)
    phases_data = pipeline_data.get("phases", [])

    phases: list[PhaseDefinition] = []
    for phase_dict in phases_data:
        phase = _parse_phase(phase_dict)
        phases.append(phase)

    return phases


def _parse_phase(data: dict[str, Any]) -> PhaseDefinition:
    """Parse a single phase from YAML dict."""
    steps = [_parse_step(s) for s in data.get("steps", [])]

    quality_gate_data = data.get("quality_gate", {})
    quality_gate = QualityGateDefinition(
        required_deliverables=quality_gate_data.get("required_deliverables", []),
        require_reviewer_approval=quality_gate_data.get("require_reviewer_approval", True),
        custom_validators=quality_gate_data.get("custom_validators", []),
    )

    return PhaseDefinition(
        phase_id=data.get("id", ""),
        display_name=data.get("name", ""),
        depends_on=data.get("depends_on", []),
        steps=steps,
        quality_gate=quality_gate,
        execution_scope=data.get("scope", "global"),
        creates_next_phases=data.get("creates_next_phases", []),
        post_phase_hook=data.get("post_phase_hook"),
    )


def _parse_step(data: dict[str, Any]) -> StepDefinition:
    """Parse a single step from YAML dict."""
    deliverables = []
    for d in data.get("deliverables", []):
        if isinstance(d, str):
            deliverables.append(DeliverableSpec(name=d, output_path=d))
        elif isinstance(d, dict):
            deliverables.append(DeliverableSpec(
                name=d.get("name", ""),
                output_path=d.get("path", d.get("output_path", "")),
                file_type=d.get("type", "markdown"),
                required=d.get("required", True),
                min_size_bytes=d.get("min_size", 100),
                per_workpackage=d.get("per_workpackage", False),
            ))

    return StepDefinition(
        step_id=data.get("id", ""),
        display_name=data.get("name", ""),
        description=data.get("description", ""),
        agent_name=data.get("agent", ""),
        prompt_file=data.get("prompt", ""),
        expected_deliverables=deliverables,
        input_dependencies=data.get("input_dependencies", []),
        depends_on=data.get("depends_on", []),
        is_reviewer=data.get("type") == "reviewer_step",
        reviewer_for=data.get("reviewer_for"),
        rework_target=data.get("rework_target"),
        max_review_iterations=data.get("max_iterations", 3),
        optional=data.get("optional", False),
        workpackage_type=data.get("workpackage_type"),
        hitl_after=data.get("hitl_after", True),
        auto_approve_on_validation=data.get("auto_approve_on_validation", False),
    )

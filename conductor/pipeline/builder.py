"""Pipeline builder — defines phases and steps in Python.

For real projects, define your pipeline in pipeline.yaml and load it
with the YAML loader. The builder provides only a minimal demo pipeline
for testing and scaffolding.
"""

from pathlib import Path

from conductor.models.phases import (
    DeliverableSpec,
    PhaseDefinition,
    QualityGateDefinition,
    StepDefinition,
)


def build_pipeline(mode: str = "minimal") -> list[PhaseDefinition]:
    """Build pipeline definition for the given mode.

    Modes:
    - "minimal": Single-phase demo pipeline (for testing)
    - "yaml:/path/to/pipeline.yaml": Load from YAML file

    For production use, define your pipeline in pipeline.yaml.
    """
    if mode == "minimal":
        return _build_minimal()
    if mode.startswith("yaml:"):
        from conductor.pipeline.loader import load_pipeline_yaml
        return load_pipeline_yaml(Path(mode[5:]))
    # Backward compat: treat "full" as minimal with a warning
    if mode == "full":
        import logging
        logging.getLogger(__name__).warning(
            "Pipeline mode 'full' is deprecated. "
            "Define your pipeline in pipeline.yaml instead. "
            "Falling back to 'minimal'."
        )
        return _build_minimal()
    raise ValueError(
        f"Unknown pipeline mode: '{mode}'. "
        f"Use 'minimal' or 'yaml:/path/to/pipeline.yaml'."
    )


def _build_minimal() -> list[PhaseDefinition]:
    """Minimal demo pipeline: single phase with one step."""
    return [
        PhaseDefinition(
            phase_id="phase_1",
            display_name="Phase 1",
            execution_scope="global",
            steps=[
                StepDefinition(
                    step_id="step_1",
                    display_name="Example Step",
                    agent_name="example_agent",
                    prompt_file="agents/example/prompts/task.md",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Example Output",
                            output_path="output/example_output.md",
                            file_type="markdown",
                        ),
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["Example Output"],
            ),
        ),
    ]

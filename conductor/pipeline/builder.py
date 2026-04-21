"""Pipeline builder — defines phases and steps in Python."""

from conductor.models.phases import (
    DeliverableSpec,
    PhaseDefinition,
    QualityGateDefinition,
    StepDefinition,
)


def build_pipeline(mode: str = "full") -> list[PhaseDefinition]:
    """Build pipeline definition for the given mode.

    Modes:
    - "full": Complete migration pipeline (Phases 1-6)
    - "minimal": Phase 1 only (for testing)
    """
    if mode == "minimal":
        return _build_minimal()
    return _build_full()


def _build_minimal() -> list[PhaseDefinition]:
    """Minimal pipeline: Phase 1 only."""
    return [
        PhaseDefinition(
            phase_id="phase_1",
            display_name="Legacy Analysis",
            execution_scope="global",
            creates_next_phases=["phase_2"],
            steps=[
                StepDefinition(
                    step_id="step_1_1_db_analysis",
                    display_name="Phase 1.1: Database Analysis",
                    agent_name="analysis_specialist_database",
                    prompt_file="prompts/01_analysis/Database/01_analysis.md",
                    hitl_after=False,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="DB Analysis Report",
                            output_path="output/analysis/database/reports/DB_Source_Analysis_Report.md",
                            file_type="markdown",
                        ),
                        DeliverableSpec(
                            name="SQLite DDL",
                            output_path="output/analysis/database/gen_src_db/sqlite_ddl.sql",
                            file_type="sql",
                        ),
                    ],
                ),
                StepDefinition(
                    step_id="step_1_2_source_analysis",
                    display_name="Phase 1.2: Source Code Analysis",
                    agent_name="analysis_specialist_legacy_code",
                    prompt_file="prompts/01_analysis/Sourcecode/01_generate_cobol_analysis_tool.md",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Source Analysis Report",
                            output_path="output/analysis/source_code/reports/Cobol_Source_Analysis_Report.md",
                            file_type="markdown",
                        ),
                        DeliverableSpec(
                            name="Business Flows",
                            output_path="output/analysis/source_code/flows/Business_Flows.json",
                            file_type="json",
                        ),
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["DB Analysis Report", "Source Analysis Report"],
            ),
        ),
    ]


def _build_full() -> list[PhaseDefinition]:
    """Full migration pipeline: Phases 1-6."""
    return [
        # Phase 1: Analysis
        PhaseDefinition(
            phase_id="phase_1",
            display_name="Legacy Analysis",
            execution_scope="global",
            creates_next_phases=["phase_2"],
            steps=[
                StepDefinition(
                    step_id="step_1_1_db_analysis",
                    display_name="Phase 1.1: Database Analysis",
                    agent_name="analysis_specialist_database",
                    prompt_file="prompts/01_analysis/Database/01_analysis.md",
                    hitl_after=False,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="DB Analysis Report",
                            output_path="output/analysis/database/reports/DB_Source_Analysis_Report.md",
                            file_type="markdown",
                        ),
                    ],
                ),
                StepDefinition(
                    step_id="step_1_2_source_analysis",
                    display_name="Phase 1.2: Source Code Analysis",
                    agent_name="analysis_specialist_legacy_code",
                    prompt_file="prompts/01_analysis/Sourcecode/01_generate_cobol_analysis_tool.md",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Source Analysis Report",
                            output_path="output/analysis/source_code/reports/Cobol_Source_Analysis_Report.md",
                            file_type="markdown",
                        ),
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["DB Analysis Report", "Source Analysis Report"],
            ),
        ),
        # Phase 2: Planning
        PhaseDefinition(
            phase_id="phase_2",
            display_name="Migration Planning",
            depends_on=["phase_1"],
            execution_scope="global",
            creates_next_phases=["phase_2_5"],
            steps=[
                StepDefinition(
                    step_id="step_2_1_workpackage_definition",
                    display_name="Phase 2.1: Workpackage Definition",
                    agent_name="planning_specialist_workpackage",
                    prompt_file="prompts/02_workpackage/01_generate_workpackage_definition_tool.md",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Workpackage Planning",
                            output_path="output/analysis/workpackages/Workpackage_Planning.json",
                            file_type="json",
                        ),
                    ],
                    input_dependencies=[
                        "output/analysis/source_code/reports/Cobol_Source_Analysis_Report.md",
                        "output/analysis/database/reports/DB_Source_Analysis_Report.md",
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["Workpackage Planning"],
                custom_validators=["validate_dag_no_cycles"],
            ),
        ),
        # Phase 2.5: Pod Partitioning
        PhaseDefinition(
            phase_id="phase_2_5",
            display_name="Pod Partitioning",
            depends_on=["phase_2"],
            execution_scope="global",
            creates_next_phases=["phase_3"],
            steps=[
                StepDefinition(
                    step_id="step_2_5_pod_partitioning",
                    display_name="Phase 2.5: Pod Partitioning",
                    agent_name="planning_specialist_workpackage",
                    prompt_file="prompts/02_workpackage/02_pod_partitioning.md",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Pod Assignment",
                            output_path="output/analysis/workpackages/Pod_Assignment.json",
                            file_type="json",
                        ),
                    ],
                    input_dependencies=[
                        "output/analysis/workpackages/Workpackage_Planning.json",
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["Pod Assignment"],
                custom_validators=["validate_pod_assignment_completeness"],
            ),
        ),
        # Phase 3: Business Specification (per workpackage)
        PhaseDefinition(
            phase_id="phase_3",
            display_name="Business Specification",
            depends_on=["phase_2_5"],
            execution_scope="per_workpackage",
            creates_next_phases=["phase_5"],
            steps=[
                StepDefinition(
                    step_id="step_3_1_logic_extraction",
                    display_name="Phase 3.1: Business Logic Extraction",
                    agent_name="business_specialist_logic_extraction",
                    prompt_file="prompts/03-business_extraction/phase_3.1_business_logic_extraction.md",
                    hitl_after=False,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Logic Extraction",
                            output_path="output/specifications/business/traceability/{wp_id}-chapter6.md",
                            file_type="markdown",
                            per_workpackage=True,
                        ),
                    ],
                ),
                StepDefinition(
                    step_id="step_3_1_1_logic_review",
                    display_name="Phase 3.1.1: Logic Extraction Review",
                    agent_name="business_reviewer_logic_extraction",
                    prompt_file="prompts/03-business_extraction/phase_3.1.1_business_logic_extraction_review.md",
                    depends_on=["step_3_1_logic_extraction"],
                    is_reviewer=True,
                    reviewer_for="step_3_1_logic_extraction",
                    rework_target="step_3_1_logic_extraction",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Logic Review",
                            output_path="output/specifications/business/traceability/review/{wp_id}-logic-extraction-review.md",
                            file_type="markdown",
                            per_workpackage=True,
                        ),
                    ],
                    input_dependencies=[
                        "output/specifications/business/traceability/{wp_id}-chapter6.md",
                    ],
                ),
                StepDefinition(
                    step_id="step_3_2_specification",
                    display_name="Phase 3.2: Business Specification",
                    agent_name="business_specialist_requirements",
                    prompt_file="prompts/03-business_extraction/phase_3.2_business_specification_generation.md",
                    depends_on=["step_3_1_1_logic_review"],
                    hitl_after=False,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Business Spec",
                            output_path="output/specifications/business/specs/{wp_id}-specification.md",
                            file_type="markdown",
                            per_workpackage=True,
                        ),
                    ],
                    input_dependencies=[
                        "output/specifications/business/traceability/{wp_id}-chapter6.md",
                    ],
                ),
                StepDefinition(
                    step_id="step_3_2_1_spec_review",
                    display_name="Phase 3.2.1: Business Spec Review",
                    agent_name="business_reviewer_requirements",
                    prompt_file="prompts/03-business_extraction/phase_3.2.1_business_specification_review.md",
                    depends_on=["step_3_2_specification"],
                    is_reviewer=True,
                    reviewer_for="step_3_2_specification",
                    rework_target="step_3_2_specification",
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Spec Review",
                            output_path="output/specifications/business/specs/review/{wp_id}-review.md",
                            file_type="markdown",
                            per_workpackage=True,
                        ),
                    ],
                    input_dependencies=[
                        "output/specifications/business/specs/{wp_id}-specification.md",
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["Business Spec", "Spec Review"],
                require_reviewer_approval=True,
            ),
        ),
        # Phase 5: Code Generation (per workpackage)
        PhaseDefinition(
            phase_id="phase_5",
            display_name="Code Generation",
            depends_on=["phase_3"],
            execution_scope="per_workpackage",
            steps=[
                StepDefinition(
                    step_id="step_5_0_0_tech_spec",
                    display_name="Phase 5.0.0: Technical Implementation Guide",
                    agent_name="tech_spec_extraction_specialist",
                    prompt_file="prompts/05_code_generation/phase_5.0.0_tech_spec_creation.md",
                    hitl_after=False,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Tech Spec",
                            output_path="output/specifications/technical/{wp_id}-tech-implementation-guide.md",
                            file_type="markdown",
                            per_workpackage=True,
                        ),
                    ],
                    input_dependencies=[
                        "output/specifications/business/specs/{wp_id}-specification.md",
                    ],
                ),
                StepDefinition(
                    step_id="step_5_0_1_tech_review",
                    display_name="Phase 5.0.1: Tech Spec Review",
                    agent_name="tech_spec_review_specialist",
                    prompt_file="prompts/05_code_generation/phase_5.0.1_tech_spec_review.md",
                    depends_on=["step_5_0_0_tech_spec"],
                    is_reviewer=True,
                    reviewer_for="step_5_0_0_tech_spec",
                    rework_target="step_5_0_0_tech_spec",
                    hitl_after=True,
                ),
                StepDefinition(
                    step_id="step_5_2_backend_code",
                    display_name="Phase 5.2: Backend Code Generation",
                    agent_name="development_specialist_code_generation",
                    prompt_file="prompts/05_code_generation/phase_5.2_backend_code_generation.md",
                    depends_on=["step_5_0_1_tech_review"],
                    hitl_after=True,
                    expected_deliverables=[
                        DeliverableSpec(
                            name="Generated Code",
                            output_path="output/gen_src/{wp_id}/",
                            file_type="directory",
                            per_workpackage=True,
                        ),
                    ],
                    input_dependencies=[
                        "output/specifications/technical/{wp_id}-tech-implementation-guide.md",
                    ],
                ),
            ],
            quality_gate=QualityGateDefinition(
                required_deliverables=["Tech Spec", "Generated Code"],
                require_reviewer_approval=True,
            ),
        ),
        # Phase 6: Integration Testing (global)
        PhaseDefinition(
            phase_id="phase_6",
            display_name="Integration Testing",
            depends_on=["phase_5"],
            execution_scope="global",
            steps=[
                StepDefinition(
                    step_id="step_6_integration_test",
                    display_name="Phase 6: Integration Testing",
                    agent_name="development_team_supervisor",
                    prompt_file="prompts/06_integration/integration_testing.md",
                    hitl_after=True,
                ),
            ],
            quality_gate=QualityGateDefinition(),
        ),
    ]

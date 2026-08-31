from io import BytesIO

from pypdf import PdfReader

from app.core.exporter import render_docx, render_pdf
from app.core.graph_engine import compute_sequence
from app.core.plan_assembler import assemble_plan
from app.core.review_rules_engine import run_rules
from app.llm.schemas import ComponentPlanLLMOutput, CutoverReviewOutput, RollbackPlanOutput
from app.schemas.architecture import ArchitectureModel, Component, Dependency
from app.schemas.migration_plan import EfficiencyBreakdown, EffortBreakdown, ValidationCheck


def _sample_model() -> ArchitectureModel:
    return ArchitectureModel(
        components=[
            Component(id="web", name="Web App", workload_type="web_service"),
            Component(id="api", name="API", workload_type="api_service"),
            Component(id="db", name="Postgres", workload_type="database"),
        ],
        dependencies=[
            Dependency(id="d1", source_id="web", target_id="api", kind="sync_call"),
            Dependency(id="d2", source_id="api", target_id="db", kind="data_read"),
        ],
    )


def _component_outputs(model: ArchitectureModel) -> list[ComponentPlanLLMOutput]:
    return [
        ComponentPlanLLMOutput(
            component_id=c.id,
            target_description=f"{c.name} on target platform",
            disposition="replatform",
            target_cloud_provider="aws",
            target_service_category="compute_vm",
            steps=[f"migrate {c.id}"],
            validation_checks=[ValidationCheck(description="smoke test", check_type="smoke_test")],
            rollback_notes="revert to source",
            estimated_effort="3-5 days",
            effort_breakdown=EffortBreakdown(
                total="3-5 days",
                implementation="2 days to configure target service and migrate component settings",
                validation="1 day for smoke and dependency validation",
                cutover="0.5 day for release support",
                rollback="0.5 day for rollback rehearsal",
                confidence="medium",
                rationale=f"{c.name} effort accounts for dependency checks and rollback readiness.",
            ),
            efficiency_breakdown=EfficiencyBreakdown(
                expected_benefits=["reduced manual patching", "more predictable scaling"],
                tradeoffs=["managed-service cost may increase"],
                primary_efficiency_gain="Reduced operational maintenance for the component.",
                confidence="medium",
                rationale=f"{c.name} benefits from managed target operations while retaining validation overhead.",
            ),
            dependencies_considered=[d.target_id for d in model.dependencies if d.source_id == c.id],
        )
        for c in model.components
    ]


def test_full_pipeline_produces_a_plan_with_no_rule_violations():
    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model,
        waves,
        _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(
            approach="phased",
            rationale="Phased cutover fits the dependency waves and keeps rollback bounded per wave.",
            steps=["cut over wave by wave"],
            go_no_go_criteria=["smoke tests green"],
            communication_plan="status page",
        ),
        rollback=RollbackPlanOutput(
            approach="revert",
            rationale="Reverse-wave rollback preserves the source fallback while stateful checks are reconciled.",
            triggers=["error rate spike"],
            steps=["roll back"],
        ),
    )

    assert len(plan.component_mappings) == 3
    assert len(plan.component_plans) == 3
    assert len(plan.roadmap_items) == 3
    assert plan.validation_summary is not None
    assert plan.cutover_strategy is not None
    assert plan.cutover_strategy.rationale == (
        "Phased cutover fits the dependency waves and keeps rollback bounded per wave."
    )
    assert plan.rollback_strategy is not None
    assert plan.rollback_strategy.rationale == (
        "Reverse-wave rollback preserves the source fallback while stateful checks are reconciled."
    )

    findings = run_rules(model, plan)
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], errors


def test_cross_wave_coexistence_is_attached_to_waves_and_satisfies_rule_007():
    """assemble_plan must write compute_cross_wave_dependencies()'s output into
    Wave.coexistence_groups — that's the only place RULE-007 looks. Without this,
    RULE-007 fires on every cross-wave dependency forever, regardless of what the
    LLM plans, since nothing else ever populates that field."""

    model = _sample_model()  # web -> api -> db, each in its own wave
    waves = compute_sequence(model)
    plan = assemble_plan(
        model,
        waves,
        _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )

    documented_pairs = {frozenset(g.component_ids) for w in plan.waves for g in w.coexistence_groups}
    assert frozenset({"web", "api"}) in documented_pairs
    assert frozenset({"api", "db"}) in documented_pairs

    findings = run_rules(model, plan)
    assert not any(f.rule_id == "RULE-007" for f in findings), findings


def test_pdf_export_contains_all_ten_deliverable_sections():
    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    plan.cutover_strategy.rationale = "Phased strategy follows the computed wave order."
    plan.rollback_strategy.rationale = "Rollback mirrors cutover to keep recovery understandable."
    pdf_bytes = render_pdf(model, plan, context=None)
    assert pdf_bytes[:4] == b"%PDF"

    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_bytes)).pages)
    for heading in [
        "Current Architecture", "Target Architecture", "Component Mapping",
        "Component Migration Approach", "Migration Sequence", "Risks & Assumptions",
        "Validation Approach", "Cutover Strategy", "Rollback Strategy", "Migration Roadmap",
    ]:
        assert heading in text, f"missing section: {heading}"
    assert "Implementation: 2 days" in text
    assert "Confidence: medium" in text
    assert "Primary efficiency gain" in text
    assert "Reduced operational maintenance" in text
    assert "Why this strategy: Phased strategy follows" in text
    assert "Why this strategy: Rollback mirrors cutover" in text


def test_roadmap_uses_discovered_owner_team_instead_of_tbd_placeholder():
    """A component with a known owner_team should show that team on the roadmap,
    not the generic 'TBD' placeholder — the roadmap should be immediately
    actionable when discovery already captured who owns each component."""

    model = _sample_model()
    model.get_component("api").owner_team = "payments-team"
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )

    api_item = next(item for item in plan.roadmap_items if item.component_id == "api")
    assert api_item.owner_placeholder == "payments-team"
    assert api_item.effort_breakdown is not None
    assert api_item.effort_breakdown.total == "3-5 days"
    assert api_item.efficiency_breakdown is not None
    assert "maintenance" in api_item.efficiency_breakdown.primary_efficiency_gain

    web_item = next(item for item in plan.roadmap_items if item.component_id == "web")
    assert web_item.owner_placeholder == "TBD"


def test_pdf_roadmap_includes_owner_placeholder():
    """The PRD's data model names 'owner placeholder' as part of RoadmapItem
    (Section: Data Model Overview); the export must actually surface it, not just
    carry it in the schema unused."""

    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    pdf_bytes = render_pdf(model, plan, context=None)
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_bytes)).pages)

    assert "Owner" in text
    assert all(item.owner_placeholder in text for item in plan.roadmap_items)


def test_pdf_tables_preserve_long_cell_context():
    """Long target descriptions and roadmap summaries must wrap inside PDF tables
    instead of being clipped off the page. Text extraction is a practical proxy for
    'the full context made it into the PDF content stream'."""

    model = _sample_model()
    waves = compute_sequence(model)
    outputs = _component_outputs(model)
    outputs[0].target_description = (
        "Amazon CloudFront with origin access control, private S3 document access, "
        "Cognito-authenticated frontend behavior preserved, and rollback validation "
        "covering cache invalidation and signed download links."
    )
    outputs[0].steps = [
        "Prepare a production build, upload static assets to the private S3 origin, "
        "configure CloudFront origin access control, validate Cognito login, and "
        "confirm presigned document downloads remain private."
    ]
    plan = assemble_plan(
        model, waves, outputs,
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )

    pdf_bytes = render_pdf(model, plan, context=None)
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_bytes)).pages)

    assert "private S3 document access" in text
    assert "presigned document downloads remain private" in text


def test_docx_export_produces_nonempty_bytes():
    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    plan.cutover_strategy.rationale = "Phased strategy follows the computed wave order."
    plan.rollback_strategy.rationale = "Rollback mirrors cutover to keep recovery understandable."
    docx_bytes = render_docx(model, plan, context=None)
    assert docx_bytes[:2] == b"PK"  # docx is a zip container

    from docx import Document

    document = Document(BytesIO(docx_bytes))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Implementation: 2 days" in text
    assert "Rollback readiness: 0.5 day" in text
    assert "Primary efficiency gain" in text
    assert "managed-service cost may increase" in text
    assert "Why this strategy: Phased strategy follows" in text
    assert "Why this strategy: Rollback mirrors cutover" in text


def test_cost_estimate_section_appears_in_both_export_formats_when_present():
    from app.schemas.cost import CloudProvider, CostEstimate, CostSummary, ServiceCategory

    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    plan.cost_summary = CostSummary(
        estimates=[
            CostEstimate(
                component_id="web",
                provider=CloudProvider.AWS,
                service_category=ServiceCategory.COMPUTE_VM,
                sku_description="AWS EC2 t3.medium",
                sizing_assumption="1x small instance, 24/7",
                monthly_usd=30.37,
                pricing_source="AWS Price List API (snapshot)",
                priced_at="2026-01-01",
            )
        ],
        total_monthly_usd=30.37,
        unestimated_component_ids=[],
    )

    pdf_text = "".join(page.extract_text() for page in PdfReader(BytesIO(render_pdf(model, plan, context=None))).pages)
    assert "Cost Estimate" in pdf_text
    assert "30.37" in pdf_text
    assert "not a quote" in pdf_text  # the methodology_note's honesty caveat must survive into the export

    docx_bytes = render_docx(model, plan, context=None)
    assert docx_bytes[:2] == b"PK"


def test_cost_estimate_section_omitted_when_absent():
    """No cost_summary (e.g. a plan from before this feature, or GCP-only with no
    key configured) must not blow up the export — the section is simply absent."""

    model = _sample_model()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model, waves, _component_outputs(model),
        target_architecture_description="Cloud-native target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    assert plan.cost_summary is None

    pdf_text = "".join(page.extract_text() for page in PdfReader(BytesIO(render_pdf(model, plan, context=None))).pages)
    assert "Cost Estimate" not in pdf_text

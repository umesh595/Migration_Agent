"""Assembles the canonical MigrationPlan from computed waves (GraphEngine) and
per-component LLM outputs. This is the only place a MigrationPlan is constructed —
Exporter never invents content, it only renders what's already here (technique #12)."""

from __future__ import annotations

from app.core.graph_engine import compute_cross_wave_dependencies
from app.llm.schemas import ComponentPlanLLMOutput, CutoverReviewOutput, RollbackPlanOutput
from app.schemas.architecture import ArchitectureModel
from app.schemas.findings import Finding, FindingSeverity, ResolutionStatus
from app.schemas.migration_plan import (
    CoexistenceGroup,
    ComponentMapping,
    ComponentPlan,
    CutoverStrategy,
    MigrationPlan,
    Risk,
    RiskSeverity,
    RoadmapItem,
    RollbackStrategy,
    ValidationCheck,
    ValidationSummary,
    Wave,
)

_SEVERITY_TO_RISK: dict[FindingSeverity, RiskSeverity] = {
    FindingSeverity.INFO: RiskSeverity.LOW,
    FindingSeverity.WARNING: RiskSeverity.MEDIUM,
    FindingSeverity.ERROR: RiskSeverity.HIGH,
}


def _build_validation_summary(component_plans: list[ComponentPlan], cutover: CutoverStrategy | None) -> ValidationSummary:
    check_types = sorted({c.check_type for p in component_plans for c in p.validation_checks})
    cross_checks = [
        ValidationCheck(
            description=f"Aggregate {check_type} results across all components in a wave before proceeding to the next",
            check_type=check_type,
        )
        for check_type in check_types
    ]
    return ValidationSummary(
        overall_strategy=(
            "Each component is validated individually against its own checks (below) before its wave is "
            "considered complete; wave completion is itself a go/no-go input to cutover."
        ),
        cross_component_checks=cross_checks,
        sign_off_gates=list(cutover.go_no_go_criteria) if cutover else [],
    )


def _build_roadmap_items(waves: list[Wave], component_plans: list[ComponentPlan]) -> list[RoadmapItem]:
    plans_by_id = {p.component_id: p for p in component_plans}
    wave_of = {cid: w.index for w in waves for cid in w.component_ids}
    items: list[RoadmapItem] = []

    for wave in waves:
        for component_id in wave.component_ids:
            plan = plans_by_id.get(component_id)
            if plan is None:
                continue
            depends_on_waves = sorted(
                {
                    wave_of[dep_id]
                    for dep_id in plan.dependencies_considered
                    if dep_id in wave_of and wave_of[dep_id] != wave.index
                }
            )
            items.append(
                RoadmapItem(
                    wave_index=wave.index,
                    component_id=component_id,
                    disposition=plan.disposition,
                    summary=plan.steps[0] if plan.steps else f"Migrate {component_id}",
                    estimated_effort=plan.estimated_effort,
                    depends_on_waves=depends_on_waves,
                )
            )
    return items


def _attach_cross_wave_coexistence(waves: list[Wave], groups: list[CoexistenceGroup]) -> list[Wave]:
    """compute_cross_wave_dependencies() finds these groups but scopes each to a
    dependency pair, not a single wave — nothing wrote them into Wave.coexistence_groups,
    which is the only place RULE-007 looks. Without this, RULE-007 fires on every
    cross-wave dependency unconditionally, forever, since the LLM has no field through
    which to ever satisfy it. Attach each group to both endpoints' waves so it's visible
    from either wave and RULE-007 sees it as documented."""

    groups_by_wave_index: dict[int, list[CoexistenceGroup]] = {}
    wave_of = {cid: w.index for w in waves for cid in w.component_ids}
    for group in groups:
        for cid in group.component_ids:
            wave_index = wave_of.get(cid)
            if wave_index is not None:
                groups_by_wave_index.setdefault(wave_index, []).append(group)

    return [
        wave.model_copy(
            update={"coexistence_groups": [*wave.coexistence_groups, *groups_by_wave_index.get(wave.index, [])]}
        )
        for wave in waves
    ]


def assemble_plan(
    model: ArchitectureModel,
    waves: list[Wave],
    component_outputs: list[ComponentPlanLLMOutput],
    target_architecture_description: str,
    cutover: CutoverReviewOutput,
    rollback: RollbackPlanOutput,
) -> MigrationPlan:
    cross_wave_groups = compute_cross_wave_dependencies(model, waves)
    waves = _attach_cross_wave_coexistence(waves, cross_wave_groups)

    component_mappings = [
        ComponentMapping(
            component_id=o.component_id,
            target_description=o.target_description,
            disposition=o.disposition,
        )
        for o in component_outputs
    ]

    wave_of = {cid: w.index for w in waves for cid in w.component_ids}
    component_plans = [
        ComponentPlan(
            component_id=o.component_id,
            disposition=o.disposition,
            wave_index=wave_of.get(o.component_id, -1),
            steps=o.steps,
            validation_checks=o.validation_checks,
            rollback_notes=o.rollback_notes,
            estimated_effort=o.estimated_effort,
            dependencies_considered=o.dependencies_considered,
        )
        for o in component_outputs
    ]

    cutover_strategy = CutoverStrategy(
        approach=cutover.approach,
        steps=cutover.steps,
        go_no_go_criteria=cutover.go_no_go_criteria,
        communication_plan=cutover.communication_plan,
    )
    rollback_strategy = RollbackStrategy(
        approach=rollback.approach,
        triggers=rollback.triggers,
        steps=rollback.steps,
        data_reconciliation_notes=rollback.data_reconciliation_notes,
    )

    cross_wave_risks = [
        Risk(
            id=f"RISK-crosswave-{i}",
            description=group.reason,
            severity=RiskSeverity.MEDIUM,
            mitigation=group.coexistence_strategy,
            related_component_ids=group.component_ids,
            source="graph_engine:cross_wave_dependency",
        )
        for i, group in enumerate(cross_wave_groups, start=1)
    ]

    assumption_risks = [
        Risk(
            id=f"RISK-assumption-{a.id}",
            description=f"Unverified assumption: {a.text}",
            severity=RiskSeverity.LOW,
            mitigation="Confirm with system owner before this component's wave begins.",
            related_component_ids=a.related_component_ids,
            source="model_assumption",
        )
        for a in model.assumptions
        if a.raised_by == "llm"
    ]

    return MigrationPlan(
        target_architecture_description=target_architecture_description,
        component_mappings=component_mappings,
        component_plans=component_plans,
        waves=waves,
        risks=cross_wave_risks + assumption_risks,
        cutover_strategy=cutover_strategy,
        rollback_strategy=rollback_strategy,
        validation_summary=_build_validation_summary(component_plans, cutover_strategy),
        roadmap_items=_build_roadmap_items(waves, component_plans),
    )


def unresolved_findings_to_risks(findings: list[Finding]) -> list[Risk]:
    """'Unresolved findings ship as Risks' (Doc 3 §3.1, end of REVIEW stage) — after
    the refine loop's iteration budget is exhausted, whatever's still open becomes a
    documented risk instead of silently disappearing."""

    return [
        Risk(
            id=f"RISK-finding-{f.id}",
            description=f.message,
            severity=_SEVERITY_TO_RISK[f.severity],
            mitigation="Not resolved within the refine-loop iteration budget; requires manual follow-up before cutover.",
            related_component_ids=f.related_component_ids,
            source=f"unresolved_finding:{f.rule_id or 'llm'}",
        )
        for f in findings
        if f.resolution_status == ResolutionStatus.OPEN
    ]

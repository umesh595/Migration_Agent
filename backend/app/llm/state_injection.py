"""Serializes canonical state into prompt context (technique #11: closed-world
prompting). Per-turn context stays near-constant instead of growing with conversation
length, because we inject the *model*, never the transcript."""

from __future__ import annotations

import json

from app.core.gap_analyzer import Gap
from app.schemas.architecture import ArchitectureModel
from app.schemas.findings import Finding
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import MigrationPlan, Wave


def render_model_for_prompt(model: ArchitectureModel) -> str:
    payload = {
        "components": [
            {
                "id": c.id,
                "name": c.name,
                "workload_type": str(c.workload_type),
                "environment": str(c.environment),
                "technology": c.technology,
                "description": c.description,
            }
            for c in model.components
        ],
        "dependencies": [
            {"source_id": d.source_id, "target_id": d.target_id, "kind": str(d.kind)} for d in model.dependencies
        ],
        "assumptions": [{"id": a.id, "text": a.text} for a in model.assumptions],
        "open_questions": [{"id": q.id, "text": q.text} for q in model.open_questions if not q.resolved],
    }
    return json.dumps(payload, indent=2)


def render_gaps_for_prompt(gaps: list[Gap]) -> str:
    return json.dumps(
        [{"category": str(g.category), "description": g.description, "components": g.related_component_ids} for g in gaps],
        indent=2,
    )


def render_context_for_prompt(context: MigrationContext) -> str:
    return json.dumps(
        {
            "source_environment": str(context.source_environment),
            "target_environment": str(context.target_environment),
            "target_platform": context.target_platform_description,
            "downtime_tolerance": str(context.downtime_tolerance),
            "maintenance_window": context.maintenance_window_description,
            "constraints": context.constraints,
        },
        indent=2,
    )


def render_component_planning_context(
    model: ArchitectureModel,
    component_id: str,
    wave: Wave,
    context: MigrationContext,
    all_waves: list[Wave],
) -> str:
    """Everything the per-component planner is allowed to see. Note that wave_index
    is presented as a FIXED FACT — the planner has no mechanism to change it
    (technique #6)."""

    component = model.get_component(component_id)
    if component is None:
        raise ValueError(f"component '{component_id}' not in model")

    wave_of = {cid: w.index for w in all_waves for cid in w.component_ids}
    depends_on = [
        {"id": d.target_id, "kind": str(d.kind), "migrates_in_wave": wave_of.get(d.target_id)}
        for d in model.dependencies
        if d.source_id == component_id
    ]
    depended_on_by = [
        {"id": d.source_id, "kind": str(d.kind), "migrates_in_wave": wave_of.get(d.source_id)}
        for d in model.dependencies
        if d.target_id == component_id
    ]

    payload = {
        "component": {
            "id": component.id,
            "name": component.name,
            "workload_type": str(component.workload_type),
            "environment": str(component.environment),
            "technology": component.technology,
            "description": component.description,
            "criticality": component.criticality,
        },
        "ASSIGNED_WAVE_INDEX_FIXED": wave.index,
        "wave_rationale": wave.rationale,
        "same_wave_components": [c for c in wave.component_ids if c != component_id],
        "depends_on": depends_on,
        "depended_on_by": depended_on_by,
        "migration_context": json.loads(render_context_for_prompt(context)),
    }
    return json.dumps(payload, indent=2)


def render_review_for_judge(
    rule_findings: list[Finding], llm_findings: list[Finding], context: MigrationContext
) -> str:
    """Everything the judge sees: the rule findings it must NOT reward the critic for
    repeating, the critic's own findings to be scored, and the context the critique
    should have respected."""

    payload = {
        "migration_context": json.loads(render_context_for_prompt(context)),
        "rule_findings_already_covered": [
            {"rule_id": f.rule_id, "message": f.message} for f in rule_findings
        ],
        "critic_findings_to_score": [
            {"severity": str(f.severity), "message": f.message, "related_component_ids": f.related_component_ids}
            for f in llm_findings
        ],
    }
    return json.dumps(payload, indent=2)


def render_plan_for_review(model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext) -> str:
    payload = {
        "migration_context": json.loads(render_context_for_prompt(context)),
        "target_architecture": plan.target_architecture_description,
        "waves": [{"index": w.index, "components": w.component_ids} for w in plan.waves],
        "component_plans": [
            {
                "component_id": p.component_id,
                "workload_type": str(c.workload_type) if (c := model.get_component(p.component_id)) else None,
                "disposition": str(p.disposition),
                "wave": p.wave_index,
                "steps": p.steps,
                "validation_checks": [{"type": v.check_type, "description": v.description} for v in p.validation_checks],
                "rollback_notes": p.rollback_notes,
                "estimated_effort": p.estimated_effort,
            }
            for p in plan.component_plans
        ],
        "cutover": plan.cutover_strategy.model_dump(mode="json") if plan.cutover_strategy else None,
        "rollback": plan.rollback_strategy.model_dump(mode="json") if plan.rollback_strategy else None,
        "existing_risks": [{"description": r.description, "severity": str(r.severity)} for r in plan.risks],
    }
    return json.dumps(payload, indent=2)

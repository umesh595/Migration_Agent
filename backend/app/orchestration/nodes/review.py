"""Review subgraph (technique #8). Deterministic rules run first at zero token cost;
the LLM critic only sees what rules can't check, and is explicitly told not to
re-report mechanical findings (see SEMANTIC_REVIEW prompt).

Refine loop: findings -> targeted re-plan of affected components -> re-run rules.
Bounded by max_refine_iterations; whatever is still open when the budget runs out
ships as documented Risks rather than silently disappearing (Doc 3 §3.1)."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.core.plan_assembler import unresolved_findings_to_risks
from app.core.review_rules_engine import run_rules
from app.llm.base import ModelTier, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.prompts.registry import get_prompt
from app.llm.schemas import ComponentPlanLLMOutput, SemanticReviewJudgeOutput, SemanticReviewOutput
from app.llm.state_injection import (
    render_component_planning_context,
    render_plan_for_review,
    render_review_for_judge,
)
from app.orchestration.state import GraphState, Stage
from app.schemas.findings import Finding, FindingSeverity, FindingSource, ResolutionStatus
from app.schemas.migration_plan import PlanStatus
from app.schemas.review_quality import ReviewQualityScore

logger = logging.getLogger(__name__)


def rules_review_node(state: GraphState) -> dict:
    """Deterministic: RULE-001..007. Zero tokens."""

    plan = state.get("plan")
    if plan is None:
        return {"findings": []}

    rule_findings = run_rules(state["model"], plan)
    # Preserve any LLM findings already recorded this cycle; rules are recomputed fresh.
    existing_llm = [f for f in state.get("findings", []) if f.source == FindingSource.LLM]
    return {"findings": rule_findings + existing_llm}


async def llm_review_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: semantic critique layered on top of the rules pass."""

    plan = state.get("plan")
    context = state.get("migration_context")
    if plan is None or context is None:
        return {}

    try:
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=get_prompt("semantic_review").system,
            user_prompt=render_plan_for_review(state["model"], plan, context),
            response_model=SemanticReviewOutput,
            meter=meter,
            node_name="review.semantic",
        )
    except StructuredOutputError as exc:
        # Rules findings still stand; the semantic layer is additive, so a failure
        # here degrades quality but must not block the review stage.
        logger.warning("semantic review failed, continuing with rule findings only: %s", exc)
        return {}

    def coerce_severity(value: str) -> FindingSeverity:
        try:
            return FindingSeverity(value.strip().lower())
        except ValueError:
            return FindingSeverity.WARNING

    llm_findings = [
        Finding(
            id=f"LLM-{state.get('refine_iteration', 0)}-{i}",
            source=FindingSource.LLM,
            severity=coerce_severity(f.severity),
            message=f.message,
            related_component_ids=[
                cid for cid in f.related_component_ids if cid in state["model"].component_ids()
            ],
        )
        for i, f in enumerate(response.parsed.findings)
    ]

    rule_findings = [f for f in state.get("findings", []) if f.source == FindingSource.RULE]
    return {"findings": rule_findings + llm_findings}


async def judge_review_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """Independent judge pass over the semantic critic's own findings (PRD Decision
    Q7, accelerated from v2 — see DECISIONS.md). Non-blocking: a low score is
    recorded for audit/observability but never gates the refine loop or Gate 2 —
    an unproven judge is not a safe thing to let block a real approval, only to
    inform one."""

    context = state.get("migration_context")
    if context is None:
        return {}

    findings = state.get("findings", [])
    rule_findings = [f for f in findings if f.source == FindingSource.RULE]
    llm_findings = [f for f in findings if f.source == FindingSource.LLM]

    try:
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=get_prompt("semantic_review_judge").system,
            user_prompt=render_review_for_judge(rule_findings, llm_findings, context),
            response_model=SemanticReviewJudgeOutput,
            meter=meter,
            node_name="review.judge",
        )
    except StructuredOutputError as exc:
        # The judge itself failing must not block review — it's an observability
        # signal, not a gate. Silently skip this iteration's score.
        logger.warning("review-quality judge failed, skipping this iteration's score: %s", exc)
        return {}

    parsed = response.parsed
    score = ReviewQualityScore(
        iteration=state.get("refine_iteration", 0),
        evaluated_finding_count=len(llm_findings),
        relevance_score=parsed.relevance_score,
        specificity_score=parsed.specificity_score,
        actionability_score=parsed.actionability_score,
        context_awareness_score=parsed.context_awareness_score,
        overall_score=parsed.overall_score,
        rationale=parsed.rationale,
        flagged_issues=parsed.flagged_issues,
    )
    history = [*state.get("review_quality_history", []), score]
    return {"review_quality_history": history}


async def refine_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """Re-plans only the components named in open findings, then lets the rules
    re-run. Wave assignment is NOT recomputed — sequencing came from the graph, and
    a review finding is not authority to change migration order."""

    plan = state["plan"]
    context = state["migration_context"]
    model = state["model"]
    iteration = state.get("refine_iteration", 0)

    open_findings = [f for f in state.get("findings", []) if f.resolution_status == ResolutionStatus.OPEN]
    affected_ids = {cid for f in open_findings for cid in f.related_component_ids}
    if not affected_ids:
        return {"refine_iteration": iteration + 1}

    waves_by_index = {w.index: w for w in plan.waves}
    findings_by_component: dict[str, list[str]] = {}
    for finding in open_findings:
        for cid in finding.related_component_ids:
            findings_by_component.setdefault(cid, []).append(f"[{finding.severity}] {finding.message}")

    updated_plans = {}
    for component_id in affected_ids:
        component_plan = plan.component_plan_for(component_id)
        if component_plan is None:
            continue
        wave = waves_by_index.get(component_plan.wave_index)
        if wave is None:
            continue

        base_context = render_component_planning_context(model, component_id, wave, context, plan.waves)
        issues = "\n".join(f"- {msg}" for msg in findings_by_component.get(component_id, []))
        user_prompt = (
            f"{base_context}\n\n"
            f"YOUR PREVIOUS PLAN FOR THIS COMPONENT:\n{component_plan.model_dump_json(indent=2)}\n\n"
            f"REVIEW FINDINGS TO ADDRESS:\n{issues}\n\n"
            "Produce a corrected plan for this component that resolves these findings. "
            "The assigned wave index is still fixed and must not change."
        )

        try:
            response = await gateway.complete(
                tier=ModelTier.STRONG,
                system_prompt=get_prompt("plan_component").system,
                user_prompt=user_prompt,
                response_model=ComponentPlanLLMOutput,
                meter=meter,
                node_name=f"review.refine.{component_id}",
            )
        except StructuredOutputError as exc:
            logger.warning("refine failed for %s, keeping original plan: %s", component_id, exc)
            continue

        updated_plans[component_id] = response.parsed

    refreshed = plan.model_copy(deep=True)
    for component_plan in refreshed.component_plans:
        update = updated_plans.get(component_plan.component_id)
        if update is None:
            continue
        component_plan.steps = update.steps
        component_plan.validation_checks = update.validation_checks
        component_plan.rollback_notes = update.rollback_notes
        component_plan.disposition = update.disposition
        component_plan.estimated_effort = update.estimated_effort
        # wave_index deliberately not touched.

    for mapping in refreshed.component_mappings:
        update = updated_plans.get(mapping.component_id)
        if update is not None:
            mapping.disposition = update.disposition
            mapping.target_description = update.target_description

    refreshed.version = plan.version + 1
    return {"plan": refreshed, "refine_iteration": iteration + 1}


def finalize_review_node(state: GraphState) -> dict:
    """Converts whatever findings remain open into documented Risks and marks the
    plan reviewed, ready for Gate 2."""

    plan = state["plan"]
    open_findings = [f for f in state.get("findings", []) if f.resolution_status == ResolutionStatus.OPEN]

    finalized = plan.model_copy(deep=True)
    existing_risk_ids = {r.id for r in finalized.risks}
    for risk in unresolved_findings_to_risks(open_findings):
        if risk.id not in existing_risk_ids:
            finalized.risks.append(risk)

    finalized.status = PlanStatus.REVIEWED
    return {"plan": finalized, "stage": Stage.AWAITING_PLAN_APPROVAL}


def should_continue_refining(state: GraphState) -> str:
    """Conditional edge: refine again, or finalize."""

    settings = get_settings()
    iteration = state.get("refine_iteration", 0)
    open_findings = [f for f in state.get("findings", []) if f.resolution_status == ResolutionStatus.OPEN]

    if not open_findings:
        return "finalize"
    if iteration >= settings.max_refine_iterations:
        return "finalize"
    return "refine"

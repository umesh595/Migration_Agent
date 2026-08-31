"""Planning subgraph. The load-bearing guarantee lives here: compute_sequence()
runs BEFORE any per-component LLM call, and each component is handed to the LLM with
its wave already fixed (Doc 3 §3.3). Order is never an LLM output."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.core.cost_estimator import estimate_plan_cost
from app.core.graph_engine import CapacityExceededError, check_scale_envelope, compute_sequence
from app.core.plan_assembler import assemble_plan
from app.core.request_intelligence import RequestImpact, RequestIntent, render_request_impact_for_prompt
from app.llm.base import ModelTier, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.prompts.registry import get_prompt
from app.llm.schemas import (
    ComponentPlanLLMOutput,
    CutoverReviewOutput,
    MigrationContextElicitationOutput,
    RollbackPlanOutput,
    TargetArchitectureOutput,
)
from app.llm.state_injection import (
    render_component_planning_context,
    render_context_for_prompt,
    render_model_for_prompt,
)
from app.observability.tracing import trace_node
from app.orchestration.nodes.discovery import (
    resolve_dependency_open_questions_from_short_answer,
    resolve_environment_open_questions_from_short_answer,
)
from app.orchestration.state import GraphState, Stage
from app.schemas.architecture import Environment
from app.schemas.migration_context import DowntimeTolerance, MigrationContext
from app.schemas.patches import AddOpenQuestionPatch, PatchSet

logger = logging.getLogger(__name__)


def _coerce_environment(value: str) -> Environment:
    try:
        return Environment(value.strip().lower())
    except ValueError:
        return Environment.UNKNOWN


def _coerce_downtime(value: str) -> DowntimeTolerance:
    try:
        return DowntimeTolerance(value.strip().lower())
    except ValueError:
        return DowntimeTolerance.FLEXIBLE


async def after_gate_intake_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """Before collecting migration context, catch post-Gate-1 architecture
    corrections or target/source ambiguity. A message like "I forgot Redis in the
    source" should discuss the accepted-model boundary before the context node
    asks generic source/target/downtime questions."""

    prompt = get_prompt("ingest_patches")
    impact = state.get("request_impact")
    impact_section = (
        f"DETERMINISTIC REQUEST CLASSIFICATION:\n{render_request_impact_for_prompt(impact)}\n\n"
        if impact is not None
        else ""
    )
    user_prompt = (
        "CURRENT_STAGE: AFTER_GATE_1\n"
        "Gate 1 has already accepted the source architecture. If the user is correcting the accepted source "
        "model, ask whether to revise that accepted source model or treat it as target-state planning input. "
        "If the user is only giving migration context, emit no patches so context elicitation can continue.\n\n"
        f"{impact_section}ACCEPTED ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"PREVIOUS AGENT MESSAGE, IF THE USER IS ANSWERING IT:\n{state.get('previous_agent_message') or '(none)'}\n\n"
        f"USER MESSAGE:\n{state.get('user_message', '')}"
    )

    try:
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=PatchSet,
            meter=meter,
            node_name="planning.after_gate_intake",
        )
    except StructuredOutputError as exc:
        logger.error("planning after-gate intake failed: %s", exc)
        return {
            "error": "I couldn't process that message reliably. Could you rephrase it?",
            "last_patch_results": [],
        }

    patch_set = resolve_dependency_open_questions_from_short_answer(
        state["model"],
        state.get("user_message", ""),
        response.parsed,
        previous_agent_message=state.get("previous_agent_message"),
    )
    patch_set = resolve_environment_open_questions_from_short_answer(
        state["model"], state.get("user_message", ""), patch_set, state.get("request_impact")
    )
    patch_set = drop_spurious_target_state_intake_questions(
        state.get("user_message", ""), patch_set, state.get("request_impact")
    )
    return {"_patch_set": patch_set, "error": None}


def drop_spurious_target_state_intake_questions(
    user_message: str, patch_set: PatchSet, request_impact: RequestImpact | None = None
) -> PatchSet:
    """If the user is clearly providing migration context, don't let the
    after-Gate-1 guard turn target-state details into a source-model confirmation
    question. That guard is for source corrections, not normal planning input."""

    if request_impact is not None and request_impact.intent == RequestIntent.TARGET_PLANNING:
        is_migration_context = True
    else:
        is_migration_context = _looks_like_migration_context(user_message)

    if not is_migration_context:
        return patch_set

    kept = [
        patch
        for patch in patch_set.patches
        if not (
            isinstance(patch, AddOpenQuestionPatch)
            and _is_target_state_intake_question(patch.text)
        )
    ]
    if len(kept) == len(patch_set.patches):
        return patch_set
    return PatchSet(patches=kept, narration="" if not kept else patch_set.narration)


def _looks_like_migration_context(user_message: str) -> bool:
    text = user_message.lower()
    has_target_signal = any(
        phrase in text
        for phrase in (
            "target is",
            "target environment",
            "target architecture",
            "modernized",
            "move to",
            "migrate to",
            "run ",
            "host ",
            "retain ",
        )
    )
    has_planning_signal = any(
        phrase in text
        for phrase in (
            "downtime",
            "maintenance window",
            "zero downtime",
            "source is",
            "source environment",
            "ecs",
            "fargate",
            "eks",
            "cloud run",
            "kubernetes",
            "rds",
            "cloudfront",
        )
    )
    return has_target_signal and has_planning_signal


def _is_target_state_intake_question(text: str) -> bool:
    lowered = text.lower()
    return (
        "source" in lowered
        and "target-state" in lowered
        and ("revise" in lowered or "accepted" in lowered)
    )


async def elicit_context_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM + interrupt: free-text migration goal -> typed MigrationContext.
    If the model reports genuine ambiguity, we surface clarifying questions rather
    than guessing — a wrong value here corrupts every downstream decision."""

    prompt = get_prompt("elicit_migration_context")
    impact = state.get("request_impact")
    impact_section = (
        f"DETERMINISTIC REQUEST CLASSIFICATION:\n{render_request_impact_for_prompt(impact)}\n\n"
        if impact is not None
        else ""
    )
    user_prompt = (
        f"{impact_section}ACCEPTED ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"USER'S DESCRIPTION OF THE MIGRATION GOAL:\n{state.get('user_message', '')}"
    )

    try:
        response = await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=MigrationContextElicitationOutput,
            meter=meter,
            node_name="planning.elicit_context",
        )
    except StructuredOutputError as exc:
        logger.error("context elicitation failed: %s", exc)
        return {
            "error": "I couldn't structure that migration goal. "
            "Could you restate the target environment and downtime tolerance?"
        }

    parsed = response.parsed
    if parsed.clarifying_questions:
        return {
            "context_clarifying_questions": parsed.clarifying_questions,
            "migration_context": None,
            "stage": Stage.PLANNING,
        }

    context = MigrationContext(
        source_environment=_coerce_environment(parsed.source_environment),
        target_environment=_coerce_environment(parsed.target_environment),
        target_platform_description=parsed.target_platform_description,
        downtime_tolerance=_coerce_downtime(parsed.downtime_tolerance),
        maintenance_window_description=parsed.maintenance_window_description,
        constraints=parsed.constraints,
        target_completion_description=parsed.target_completion_description,
    )
    return {"migration_context": context, "context_clarifying_questions": [], "stage": Stage.PLANNING}


def compute_sequence_node(state: GraphState) -> dict:
    """Deterministic: topological sort -> waves. THE decision point for ordering.
    Runs before any component-level LLM call, by construction."""

    settings = get_settings()
    model = state["model"]

    try:
        check_scale_envelope(model, settings.max_components_per_model, settings.max_dependencies_per_model)
    except CapacityExceededError as exc:
        return {"error": str(exc)}

    waves = compute_sequence(model)
    trace_node(
        node_name="planning.compute_sequence",
        session_id=state.get("session_id", ""),
        metadata={"wave_count": len(waves), "component_count": len(model.components)},
    )
    return {"_waves": waves, "error": None}


async def per_component_planning_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM, batched per wave. Each call receives ASSIGNED_WAVE_INDEX_FIXED and has no
    schema field through which it could express a different ordering."""

    waves = state.get("_waves", [])
    context = state.get("migration_context")
    model = state["model"]
    if not waves or context is None:
        return {"error": "cannot plan components before sequencing and context elicitation complete"}

    prompt = get_prompt("plan_component")

    async def plan_one(component_id: str, wave) -> ComponentPlanLLMOutput | None:
        user_prompt = render_component_planning_context(model, component_id, wave, context, waves)
        try:
            response = await gateway.complete(
                tier=ModelTier.STRONG,
                system_prompt=prompt.system,
                user_prompt=user_prompt,
                response_model=ComponentPlanLLMOutput,
                meter=meter,
                node_name=f"planning.component.{component_id}",
            )
        except StructuredOutputError as exc:
            logger.error("component planning failed for %s: %s", component_id, exc)
            return None

        # The LLM echoes component_id; trust code's value, not the model's, so a
        # hallucinated id can't attach a plan to the wrong component.
        parsed = response.parsed
        parsed.component_id = component_id
        return parsed

    outputs: list[ComponentPlanLLMOutput] = []
    for wave in waves:
        wave_results = await asyncio.gather(*(plan_one(cid, wave) for cid in wave.component_ids))
        outputs.extend(o for o in wave_results if o is not None)

    failed = {c.id for c in model.components} - {o.component_id for o in outputs}
    if failed:
        return {"error": f"could not produce plans for: {sorted(failed)}. Try again or simplify those components."}

    return {"_component_outputs": outputs, "error": None}


async def strategy_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: target architecture narrative + cutover + rollback strategies."""

    context = state["migration_context"]
    model = state["model"]
    waves = state.get("_waves", [])
    outputs = state.get("_component_outputs", [])

    wave_summary = "\n".join(f"Wave {w.index}: {', '.join(w.component_ids)}" for w in waves)
    component_targets = "\n".join(f"- {o.component_id}: {o.disposition} -> {o.target_description}" for o in outputs)
    shared_context = (
        f"CURRENT MODEL:\n{render_model_for_prompt(model)}\n\n"
        f"MIGRATION CONTEXT:\n{render_context_for_prompt(context)}\n\n"
        f"COMPUTED WAVE SEQUENCE:\n{wave_summary}\n\n"
        f"PER-COMPONENT TARGETS:\n{component_targets}"
    )

    async def call(prompt_id: str, response_model, node_name: str):
        return await gateway.complete(
            tier=ModelTier.STRONG,
            system_prompt=get_prompt(prompt_id).system,
            user_prompt=shared_context,
            response_model=response_model,
            meter=meter,
            node_name=node_name,
        )

    try:
        target, cutover, rollback = await asyncio.gather(
            call("target_architecture", TargetArchitectureOutput, "planning.target_architecture"),
            call("cutover_strategy", CutoverReviewOutput, "planning.cutover"),
            call("rollback_strategy", RollbackPlanOutput, "planning.rollback"),
        )
    except StructuredOutputError as exc:
        logger.error("strategy generation failed: %s", exc)
        return {"error": "Could not generate the cutover/rollback strategy. Please retry."}

    return {
        "_target_architecture": target.parsed.description,
        "_cutover": cutover.parsed,
        "_rollback": rollback.parsed,
        "error": None,
    }


def assemble_plan_node(state: GraphState) -> dict:
    """Deterministic: PlanAssembler + CoverageChecker -> DRAFT plan."""

    plan = assemble_plan(
        model=state["model"],
        waves=state["_waves"],
        component_outputs=state["_component_outputs"],
        target_architecture_description=state["_target_architecture"],
        cutover=state["_cutover"],
        rollback=state["_rollback"],
    )
    trace_node(
        node_name="planning.assemble_plan",
        session_id=state.get("session_id", ""),
        metadata={
            "component_plan_count": len(plan.component_plans),
            "risk_count": len(plan.risks),
            "wave_count": len(plan.waves),
        },
    )
    return {
        "plan": plan,
        "stage": Stage.REVIEW,
        "_waves": None,
        "_component_outputs": None,
        "_target_architecture": None,
        "_cutover": None,
        "_rollback": None,
    }


async def estimate_cost_node(state: GraphState) -> dict:
    """Deliverable 11 — Cost Estimate. Deterministic (real pricing-API lookups,
    never an LLM call — see cost_estimator's module docstring); runs after the
    plan exists since it needs component_mappings[].target_cloud_provider /
    target_service_category, which per_component_planning just produced."""

    plan = state["plan"]
    summary = await estimate_plan_cost(state["model"], plan)
    trace_node(
        node_name="planning.estimate_cost",
        session_id=state.get("session_id", ""),
        metadata={
            "total_monthly_usd": summary.total_monthly_usd,
            "unestimated_count": len(summary.unestimated_component_ids),
        },
    )
    return {"plan": plan.model_copy(update={"cost_summary": summary})}

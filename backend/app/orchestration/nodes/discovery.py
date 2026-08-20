"""Discovery subgraph nodes. LLM nodes are marked; every other node is deterministic.

Flow (Doc 3 §3.1):
  ingest (LLM) -> apply_patches (code) -> gap_analysis (code) -> generate_questions (LLM) -> interrupt
"""

from __future__ import annotations

import logging

from app.core.gap_analyzer import top_gaps
from app.core.patch_applier import apply_patch_set
from app.llm.base import ModelTier, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.prompts.registry import get_prompt
from app.llm.schemas import QuestionGenerationOutput
from app.llm.state_injection import render_gaps_for_prompt, render_model_for_prompt
from app.observability.tracing import trace_node
from app.orchestration.state import GraphState, Stage
from app.schemas.patches import PatchSet

logger = logging.getLogger(__name__)


async def ingest_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: user text -> proposed patches. Closed-world: sees only the current model
    plus this message."""

    prompt = get_prompt("ingest_patches")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"USER MESSAGE:\n{state['user_message']}"
    )

    try:
        response = await gateway.complete(
            tier=ModelTier.CHEAP,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=PatchSet,
            meter=meter,
            node_name="discovery.ingest",
        )
    except StructuredOutputError as exc:
        # State untouched — this is the documented failure branch (Doc 3 §3.2).
        logger.error("ingest failed for session %s: %s", state.get("session_id"), exc)
        return {
            "error": "I couldn't process that message reliably. Could you rephrase it?",
            "last_patch_results": [],
        }

    return {"_patch_set": response.parsed, "error": None}


def apply_patches_node(state: GraphState) -> dict:
    """Deterministic: validate, apply, audit, version++."""

    patch_set: PatchSet | None = state.get("_patch_set")
    if patch_set is None:
        return {"last_patch_results": []}

    new_model, results = apply_patch_set(state["model"], patch_set)
    trace_node(
        node_name="discovery.apply_patches",
        session_id=state.get("session_id", ""),
        metadata={
            "patches_applied": sum(1 for r in results if r.outcome == "applied"),
            "patches_rejected": sum(1 for r in results if r.outcome == "rejected"),
            "model_version": new_model.version,
        },
    )
    return {
        "model": new_model,
        "last_patch_results": results,
        "narration": patch_set.narration,
        "_patch_set": None,
    }


def gap_analysis_node(state: GraphState) -> dict:
    """Deterministic: recompute unknowns from the UPDATED model."""

    gaps = top_gaps(state["model"], n=3)
    trace_node(
        node_name="discovery.gap_analysis",
        session_id=state.get("session_id", ""),
        metadata={"gap_count": len(gaps)},
    )
    return {"_gaps": gaps}


async def generate_questions_node(state: GraphState, gateway: LLMGateway, meter: SessionTokenMeter) -> dict:
    """LLM: computed gaps -> contextual questions. Never invents its own gaps."""

    gaps = state.get("_gaps", [])
    if not gaps:
        return {
            "pending_questions": [],
            "stage": Stage.DISCOVERY,
            "_gaps": None,
        }

    prompt = get_prompt("generate_questions")
    user_prompt = (
        f"CURRENT ARCHITECTURE MODEL:\n{render_model_for_prompt(state['model'])}\n\n"
        f"COMPUTED GAPS (ask about these, and only these):\n{render_gaps_for_prompt(gaps)}"
    )

    try:
        response = await gateway.complete(
            tier=ModelTier.CHEAP,
            system_prompt=prompt.system,
            user_prompt=user_prompt,
            response_model=QuestionGenerationOutput,
            meter=meter,
            node_name="discovery.generate_questions",
        )
    except StructuredOutputError:
        # Degrade to the raw computed gap text rather than failing the turn — the
        # gaps are real either way, they just read less naturally.
        return {
            "pending_questions": [g.description for g in gaps],
            "stage": Stage.DISCOVERY,
            "_gaps": None,
        }

    return {
        "pending_questions": [q.text for q in response.parsed.questions],
        "narration": f"{state.get('narration', '')}\n\n{response.parsed.narration}".strip(),
        "stage": Stage.DISCOVERY,
        "_gaps": None,
    }

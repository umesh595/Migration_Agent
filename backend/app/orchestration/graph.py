"""Builds the StateGraph (technique #1). Stages cannot be skipped because the edges
say so — not because a prompt asks the model nicely.

Three subgraphs are expressed as three entry points on one graph, each invoked by a
distinct API action, with the checkpointer carrying state between them:
  - discovery turn   (POST /sessions/{id}/messages while stage == discovery)
  - planning run     (triggered by POST /sessions/{id}/model/accept — gate 1)
  - review runs as the tail of planning, ending at gate 2

Gates are enforced in the API layer against persisted status (app/api/routers), NOT
by trusting graph state alone — a gate that can be bypassed by replaying a stale
checkpoint isn't a gate.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.orchestration.nodes import discovery, planning, review
from app.orchestration.state import GraphState


def build_discovery_graph(gateway: LLMGateway, meter: SessionTokenMeter):
    graph = StateGraph(GraphState)

    graph.add_node("ingest", partial(discovery.ingest_node, gateway=gateway, meter=meter))
    graph.add_node("apply_patches", discovery.apply_patches_node)
    graph.add_node("gap_analysis", discovery.gap_analysis_node)
    graph.add_node("generate_questions", partial(discovery.generate_questions_node, gateway=gateway, meter=meter))

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "apply_patches")
    graph.add_edge("apply_patches", "gap_analysis")
    graph.add_edge("gap_analysis", "generate_questions")
    graph.add_edge("generate_questions", END)

    return graph


def build_planning_graph(gateway: LLMGateway, meter: SessionTokenMeter):
    """Planning + review as one run, ending at gate 2. compute_sequence sits between
    context elicitation and any per-component LLM call — that edge order is the
    structural guarantee described in Doc 3 §3.3."""

    graph = StateGraph(GraphState)

    graph.add_node("elicit_context", partial(planning.elicit_context_node, gateway=gateway, meter=meter))
    graph.add_node("compute_sequence", planning.compute_sequence_node)
    graph.add_node("per_component_planning", partial(planning.per_component_planning_node, gateway=gateway, meter=meter))
    graph.add_node("strategy", partial(planning.strategy_node, gateway=gateway, meter=meter))
    graph.add_node("assemble_plan", planning.assemble_plan_node)
    graph.add_node("rules_review", review.rules_review_node)
    graph.add_node("llm_review", partial(review.llm_review_node, gateway=gateway, meter=meter))
    graph.add_node("judge_review", partial(review.judge_review_node, gateway=gateway, meter=meter))
    graph.add_node("refine", partial(review.refine_node, gateway=gateway, meter=meter))
    graph.add_node("finalize_review", review.finalize_review_node)

    graph.add_edge(START, "elicit_context")
    graph.add_conditional_edges(
        "elicit_context",
        _context_ready,
        {"continue": "compute_sequence", "await_user": END},
    )
    graph.add_conditional_edges(
        "compute_sequence",
        _no_error,
        {"continue": "per_component_planning", "halt": END},
    )
    graph.add_conditional_edges(
        "per_component_planning",
        _no_error,
        {"continue": "strategy", "halt": END},
    )
    graph.add_conditional_edges(
        "strategy",
        _no_error,
        {"continue": "assemble_plan", "halt": END},
    )
    graph.add_edge("assemble_plan", "rules_review")
    graph.add_edge("rules_review", "llm_review")
    graph.add_edge("llm_review", "judge_review")
    graph.add_conditional_edges(
        "judge_review",
        review.should_continue_refining,
        {"refine": "refine", "finalize": "finalize_review"},
    )
    graph.add_edge("refine", "rules_review")
    graph.add_edge("finalize_review", END)

    return graph


def _context_ready(state: GraphState) -> str:
    if state.get("error") or state.get("context_clarifying_questions"):
        return "await_user"
    return "continue" if state.get("migration_context") is not None else "await_user"


def _no_error(state: GraphState) -> str:
    return "halt" if state.get("error") else "continue"

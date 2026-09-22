"""Policy attached to a classified user request.

This module used to classify intent itself, with hand-maintained keyword tuples
("java", "spring boot", "stripe", "payment", "run ", "host "...). That approach was
wrong in a way no amount of list-tending fixes: it only recognized the technologies,
domains and phrasings someone had thought to enumerate in English. A migration off
COBOL, a request to add device provisioning, or the same sentence written in another
language all fell through to the default branch, while an innocent "we run a booking
system" tripped the target-planning branch on the substring "run ".

Intent is now judged by the LLM that already reads the message (PatchSet.request_intent
— the same pattern the codebase already uses for user_technical_signal), so it
generalizes to any system, wording or language without a code change.

What stays here is POLICY, which is deterministic on purpose: given an intent and the
session's stage, what is the app allowed to do? Whether a message may mutate the
accepted source architecture is a safety rule, not an act of language understanding,
and it must be testable and identical every time.
"""

from __future__ import annotations

from app.schemas.requests import RequestImpact, RequestIntent

__all__ = ["RequestImpact", "RequestIntent", "derive_request_impact", "render_request_impact_for_prompt"]


# Per-intent policy. Only `intent` comes from the LLM; everything below is decided by
# code so the same intent always carries the same permissions.
_POLICY: dict[RequestIntent, dict] = {
    RequestIntent.SPARSE_INTAKE: {
        "should_mutate_source": False,
        "requires_confirmation": False,
        "rationale": (
            "The user gave a thin business/application description, not enough deployable architecture "
            "detail to safely create components and dependencies."
        ),
        "impact_dimensions": ["source_architecture", "target"],
    },
    RequestIntent.CURRENT_FACT: {
        "should_mutate_source": True,
        "requires_confirmation": False,
        "rationale": "The message describes or refines architecture facts.",
        "impact_dimensions": [],
    },
    RequestIntent.SOURCE_CORRECTION: {
        "should_mutate_source": False,
        "requires_confirmation": True,
        "rationale": (
            "The user is correcting the accepted source architecture, so the app must confirm "
            "whether to revise the frozen baseline."
        ),
        "impact_dimensions": ["effort", "cost", "sequencing", "validation", "rollback"],
    },
    RequestIntent.TARGET_PLANNING: {
        "should_mutate_source": False,
        "requires_confirmation": False,
        "rationale": "The user is providing migration target/context, not correcting the source architecture.",
        "impact_dimensions": ["target_service_choice", "cost", "operations", "cutover"],
    },
    RequestIntent.HIGH_IMPACT_REPLATFORM: {
        "should_mutate_source": False,
        "requires_confirmation": True,
        "rationale": "The user is proposing a technology/runtime replacement that changes delivery scope and risk.",
        "impact_dimensions": ["effort", "cost", "team_skills", "testing", "rollback", "dependencies"],
    },
    RequestIntent.UNSCOPED_CAPABILITY: {
        "should_mutate_source": False,
        "requires_confirmation": True,
        "rationale": (
            "The user may be proposing a new business capability that needs justification "
            "before it becomes architecture."
        ),
        "impact_dimensions": ["scope", "cost", "integration"],
    },
    RequestIntent.REVIEW_EXPLANATION: {
        "should_mutate_source": False,
        "requires_confirmation": False,
        "rationale": "The user is asking to inspect or explain the generated plan, not to change the model.",
        "impact_dimensions": ["effort", "cost", "risk", "validation", "rollback"],
    },
    RequestIntent.TERSE_CONFIRMATION: {
        "should_mutate_source": False,
        "requires_confirmation": False,
        "rationale": "The user is answering a previous question tersely.",
        "impact_dimensions": [],
    },
    RequestIntent.PROCEED_WITH_ASSUMPTIONS: {
        "should_mutate_source": True,
        "requires_confirmation": False,
        "rationale": (
            "The user explicitly asked to proceed despite incomplete information, so the app should "
            "draft with clearly labeled assumptions instead of asking another clarifying question."
        ),
        "impact_dimensions": ["completeness", "assumptions"],
    },
    RequestIntent.UNKNOWN: {
        "should_mutate_source": False,
        "requires_confirmation": False,
        "rationale": "The message's intent could not be determined; treat it conservatively.",
        "impact_dimensions": [],
    },
}


def derive_request_impact(
    intent: RequestIntent | str | None,
    *,
    after_gate_1: bool = False,
    confidence: str = "high",
) -> RequestImpact:
    """Turn an LLM-judged intent into the permissions that intent carries.

    `after_gate_1` is a hard override, not a hint: once the source model is frozen,
    nothing may mutate it without explicit confirmation regardless of how the message
    reads. That guarantee belongs in code — a model asked nicely not to mutate is a
    request, whereas this is a rule (Doc 3 §2.3).
    """

    try:
        resolved = RequestIntent(intent) if intent is not None else RequestIntent.UNKNOWN
    except ValueError:
        resolved = RequestIntent.UNKNOWN

    policy = _POLICY[resolved]
    should_mutate_source = policy["should_mutate_source"] and not after_gate_1

    return RequestImpact(
        intent=resolved,
        confidence=confidence,
        should_mutate_source=should_mutate_source,
        requires_confirmation=policy["requires_confirmation"],
        rationale=policy["rationale"],
        impact_dimensions=list(policy["impact_dimensions"]),
    )


def render_request_impact_for_prompt(impact: RequestImpact) -> str:
    return impact.model_dump_json(indent=2)

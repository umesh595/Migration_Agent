from __future__ import annotations

import pytest

from app.core.request_intelligence import classify_user_request
from app.llm.base import ModelTier
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel, Environment, WorkloadType
from app.schemas.patches import AddComponentPatch, PatchSet


@pytest.mark.asyncio
async def test_fast_discovery_turn_stays_on_the_cheap_tier_for_every_real_llm_call():
    """The fast path's latency win must come from a smaller prompt + cheaper
    tier on REAL calls, never from skipping reasoning altogether — a prior
    version of this test pinned exactly that shortcut (a hardcoded, non-LLM
    question keyed off the gap's category) as "the deterministic questions
    step," which is precisely the class of bug this project's "never hardcode
    the reasoning" rule exists to prevent. Both ingest and question generation
    must still be genuine, dynamic LLM calls on the fast path — just cheap-tier
    ones with a condensed prompt."""

    provider = MockProvider()
    provider.register(
        PatchSet,
        PatchSet(
            patches=[
                AddComponentPatch(
                    id="api", name="Booking API", workload_type=WorkloadType.API_SERVICE,
                    environment=Environment.CLOUD,
                )
            ],
            narration="Captured the booking API.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(
            questions=[GeneratedQuestion(text="How business-critical is the Booking API?")],
            narration="One more detail helps.",
        ),
    )
    graph = build_discovery_graph(LLMGateway(provider), SessionTokenMeter(100_000)).compile()
    message = "The booking API runs in GCP."

    result = await graph.ainvoke(
        {
            "session_id": "fast-path", "stage": Stage.DISCOVERY, "model": ArchitectureModel(),
            "user_message": message, "conversation_context": message,
            "request_impact": classify_user_request(message),
        }
    )

    # Exactly two real LLM calls (ingest, generate_questions) — no more, and
    # neither one skipped in favor of a fixed string.
    assert len(provider.calls) == 2
    assert all(call["model"] == f"mock-{ModelTier.CHEAP}" for call in provider.calls)
    assert result["model"].components[0].name == "Booking API"
    assert result["pending_questions"] == ["How business-critical is the Booking API?"]
    assert result["question_details"]


@pytest.mark.asyncio
async def test_a_substantial_message_still_gets_full_requirement_coverage_analysis():
    """discovery_fast_mode used to skip the requirement-coverage generator/critic
    pair (the source of most multi-topic questions — access control, payments,
    compliance, PII, scale) UNCONDITIONALLY, for every message regardless of
    length. That silently lost an entire dimension of analysis even for a long,
    detailed message that clearly warranted it. It must now use the same
    length-based gate ingest_node already uses for its own fast path: short
    stays fast, but a message at or beyond discovery_full_prompt_min_chars
    still gets the full generator+critic pass."""

    from app.config import get_settings
    from app.llm.schemas import RequirementCoverageCriticOutput, RequirementCoverageOutput, RequirementCoverageVerdict
    from app.orchestration.nodes.discovery import gap_analysis_node
    from app.schemas.architecture import Component, WorkloadType

    get_settings.cache_clear()
    long_message = "The booking API handles payments and customer PII. " * 100
    assert len(long_message) >= get_settings().discovery_full_prompt_min_chars

    provider = MockProvider()
    provider.register(
        RequirementCoverageOutput,
        RequirementCoverageOutput(
            requirements=[
                RequirementCoverageVerdict(category="payment idempotency", status="unknown", high_impact=True)
            ]
        ),
    )
    provider.register(
        RequirementCoverageCriticOutput,
        RequirementCoverageCriticOutput(
            corrected_requirements=[
                RequirementCoverageVerdict(category="payment idempotency", status="unknown", high_impact=True)
            ]
        ),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    model = ArchitectureModel(
        components=[Component(id="api", name="Booking API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD)]
    )

    result = await gap_analysis_node(
        {"session_id": "s1", "model": model, "user_message": long_message, "conversation_context": long_message},
        gateway=gateway,
        meter=meter,
    )

    assert len(provider.calls) == 2, "the long-message path must run the generator AND its critic"
    assert any(g.description and "payment idempotency" in g.description for g in result["_gaps"])


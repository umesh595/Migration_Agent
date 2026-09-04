"""critique_ingest_completeness: the second-opinion pass over ingest_node's own
patch proposal (technique #8's rules->critic->judge pattern, applied to
discovery ingestion). Must be additive-only — a missed fact becomes a new
assumption, never a mutation of what the LLM already proposed — and must
never block the turn if the critic call itself fails."""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import Contradiction, IngestCompletenessCriticOutput
from app.orchestration.nodes.discovery import critique_ingest_completeness
from app.schemas.architecture import ArchitectureModel, Component, WorkloadType
from app.schemas.patches import AddAssumptionPatch, PatchOp, PatchSet


def _model() -> ArchitectureModel:
    return ArchitectureModel(
        components=[Component(id="db", name="Database", workload_type=WorkloadType.DATABASE)]
    )


@pytest.mark.asyncio
async def test_missed_facts_become_an_additional_assumption_patch():
    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(
            fully_captured=False,
            missed_facts=["PII fields: name, email, phone", "roughly 50k users"],
            invented_facts=[],
            rationale="Narration mentioned PII and scale but no patch recorded either.",
        ),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="Noted the PII and scale details.")

    result = await critique_ingest_completeness(
        _model(), "PII is just name, email, phone and we have roughly 50k users", original, gateway, meter
    )

    added = [p for p in result.patches if p.op == PatchOp.ADD_ASSUMPTION]
    assert len(added) == 1
    assert "PII fields: name, email, phone" in added[0].text
    assert "roughly 50k users" in added[0].text
    assert result.narration == original.narration


@pytest.mark.asyncio
async def test_fully_captured_verdict_leaves_patch_set_untouched():
    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(fully_captured=True, missed_facts=[], invented_facts=[], rationale="Complete."),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(
        patches=[AddAssumptionPatch(text="Already captured.", related_component_ids=["db"])], narration="ok"
    )

    result = await critique_ingest_completeness(_model(), "the database stores booking records", original, gateway, meter)

    assert result is original


@pytest.mark.asyncio
async def test_skips_trivial_short_messages_without_calling_the_llm():
    provider = MockProvider()
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="")

    result = await critique_ingest_completeness(_model(), "yes", original, gateway, meter)

    assert result is original
    assert provider.calls == []


@pytest.mark.asyncio
async def test_contradiction_becomes_a_tracked_open_question_not_a_silent_pick():
    """Contradiction Detector: a genuine conflict must become a real, tracked
    open question (so it surfaces via gap analysis and gets asked about) —
    never silently resolved by the critic picking a side, and never left only
    in narration where gap analysis can't see it."""

    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(
            fully_captured=True,
            missed_facts=[],
            invented_facts=[],
            contradictions=[
                Contradiction(
                    description="the system was earlier described as fully on-prem, but this message says "
                    "all services run on AWS ECS",
                    existing_fact="A1: the system runs entirely on-prem",
                    new_statement="all services run on AWS ECS",
                    severity="medium",
                )
            ],
            rationale="Direct conflict with an earlier confirmed fact.",
        ),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="Noted the ECS deployment.")

    result = await critique_ingest_completeness(
        _model(), "all services run on AWS ECS now", original, gateway, meter
    )

    added = [p for p in result.patches if p.op == PatchOp.ADD_OPEN_QUESTION]
    assert len(added) == 1
    assert "CONTRADICTION" in added[0].text
    assert "on-prem" in added[0].text
    assert "AWS ECS" in added[0].text
    assert result.narration == original.narration


@pytest.mark.asyncio
async def test_missed_facts_and_contradictions_can_both_be_emitted_in_the_same_turn():
    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(
            fully_captured=False,
            missed_facts=["roughly 50k users"],
            invented_facts=[],
            contradictions=[
                Contradiction(
                    description="no PII stated, but customer email and phone are also described",
                    existing_fact="",
                    new_statement="stores customer email and phone",
                    severity="high",
                )
            ],
            rationale="Both a gap and a conflict in the same message.",
        ),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="ok")

    result = await critique_ingest_completeness(
        _model(), "no PII at all, we do store customer email and phone though", original, gateway, meter
    )

    assert len([p for p in result.patches if p.op == PatchOp.ADD_ASSUMPTION]) == 1
    assert len([p for p in result.patches if p.op == PatchOp.ADD_OPEN_QUESTION]) == 1


@pytest.mark.asyncio
async def test_no_contradictions_leaves_patch_set_untouched():
    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(fully_captured=True, missed_facts=[], invented_facts=[], contradictions=[], rationale="Fine."),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="ok")

    result = await critique_ingest_completeness(_model(), "the database stores booking records", original, gateway, meter)

    assert result is original


@pytest.mark.asyncio
async def test_critic_failure_is_non_blocking():
    provider = MockProvider()  # no registered response -> StructuredOutputError
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="")

    result = await critique_ingest_completeness(
        _model(), "the database stores booking records for the movie app", original, gateway, meter
    )

    assert result is original

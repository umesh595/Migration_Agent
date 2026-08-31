"""critique_ingest_completeness: the second-opinion pass over ingest_node's own
patch proposal (technique #8's rules->critic->judge pattern, applied to
discovery ingestion). Must be additive-only — a missed fact becomes a new
assumption, never a mutation of what the LLM already proposed — and must
never block the turn if the critic call itself fails."""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import IngestCompletenessCriticOutput
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
async def test_critic_failure_is_non_blocking():
    provider = MockProvider()  # no registered response -> StructuredOutputError
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)
    original = PatchSet(patches=[], narration="")

    result = await critique_ingest_completeness(
        _model(), "the database stores booking records for the movie app", original, gateway, meter
    )

    assert result is original

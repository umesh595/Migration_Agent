"""A reasoning-tier model (deepseekv4p, verified live via the CodeVector gateway)
returns its own chain-of-thought alongside the final structured output. Rather
than let that be computed and thrown away, the generator/critic pairs in
discovery.py thread it into the critic's own prompt as evidence to
cross-examine — the goal being to close some of the quality gap against a
stronger single model by making the SECOND opinion actually see the first
model's reasoning, not just its conclusion. These tests cover the plumbing:
the reasoning text must reach the critic's prompt when present, and every
call site must degrade cleanly (empty section, no crash) when it's absent."""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import (
    IngestCompletenessCriticOutput,
    RequirementCoverageCriticOutput,
    RequirementCoverageVerdict,
)
from app.llm.state_injection import render_generator_reasoning_for_prompt
from app.orchestration.nodes.discovery import _critique_requirement_coverage, critique_ingest_completeness
from app.schemas.architecture import ArchitectureModel
from app.schemas.patches import PatchSet


def test_render_generator_reasoning_is_empty_for_none():
    assert render_generator_reasoning_for_prompt(None) == ""
    assert render_generator_reasoning_for_prompt("") == ""


def test_render_generator_reasoning_labels_and_caps_a_real_trace():
    rendered = render_generator_reasoning_for_prompt("x" * 5000)

    assert "GENERATOR'S OWN REASONING FOR THIS TURN" in rendered
    assert "not shown to the user" in rendered
    # Capped well under the raw 5000 chars so a long trace can't dominate the
    # critic's own deliberately small prompt budget.
    assert len(rendered) < 3500


@pytest.mark.asyncio
async def test_ingest_completeness_critic_receives_the_generators_reasoning():
    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(fully_captured=True, missed_facts=[], invented_facts=[], contradictions=[], rationale="ok"),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    patch_set = PatchSet(patches=[], narration="Captured the booking API.")

    await critique_ingest_completeness(
        ArchitectureModel(),
        "The booking API talks to Postgres and Redis, handles PII, and must never double-charge a customer.",
        patch_set,
        gateway,
        meter,
        session_id="s1",
        generator_reasoning="I noticed PII and payment language, so I should check both are captured as facts.",
    )

    assert len(provider.calls) == 1
    assert "GENERATOR'S OWN REASONING FOR THIS TURN" in provider.calls[0]["user"]
    assert "I noticed PII and payment language" in provider.calls[0]["user"]


@pytest.mark.asyncio
async def test_ingest_completeness_critic_degrades_cleanly_with_no_reasoning():
    """A non-reasoning model/provider never sets `reasoning` — the critic call
    must still work exactly as it did before this feature existed."""

    provider = MockProvider()
    provider.register(
        IngestCompletenessCriticOutput,
        IngestCompletenessCriticOutput(fully_captured=True, missed_facts=[], invented_facts=[], contradictions=[], rationale="ok"),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    patch_set = PatchSet(patches=[], narration="Captured the booking API.")

    await critique_ingest_completeness(
        ArchitectureModel(),
        "The booking API talks to Postgres and handles PII data for customers.",
        patch_set,
        gateway,
        meter,
        session_id="s1",
    )

    assert "GENERATOR'S OWN REASONING FOR THIS TURN" not in provider.calls[0]["user"]


@pytest.mark.asyncio
async def test_requirement_coverage_critic_receives_the_generators_reasoning():
    provider = MockProvider()
    provider.register(
        RequirementCoverageCriticOutput,
        RequirementCoverageCriticOutput(corrected_requirements=[]),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    verdicts = [RequirementCoverageVerdict(category="payment idempotency", status="unknown", high_impact=True)]

    await _critique_requirement_coverage(
        ArchitectureModel(),
        "user message",
        verdicts,
        gateway,
        meter,
        session_id="s1",
        generator_reasoning="I flagged payment idempotency as unknown because nothing described retry handling.",
    )

    assert "GENERATOR'S OWN REASONING FOR THIS TURN" in provider.calls[0]["user"]
    assert "nothing described retry handling" in provider.calls[0]["user"]

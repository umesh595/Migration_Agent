"""assess_dynamic_requirement_coverage: replaces the old fixed keyword-matched
_BASIC_REQUIREMENTS checklist with a domain-aware LLM generator/critic pair
that also breaks the "same hedge asked forever" loop. A keyword scan couldn't
tell "we removed SSO" from "we have SSO", couldn't handle paraphrase, could
only ever check categories a human anticipated in advance, and had no way to
distinguish a confident answer from a hedge — or to stop re-asking a hedge
that's already been raised once. This suite covers all of that."""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import RequirementCoverageCriticOutput, RequirementCoverageOutput, RequirementCoverageVerdict
from app.orchestration.nodes.discovery import assess_dynamic_requirement_coverage
from app.schemas.architecture import ArchitectureModel, Assumption, Component, WorkloadType


def _model_with_components() -> ArchitectureModel:
    return ArchitectureModel(
        components=[
            Component(id="frontend", name="Web Frontend", workload_type=WorkloadType.WEB_SERVICE),
            Component(id="backend", name="Backend API", workload_type=WorkloadType.API_SERVICE),
            Component(id="db", name="Booking Database", workload_type=WorkloadType.DATABASE),
        ]
    )


def _passthrough_critic(verdicts: list[RequirementCoverageVerdict]) -> RequirementCoverageCriticOutput:
    """A critic response that agrees with the generator verbatim, for tests
    that aren't specifically exercising the critic's own correction logic."""

    return RequirementCoverageCriticOutput(corrected_requirements=verdicts, corrections_made=[])


@pytest.mark.asyncio
async def test_unknown_verdicts_become_one_grouped_basic_app_requirements_gap():
    verdicts = [
        RequirementCoverageVerdict(category="authentication/roles", status="unknown", high_impact=True),
        RequirementCoverageVerdict(category="seat locking during checkout", status="unknown", high_impact=True),
        RequirementCoverageVerdict(
            category="external integrations",
            status="not_applicable",
            high_impact=False,
            evidence="user said no integrations",
        ),
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, model = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert len(gaps) == 1
    assert gaps[0].category == "basic_app_requirements"
    assert "authentication/roles" in gaps[0].description
    assert "seat locking during checkout" in gaps[0].description
    assert "external integrations" not in gaps[0].description
    assert model.assumptions == []


@pytest.mark.asyncio
async def test_all_covered_or_not_applicable_produces_no_gap():
    verdicts = [
        RequirementCoverageVerdict(
            category="authentication/roles", status="covered", high_impact=True, evidence="SSO login"
        ),
        RequirementCoverageVerdict(
            category="external integrations", status="not_applicable", high_impact=False, evidence="none stated"
        ),
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, _ = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert gaps == []


@pytest.mark.asyncio
async def test_skipped_when_model_is_still_sparse_no_components_no_assumptions():
    provider = MockProvider()
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    sparse_model = ArchitectureModel()

    gaps, model = await assess_dynamic_requirement_coverage(sparse_model, gateway, meter)

    assert gaps == []
    assert model is sparse_model
    assert provider.calls == []


@pytest.mark.asyncio
async def test_runs_even_with_zero_components_if_assumptions_exist():
    """The stuck-at-zero-components case this whole mechanism exists to fix:
    real detail was captured only as assumptions — coverage should still be
    assessed rather than skipped as sparse."""

    verdicts = [RequirementCoverageVerdict(category="scale", status="unknown", high_impact=False)]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)
    model = ArchitectureModel(
        assumptions=[Assumption(id="A1", text="Accepts bookings, hosted on GCP.", raised_by="llm", resolved=True)]
    )

    gaps, _ = await assess_dynamic_requirement_coverage(model, gateway, meter)

    assert len(gaps) == 1
    assert "scale" in gaps[0].description


@pytest.mark.asyncio
async def test_hedged_high_impact_verdict_is_called_out_urgently_and_never_dropped():
    """The exact live-reported bug: "no idea how seat locking works, maybe
    just a db transaction" must not be treated as settled just because SOME
    answer was given — a hedge on a high-impact category has to keep
    surfacing until the user gives a confident answer or it escalates."""

    verdicts = [
        RequirementCoverageVerdict(
            category="seat locking during checkout",
            status="hedged_or_uncertain",
            high_impact=True,
            evidence="no idea how seat locking works tbh maybe just db transaction",
        )
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, _ = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert len(gaps) == 1
    assert "IMPORTANT" in gaps[0].description
    assert "seat locking during checkout" in gaps[0].description
    assert "no idea how seat locking works" in gaps[0].description


@pytest.mark.asyncio
async def test_critic_can_downgrade_a_generator_verdict_and_add_a_missed_category():
    generator_verdicts = [
        RequirementCoverageVerdict(
            category="seat locking during checkout",
            status="covered",
            high_impact=True,
            evidence="maybe just a db transaction",
        )
    ]
    corrected_verdicts = [
        RequirementCoverageVerdict(
            category="seat locking during checkout",
            status="hedged_or_uncertain",
            high_impact=True,
            evidence="maybe just a db transaction",
        ),
        RequirementCoverageVerdict(category="payment idempotency", status="unknown", high_impact=True),
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=generator_verdicts))
    provider.register(
        RequirementCoverageCriticOutput,
        RequirementCoverageCriticOutput(
            corrected_requirements=corrected_verdicts,
            corrections_made=["seat locking was marked covered but the evidence is a hedge — downgraded"],
        ),
    )
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, _ = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert len(gaps) == 1
    assert "seat locking during checkout" in gaps[0].description
    assert "payment idempotency" in gaps[0].description


@pytest.mark.asyncio
async def test_generator_failure_is_non_blocking():
    provider = MockProvider()  # no registered response -> StructuredOutputError
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)
    original = _model_with_components()

    gaps, model = await assess_dynamic_requirement_coverage(original, gateway, meter)

    assert gaps == []
    assert model is original


@pytest.mark.asyncio
async def test_critic_failure_falls_back_to_generators_own_verdicts():
    verdicts = [RequirementCoverageVerdict(category="scale", status="unknown", high_impact=False)]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    # No RequirementCoverageCriticOutput registered -> critic call fails, should fall back gracefully.
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)

    gaps, _ = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert len(gaps) == 1
    assert "scale" in gaps[0].description


@pytest.mark.asyncio
async def test_escalated_hedge_becomes_a_durable_confirmed_risk_assumption_not_another_question():
    """The exact regression the user reported: a hedge that's already been
    asked about once must not repeat the identical question a third time —
    it must become a permanently-recorded, auto-confirmed risk assumption
    (visible in the exported deliverable) instead."""

    verdicts = [
        RequirementCoverageVerdict(
            category="double-booking prevention",
            status="escalate_as_risk",
            high_impact=True,
            evidence="dunno probably db handles it somehow",
            recommended_mitigation="Implement a unique constraint or row-level locking on (show_id, seat_id).",
        )
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, model = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    # No new question — the escalation replaces asking again, it doesn't add to it.
    assert gaps == []
    assert len(model.assumptions) == 1
    risk = model.assumptions[0]
    assert risk.resolved is True
    assert "FLAGGED RISK" in risk.text
    assert "double-booking prevention" in risk.text
    assert "unique constraint or row-level locking" in risk.text


@pytest.mark.asyncio
async def test_escalation_and_a_separate_unknown_category_coexist():
    verdicts = [
        RequirementCoverageVerdict(
            category="double-booking prevention",
            status="escalate_as_risk",
            high_impact=True,
            evidence="dunno probably db handles it",
            recommended_mitigation="Use a unique constraint on (show_id, seat_id).",
        ),
        RequirementCoverageVerdict(category="reporting/analytics", status="unknown", high_impact=False),
    ]
    provider = MockProvider()
    provider.register(RequirementCoverageOutput, RequirementCoverageOutput(requirements=verdicts))
    provider.register(RequirementCoverageCriticOutput, _passthrough_critic(verdicts))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    gaps, model = await assess_dynamic_requirement_coverage(_model_with_components(), gateway, meter)

    assert len(gaps) == 1
    assert "reporting/analytics" in gaps[0].description
    assert "double-booking prevention" not in gaps[0].description  # escalated, not re-asked
    assert len(model.assumptions) == 1
    assert model.assumptions[0].resolved is True

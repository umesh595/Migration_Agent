"""judge_review_node: an independent LLM-as-judge pass over the semantic critic's
own findings (PRD Decision Q7, overridden from v2 — see DECISIONS.md). Must never
block the refine loop even when the judge itself fails — it's an observability
signal, not a gate."""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import SemanticReviewJudgeOutput
from app.orchestration.nodes.review import judge_review_node
from app.schemas.findings import Finding, FindingSeverity, FindingSource
from app.schemas.migration_context import MigrationContext
from app.schemas.review_quality import ReviewQualityScore


def _context() -> MigrationContext:
    return MigrationContext(
        source_environment="on_prem",
        target_environment="cloud",
        target_platform_description="AWS EKS",
        downtime_tolerance="maintenance_window",
    )


def _judge_output(**overrides) -> SemanticReviewJudgeOutput:
    base = dict(
        relevance_score=80, specificity_score=75, actionability_score=70,
        context_awareness_score=85, overall_score=78, rationale="fine", flagged_issues=[],
    )
    base.update(overrides)
    return SemanticReviewJudgeOutput(**base)


@pytest.mark.asyncio
async def test_returns_noop_without_migration_context():
    provider = MockProvider()
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    result = await judge_review_node(
        {"findings": [], "migration_context": None, "refine_iteration": 0}, gateway=gateway, meter=meter
    )
    assert result == {}
    assert provider.calls == []


@pytest.mark.asyncio
async def test_scores_are_recorded_with_correct_iteration_and_finding_count():
    provider = MockProvider()
    provider.register(SemanticReviewJudgeOutput, _judge_output(overall_score=91))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    findings = [
        Finding(id="RULE-001-x", source=FindingSource.RULE, rule_id="RULE-001",
                severity=FindingSeverity.ERROR, message="rule finding"),
        Finding(id="LLM-0-0", source=FindingSource.LLM, severity=FindingSeverity.WARNING,
                message="llm finding one"),
        Finding(id="LLM-0-1", source=FindingSource.LLM, severity=FindingSeverity.INFO,
                message="llm finding two"),
    ]

    result = await judge_review_node(
        {"findings": findings, "migration_context": _context(), "refine_iteration": 2,
         "review_quality_history": []},
        gateway=gateway, meter=meter,
    )

    history = result["review_quality_history"]
    assert len(history) == 1
    score = history[0]
    assert score.iteration == 2
    assert score.evaluated_finding_count == 2, "only LLM-source findings are scored, not rule findings"
    assert score.overall_score == 91


@pytest.mark.asyncio
async def test_appends_to_existing_history_rather_than_overwriting():
    provider = MockProvider()
    provider.register(SemanticReviewJudgeOutput, _judge_output(overall_score=60))
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    prior = [
        ReviewQualityScore(
            iteration=0, evaluated_finding_count=1, relevance_score=50, specificity_score=50,
            actionability_score=50, context_awareness_score=50, overall_score=50, rationale="prior",
        )
    ]

    result = await judge_review_node(
        {"findings": [], "migration_context": _context(), "refine_iteration": 1, "review_quality_history": prior},
        gateway=gateway, meter=meter,
    )

    history = result["review_quality_history"]
    assert len(history) == 2
    assert history[0].iteration == 0
    assert history[1].iteration == 1
    assert history[1].overall_score == 60


@pytest.mark.asyncio
async def test_judge_failure_is_non_blocking():
    """No mock response registered -> MockProvider raises StructuredOutputError.
    The judge is observability, not a gate: this must degrade to a no-op, never
    propagate and break the review stage."""

    provider = MockProvider()  # nothing registered
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    result = await judge_review_node(
        {"findings": [], "migration_context": _context(), "refine_iteration": 0},
        gateway=gateway, meter=meter,
    )
    assert result == {}

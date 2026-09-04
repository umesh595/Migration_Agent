"""Discovery Confidence Score: deliberately deterministic, computed straight
from fields the model actually has — never an LLM guess, never faked."""

from __future__ import annotations

from app.core.discovery_confidence import compute_discovery_confidence
from app.schemas.architecture import (
    ArchitectureModel,
    Assumption,
    AssumptionStatus,
    Component,
    Dependency,
    DependencyKind,
    Environment,
    OpenQuestion,
    WorkloadType,
)


def test_blank_model_is_zero_percent_and_not_ready():
    result = compute_discovery_confidence(ArchitectureModel())
    assert result.completeness_percent == 0
    assert result.ready_for_planning is False


def test_a_lone_unconnected_component_is_sparse_context_not_complete():
    """A single component with nothing else recorded is exactly what
    gap_analyzer.SPARSE_ARCHITECTURE_CONTEXT exists to catch — this isn't a
    special case for the confidence score, it reads the same deterministic
    gaps discovery itself asks about every turn."""

    model = ArchitectureModel(
        components=[
            Component(
                id="api", name="API", workload_type=WorkloadType.API_SERVICE,
                environment=Environment.CLOUD, criticality="tier-1",
            )
        ]
    )
    result = compute_discovery_confidence(model)
    assert result.blocking_unknowns >= 1
    assert result.ready_for_planning is False


def test_orphan_component_counts_as_blocking_and_incomplete():
    model = ArchitectureModel(
        components=[
            Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD, criticality="tier-1"),
            Component(id="db", name="DB", workload_type=WorkloadType.DATABASE, environment=Environment.CLOUD, criticality="tier-1"),
        ]
    )
    result = compute_discovery_confidence(model)
    assert result.blocking_unknowns >= 1  # both are orphans (no dependency between them)
    assert result.completeness_percent < 100
    assert result.ready_for_planning is False


def test_dependency_between_the_only_two_components_removes_the_orphan_gap():
    model = ArchitectureModel(
        components=[
            Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD, criticality="tier-1"),
            Component(id="db", name="DB", workload_type=WorkloadType.DATABASE, environment=Environment.CLOUD, criticality="tier-1"),
        ],
        dependencies=[Dependency(id="api->db", source_id="api", target_id="db", kind=DependencyKind.DATA_WRITE)],
    )
    result = compute_discovery_confidence(model)
    assert result.completeness_percent == 100
    assert result.ready_for_planning is True


def test_open_llm_assumption_lowers_completeness_but_is_not_by_itself_blocking():
    """UNCONFIRMED_ASSUMPTION sits below the blocking floor (gap_analyzer
    priority 50 — a lower-stakes gap than an open question, sparse context, or
    orphan component) — it should still cost completeness percentage, but a
    single low-stakes unconfirmed guess shouldn't by itself stop the model
    from being ready for planning the way an actual open question would."""

    model = ArchitectureModel(
        components=[
            Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD, criticality="tier-1"),
            Component(id="db", name="DB", workload_type=WorkloadType.DATABASE, environment=Environment.CLOUD, criticality="tier-1"),
        ],
        dependencies=[Dependency(id="api->db", source_id="api", target_id="db", kind=DependencyKind.DATA_WRITE)],
        assumptions=[Assumption(id="A1", text="Probably stateless", raised_by="llm", status=AssumptionStatus.OPEN)],
    )
    result = compute_discovery_confidence(model)
    assert result.completeness_percent < 100
    assert result.blocking_unknowns == 0
    assert result.ready_for_planning is True


def test_flagged_risk_assumption_counts_as_high_risk_and_blocks_readiness():
    model = ArchitectureModel(
        components=[Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD, criticality="tier-1")],
        assumptions=[
            Assumption(
                id="A1", text="FLAGGED RISK — unconfirmed by user: payment idempotency.",
                raised_by="llm", status=AssumptionStatus.CONFIRMED,
            )
        ],
    )
    result = compute_discovery_confidence(model)
    assert result.high_risk_assumptions == 1
    assert result.ready_for_planning is False


def test_unresolved_open_question_is_blocking():
    model = ArchitectureModel(
        components=[Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD, criticality="tier-1")],
        open_questions=[OpenQuestion(id="Q1", text="What does this connect to?")],
    )
    result = compute_discovery_confidence(model)
    assert result.blocking_unknowns >= 1
    assert result.ready_for_planning is False

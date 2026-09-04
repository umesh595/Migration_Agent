"""Evidence Ledger 'used_by' computation — deliberately deterministic code, not
an LLM judgment, and deliberately recomputed fresh rather than stored (see
app.core.evidence's module docstring for why a stored value would be worse
than none)."""

from __future__ import annotations

from app.core.evidence import assumption_used_by, dependency_used_by
from app.schemas.architecture import (
    ArchitectureModel,
    Assumption,
    AssumptionStatus,
    Component,
    Dependency,
    DependencyKind,
    ModelStatus,
    WorkloadType,
)


def _model(**kwargs) -> ArchitectureModel:
    defaults = {
        "components": [
            Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE, criticality="tier-1"),
            Component(id="db", name="DB", workload_type=WorkloadType.DATABASE),
        ]
    }
    defaults.update(kwargs)
    return ArchitectureModel(**defaults)


def test_open_assumption_feeds_nothing_yet():
    model = _model()
    assumption = Assumption(id="A1", text="guess", raised_by="llm", status=AssumptionStatus.OPEN)
    assert assumption_used_by(assumption, model) == []


def test_rejected_assumption_feeds_nothing():
    model = _model()
    assumption = Assumption(id="A1", text="wrong guess", raised_by="llm", status=AssumptionStatus.REJECTED)
    assert assumption_used_by(assumption, model) == []


def test_confirmed_assumption_with_criticality_related_component_feeds_criticality_classification():
    model = _model()
    assumption = Assumption(
        id="A1", text="fact", raised_by="user", status=AssumptionStatus.CONFIRMED, related_component_ids=["api"]
    )
    used = assumption_used_by(assumption, model)
    assert "gap analysis" in used
    assert "component planning" in used
    assert "criticality classification" in used
    assert "migration wave sequencing" not in used  # model not accepted yet


def test_confirmed_assumption_gains_wave_sequencing_once_model_is_accepted():
    model = _model(status=ModelStatus.ACCEPTED)
    assumption = Assumption(
        id="A1", text="fact", raised_by="user", status=AssumptionStatus.CONFIRMED, related_component_ids=["db"]
    )
    used = assumption_used_by(assumption, model)
    assert "migration wave sequencing" in used
    assert "criticality classification" not in used  # db has no criticality set


def test_dependency_used_by_before_and_after_acceptance():
    dependency = Dependency(id="api->db", source_id="api", target_id="db", kind=DependencyKind.DATA_WRITE)

    before = dependency_used_by(dependency, _model())
    assert before == ["dependency graph"]

    after = dependency_used_by(dependency, _model(status=ModelStatus.ACCEPTED))
    assert "migration wave sequencing" in after
    assert "coexistence and rollback planning" in after

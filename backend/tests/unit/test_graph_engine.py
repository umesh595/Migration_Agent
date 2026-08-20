import pytest

from app.core.graph_engine import (
    CapacityExceededError,
    check_scale_envelope,
    compute_cross_wave_dependencies,
    compute_impact,
    compute_sequence,
)
from app.schemas.architecture import ArchitectureModel, Component, Dependency


def _component(cid: str) -> Component:
    return Component(id=cid, name=cid, workload_type="other")


def test_linear_chain_sequences_dependency_first():
    # web -> api -> db  (web depends on api depends on db)
    model = ArchitectureModel(
        components=[_component("web"), _component("api"), _component("db")],
        dependencies=[
            Dependency(id="d1", source_id="web", target_id="api", kind="sync_call"),
            Dependency(id="d2", source_id="api", target_id="db", kind="data_read"),
        ],
    )
    waves = compute_sequence(model)

    assert [w.component_ids for w in waves] == [["db"], ["api"], ["web"]]


def test_independent_components_can_share_a_wave_or_be_ordered_but_never_violate_edges():
    model = ArchitectureModel(
        components=[_component("a"), _component("b"), _component("c")],
        dependencies=[Dependency(id="d1", source_id="a", target_id="c", kind="sync_call")],
    )
    waves = compute_sequence(model)
    wave_of = {cid: w.index for w in waves for cid in w.component_ids}

    assert wave_of["a"] > wave_of["c"]  # a depends on c, so c must move first
    assert "b" in wave_of  # never dropped


def test_mutual_dependency_cycle_grouped_into_same_wave():
    model = ArchitectureModel(
        components=[_component("x"), _component("y")],
        dependencies=[
            Dependency(id="d1", source_id="x", target_id="y", kind="sync_call"),
            Dependency(id="d2", source_id="y", target_id="x", kind="sync_call"),
        ],
    )
    waves = compute_sequence(model)

    assert len(waves) == 1
    assert set(waves[0].component_ids) == {"x", "y"}
    assert len(waves[0].coexistence_groups) == 1


def test_cross_wave_dependencies_detected_for_multi_wave_span():
    model = ArchitectureModel(
        components=[_component("web"), _component("api"), _component("db")],
        dependencies=[
            Dependency(id="d1", source_id="web", target_id="api", kind="sync_call"),
            Dependency(id="d2", source_id="api", target_id="db", kind="data_read"),
            Dependency(id="d3", source_id="web", target_id="db", kind="data_read"),  # spans 2 waves
        ],
    )
    waves = compute_sequence(model)
    crossings = compute_cross_wave_dependencies(model, waves)

    assert any(set(c.component_ids) == {"web", "db"} for c in crossings)


def test_impact_analysis_upstream_downstream():
    model = ArchitectureModel(
        components=[_component("web"), _component("api"), _component("db")],
        dependencies=[
            Dependency(id="d1", source_id="web", target_id="api", kind="sync_call"),
            Dependency(id="d2", source_id="api", target_id="db", kind="data_read"),
        ],
    )
    impact = compute_impact(model, "api")
    assert impact["upstream"] == ["web"]
    assert impact["downstream"] == ["db"]


def test_scale_envelope_raises_when_exceeded():
    model = ArchitectureModel(components=[_component(f"c{i}") for i in range(5)])
    with pytest.raises(CapacityExceededError):
        check_scale_envelope(model, max_components=4, max_dependencies=200)

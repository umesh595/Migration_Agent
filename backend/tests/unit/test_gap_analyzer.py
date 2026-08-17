from app.core.gap_analyzer import GapCategory, analyze_gaps, top_gaps
from app.schemas.architecture import ArchitectureModel, Component, OpenQuestion


def test_open_questions_always_outrank_other_gap_categories():
    model = ArchitectureModel(
        components=[Component(id="a", name="A", workload_type="other", technology="X", criticality="tier-1")],
        open_questions=[OpenQuestion(id="q1", text="what does A talk to?")],
    )
    gaps = analyze_gaps(model)
    assert gaps[0].category == GapCategory.OPEN_QUESTION


def test_resolved_open_questions_are_not_gaps():
    model = ArchitectureModel(open_questions=[OpenQuestion(id="q1", text="resolved already", resolved=True)])
    gaps = analyze_gaps(model)
    assert all(g.category != GapCategory.OPEN_QUESTION for g in gaps)


def test_single_component_is_not_flagged_as_orphan():
    model = ArchitectureModel(
        components=[
            Component(id="a", name="A", workload_type="other", technology="X", criticality="tier-1", environment="cloud")
        ]
    )
    gaps = analyze_gaps(model)
    assert all(g.category != GapCategory.ORPHAN_COMPONENT for g in gaps)


def test_top_gaps_respects_limit():
    model = ArchitectureModel(
        components=[Component(id=f"c{i}", name=f"c{i}", workload_type="other") for i in range(10)]
    )
    assert len(top_gaps(model, n=3)) == 3

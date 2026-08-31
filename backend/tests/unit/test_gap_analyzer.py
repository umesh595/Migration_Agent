from app.core.gap_analyzer import GapCategory, analyze_gaps, top_gaps
from app.core.request_intelligence import classify_user_request
from app.orchestration.nodes.discovery import _adapt_gaps_to_latest_user_message
from app.schemas.architecture import ArchitectureModel, Assumption, Component, OpenQuestion


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


def test_single_component_without_architecture_details_gets_intake_gap():
    model = ArchitectureModel(
        components=[
            Component(
                id="employee_allocation_tracker",
                name="Employee Allocation Tracker",
                workload_type="other",
                criticality="tier-1",
                environment="cloud",
            )
        ]
    )

    gaps = analyze_gaps(model)
    sparse_gaps = [g for g in gaps if g.category == GapCategory.SPARSE_ARCHITECTURE_CONTEXT]

    assert len(sparse_gaps) == 1
    assert "user access channel" in sparse_gaps[0].description
    assert "authentication/roles" in sparse_gaps[0].description
    assert "target cloud" in sparse_gaps[0].description


def test_greenfield_answer_does_not_repeat_current_hosting_question():
    model = ArchitectureModel(
        components=[
            Component(
                id="employee_allocation_tracker",
                name="Employee Allocation Tracker",
                workload_type="other",
                criticality="tier-1",
            )
        ]
    )
    user_message = "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs"
    gaps = analyze_gaps(model)

    adapted = _adapt_gaps_to_latest_user_message(
        gaps,
        {
            "user_message": user_message,
            "request_impact": classify_user_request(user_message),
        },
    )

    assert all(g.category != GapCategory.MISSING_ENVIRONMENT for g in adapted)
    sparse_gap = next(g for g in adapted if g.category == GapCategory.SPARSE_ARCHITECTURE_CONTEXT)
    assert "Do not ask where the current app is hosted" in sparse_gap.description
    assert "core workflows" in sparse_gap.description


def test_sparse_intake_question_does_not_fire_once_real_assumptions_exist_even_with_zero_components():
    """Regression: the LLM sometimes keeps representing real answers as
    assumptions without ever committing to a component. Previously the sparse
    gap fired on component/dependency COUNT alone, so a model stuck at 0
    components repeated the entire original intake question forever no
    matter how much real detail had accumulated as assumptions. Once any
    assumption exists there is real substance — analyze_gaps must stop
    treating the model as sparse (the follow-up itself is now a separate,
    LLM-driven classification — see tests/unit/test_requirement_coverage.py —
    not something this deterministic function decides)."""

    model = ArchitectureModel(
        components=[],
        assumptions=[
            Assumption(
                id="A1",
                text="System accepts user data; books movie shows; hosted on GCP.",
                raised_by="llm",
                resolved=True,
            )
        ],
    )

    gaps = analyze_gaps(model)

    assert all(g.category != GapCategory.SPARSE_ARCHITECTURE_CONTEXT for g in gaps)


def test_top_gaps_respects_limit():
    model = ArchitectureModel(
        components=[Component(id=f"c{i}", name=f"c{i}", workload_type="other") for i in range(10)]
    )
    assert len(top_gaps(model, n=3)) == 3


def test_missing_criticality_is_one_grouped_gap_not_one_per_component():
    """The exact production bug this guards: 13 components with no stated
    criticality used to produce 13 near-identical 'how critical is X?' gaps —
    mechanical schema-filling instead of one grouped question."""

    model = ArchitectureModel(
        components=[
            Component(id=f"c{i}", name=f"Component {i}", workload_type="other", technology="X", environment="cloud")
            for i in range(13)
        ]
    )
    gaps = analyze_gaps(model)
    criticality_gaps = [g for g in gaps if g.category == GapCategory.MISSING_CRITICALITY]

    assert len(criticality_gaps) == 1
    assert set(criticality_gaps[0].related_component_ids) == {f"c{i}" for i in range(13)}


def test_missing_environment_is_one_grouped_gap():
    model = ArchitectureModel(
        components=[
            Component(id="a", name="A", workload_type="other", criticality="tier-1"),
            Component(id="b", name="B", workload_type="other", criticality="tier-1"),
        ]
    )
    gaps = analyze_gaps(model)

    env_gaps = [g for g in gaps if g.category == GapCategory.MISSING_ENVIRONMENT]
    assert len(env_gaps) == 1 and set(env_gaps[0].related_component_ids) == {"a", "b"}


def test_missing_technology_is_never_a_gap():
    """`technology` has no downstream consumer (nothing in planning/review reads
    it) — asking about it is a low-value 'fill every field' question, not a
    migration-relevant one, so it must never surface as a gap at all, unlike
    criticality/environment which do have real downstream consequences."""

    model = ArchitectureModel(
        components=[Component(id="a", name="A", workload_type="other", criticality="tier-1", environment="cloud")]
    )
    gaps = analyze_gaps(model)
    assert not any(g.category.value == "missing_technology" for g in gaps)


def test_orphan_components_are_one_grouped_gap():
    model = ArchitectureModel(
        components=[
            Component(id="a", name="A", workload_type="other", technology="X", criticality="tier-1", environment="cloud"),
            Component(id="b", name="B", workload_type="other", technology="X", criticality="tier-1", environment="cloud"),
            Component(id="c", name="C", workload_type="other", technology="X", criticality="tier-1", environment="cloud"),
        ]
    )
    gaps = analyze_gaps(model)
    orphan_gaps = [g for g in gaps if g.category == GapCategory.ORPHAN_COMPONENT]

    assert len(orphan_gaps) == 1
    assert set(orphan_gaps[0].related_component_ids) == {"a", "b", "c"}


def test_component_with_criticality_already_set_is_not_flagged():
    model = ArchitectureModel(
        components=[
            Component(id="a", name="A", workload_type="other", technology="X", environment="cloud", criticality="tier-1"),
            Component(id="b", name="B", workload_type="other", technology="X", environment="cloud", criticality=None),
        ],
        dependencies=[],
    )
    gaps = analyze_gaps(model)
    criticality_gaps = [g for g in gaps if g.category == GapCategory.MISSING_CRITICALITY]

    assert len(criticality_gaps) == 1
    assert criticality_gaps[0].related_component_ids == ["b"]

from app.core.gap_analyzer import GapCategory, analyze_gaps, top_gaps
from app.core.request_intelligence import classify_user_request
from app.orchestration.nodes.discovery import _adapt_gaps_to_latest_user_message
from app.schemas.architecture import ArchitectureModel, Assumption, Component, Dependency, OpenQuestion


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


def test_discovery_keeps_asking_until_basic_application_requirements_are_covered():
    model = ArchitectureModel(
        components=[
            Component(
                id="iot_platform",
                name="IoT Platform",
                workload_type="data_pipeline",
                description="Ingests MQTT telemetry",
                environment="cloud",
                criticality="tier-1",
            ),
            Component(
                id="fastapi_backend",
                name="FastAPI Backend",
                workload_type="api_service",
                environment="cloud",
                criticality="tier-1",
            ),
            Component(
                id="postgres_database",
                name="Postgres Database",
                workload_type="database",
                environment="cloud",
                criticality="tier-1",
            ),
        ],
        dependencies=[
            Dependency(
                id="iot_platform->fastapi_backend:async_call",
                source_id="iot_platform",
                target_id="fastapi_backend",
                kind="async_call",
            )
        ],
    )

    gaps = analyze_gaps(model)
    basic_gaps = [g for g in gaps if g.category == GapCategory.BASIC_APP_REQUIREMENTS]

    assert len(basic_gaps) == 1
    assert "authentication" in basic_gaps[0].description
    assert "reporting" in basic_gaps[0].description


def test_basic_application_requirements_clear_when_positive_or_negative_facts_are_known():
    model = ArchitectureModel(
        components=[
            Component(
                id="web_frontend",
                name="Web Frontend",
                workload_type="web_service",
                description="Users access the application through a web UI",
                environment="cloud",
                criticality="tier-1",
            ),
            Component(
                id="backend_api",
                name="Backend API",
                workload_type="api_service",
                environment="cloud",
                criticality="tier-1",
            ),
            Component(
                id="postgres_database",
                name="Postgres Database",
                workload_type="database",
                environment="cloud",
                criticality="tier-1",
            ),
        ],
        dependencies=[
            Dependency(
                id="web_frontend->backend_api:sync_call",
                source_id="web_frontend",
                target_id="backend_api",
                kind="sync_call",
            ),
            Dependency(
                id="backend_api->postgres_database:data_write",
                source_id="backend_api",
                target_id="postgres_database",
                kind="data_write",
            ),
        ],
        assumptions=[
            Assumption(
                id="A1",
                text=(
                    "Users log in with SSO roles. No external integrations or payments. No async jobs or "
                    "event queues. No reporting exports. Monitoring logs, audit logging, encryption, PII "
                    "controls, 7 years retention, 50k users and millions of records are required."
                ),
                raised_by="user",
                resolved=True,
            )
        ],
    )

    gaps = analyze_gaps(model)

    assert all(g.category != GapCategory.BASIC_APP_REQUIREMENTS for g in gaps)


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

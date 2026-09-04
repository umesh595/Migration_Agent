"""Coverage for the assumption / open-question patch paths — the mechanism behind
'uncertainty as data' (technique #12). These are how an unknown surfaces instead of
being silently filled in, so their validation matters as much as the structural ops."""

from app.core.gap_analyzer import GapCategory, top_gaps
from app.core.patch_applier import apply_patch_set
from app.orchestration.nodes.discovery import (
    resolve_dependency_open_questions_from_short_answer,
    resolve_environment_open_questions_from_short_answer,
)
from app.schemas.architecture import (
    ArchitectureModel,
    Assumption,
    AssumptionStatus,
    Component,
    DependencyKind,
    Environment,
    OpenQuestion,
)
from app.schemas.patches import (
    AddAssumptionPatch,
    AddOpenQuestionPatch,
    ConfirmAssumptionPatch,
    PatchOutcome,
    PatchSet,
    ResolveOpenQuestionPatch,
)


def _model() -> ArchitectureModel:
    return ArchitectureModel(
        components=[Component(id="api", name="API", workload_type="api_service")],
        open_questions=[OpenQuestion(id="Q1", text="Which database does the API use?", related_component_ids=["api"])],
    )


def test_assumption_is_recorded_and_attributed_to_the_llm():
    model = _model()
    patch_set = PatchSet(
        patches=[AddAssumptionPatch(text="The API is stateless", related_component_ids=["api"])],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].text == "The API is stateless"
    assert new_model.assumptions[0].raised_by == "llm"


def test_empty_assumption_text_rejected():
    _, results = apply_patch_set(_model(), PatchSet(patches=[AddAssumptionPatch(text="   ")], narration=""))
    assert results[0].outcome == PatchOutcome.REJECTED
    assert "empty" in results[0].reason


def test_assumption_referencing_unknown_component_rejected():
    patch_set = PatchSet(
        patches=[AddAssumptionPatch(text="Ghost is stateless", related_component_ids=["ghost"])], narration=""
    )
    _, results = apply_patch_set(_model(), patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "ghost" in results[0].reason


def test_resolving_an_open_question_marks_it_resolved_and_records_user_answer():
    patch_set = PatchSet(
        patches=[ResolveOpenQuestionPatch(question_id="Q1", resolution_text="The API uses Postgres 14")],
        narration="",
    )
    new_model, results = apply_patch_set(_model(), patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.open_questions[0].resolved is True
    # A resolved question becomes a user-attributed assumption, so the answer isn't lost.
    assert new_model.assumptions[-1].raised_by == "user"
    assert "Postgres 14" in new_model.assumptions[-1].text


def test_standalone_short_answer_resolves_dependency_open_question():
    model = ArchitectureModel(
        components=[Component(id="reporting", name="Reporting Service", workload_type="api_service")],
        open_questions=[
            OpenQuestion(
                id="Q1",
                text="Are there any dependencies or connections for Reporting Service that we have not captured?",
                related_component_ids=["reporting"],
            )
        ],
    )

    patch_set = resolve_dependency_open_questions_from_short_answer(
        model, "they truly operate independently within the current architecture", PatchSet(patches=[], narration="")
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.open_questions[0].resolved is True
    assert "no missing connections" in new_model.assumptions[-1].text


def test_standalone_short_answer_does_not_resolve_unrelated_open_question():
    model = ArchitectureModel(
        components=[Component(id="api", name="API", workload_type="api_service")],
        open_questions=[
            OpenQuestion(
                id="Q1",
                text="Is changing the API from FastAPI to Java a firm target decision?",
                related_component_ids=["api"],
            )
        ],
    )

    patch_set = resolve_dependency_open_questions_from_short_answer(
        model, "standalone", PatchSet(patches=[], narration="")
    )

    assert patch_set.patches == []


def test_yes_to_previous_dependency_hypothesis_adds_iot_backend_database_edges():
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
        ]
    )
    previous = (
        "I'd expect the IoT Platform to send telemetry data to the FastAPI Backend for processing, "
        "and the FastAPI Backend to interact with the Postgres Database for data storage. Can you confirm?"
    )

    patch_set = resolve_dependency_open_questions_from_short_answer(
        model,
        "YES",
        PatchSet(patches=[], narration=""),
        previous_agent_message=previous,
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(result.outcome == PatchOutcome.APPLIED for result in results)
    assert any(
        dependency.source_id == "iot_platform"
        and dependency.target_id == "fastapi_backend"
        and dependency.kind == DependencyKind.ASYNC_CALL
        for dependency in new_model.dependencies
    )
    assert any(
        dependency.source_id == "fastapi_backend"
        and dependency.target_id == "postgres_database"
        and dependency.kind == DependencyKind.DATA_WRITE
        for dependency in new_model.dependencies
    )
    assert any(
        dependency.source_id == "fastapi_backend"
        and dependency.target_id == "postgres_database"
        and dependency.kind == DependencyKind.DATA_READ
        for dependency in new_model.dependencies
    )


def test_gcp_short_answer_resolves_environment_question_and_updates_components():
    model = ArchitectureModel(
        components=[
            Component(id="iot_device", name="IOT Device", workload_type="other"),
            Component(id="datalake", name="Datalake", workload_type="storage"),
        ],
        open_questions=[
            OpenQuestion(
                id="Q1",
                text="Is the current hosting model for the IOT system on-prem, cloud, or hybrid?",
                related_component_ids=["iot_device", "datalake"],
            )
        ],
    )

    patch_set = resolve_environment_open_questions_from_short_answer(
        model, "ON GCP", PatchSet(patches=[], narration="")
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(result.outcome == PatchOutcome.APPLIED for result in results)
    assert new_model.open_questions[0].resolved is True
    assert all(component.environment == Environment.CLOUD for component in new_model.components)


def test_yes_confirms_gcp_environment_assumption_and_clears_unknown_environment():
    model = ArchitectureModel(
        components=[Component(id="api", name="API", workload_type="api_service")],
        assumptions=[
            Assumption(
                id="A1",
                text="The system is hosted on GCP.",
                raised_by="llm",
                related_component_ids=["api"],
                status=AssumptionStatus.OPEN,
            )
        ],
    )

    patch_set = resolve_environment_open_questions_from_short_answer(
        model, "YES", PatchSet(patches=[], narration="")
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(result.outcome == PatchOutcome.APPLIED for result in results)
    assert new_model.assumptions[0].status == AssumptionStatus.CONFIRMED
    assert new_model.components[0].environment == Environment.CLOUD


def test_resolving_unknown_question_rejected():
    patch_set = PatchSet(patches=[ResolveOpenQuestionPatch(question_id="Q99", resolution_text="x")], narration="")
    _, results = apply_patch_set(_model(), patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "Q99" in results[0].reason


def test_resolving_an_already_resolved_question_rejected():
    model = _model()
    model.open_questions[0].resolved = True
    patch_set = PatchSet(patches=[ResolveOpenQuestionPatch(question_id="Q1", resolution_text="x")], narration="")
    _, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "already resolved" in results[0].reason


def test_unresolved_llm_assumption_is_a_gap_but_resolved_one_is_not():
    """The exact production bug this guards: an LLM-raised assumption with no way
    to be confirmed used to be flagged by GapAnalyzer forever, since 'yes, that's
    correct' had no patch to attach to and the same question got re-asked every
    turn indefinitely."""

    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm"))

    gaps = top_gaps(model, n=10)
    assert any(g.category == GapCategory.UNCONFIRMED_ASSUMPTION for g in gaps)

    confirmed = model.model_copy(deep=True)
    confirmed.assumptions[0].status = AssumptionStatus.CONFIRMED
    gaps_after = top_gaps(confirmed, n=10)
    assert not any(g.category == GapCategory.UNCONFIRMED_ASSUMPTION for g in gaps_after)


def test_confirm_assumption_marks_it_resolved():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm"))

    new_model, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1")], narration="")
    )

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].status == AssumptionStatus.CONFIRMED
    assert new_model.assumptions[0].text == "The API is stateless"


def test_confirm_assumption_can_correct_the_wording():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm"))

    new_model, results = apply_patch_set(
        model,
        PatchSet(
            patches=[ConfirmAssumptionPatch(assumption_id="A1", updated_text="The API is stateless except for caching")],
            narration="",
        ),
    )

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].status == AssumptionStatus.CONFIRMED
    assert new_model.assumptions[0].text == "The API is stateless except for caching"


def test_reject_assumption_marks_it_rejected_not_confirmed():
    """The explicit-rejection counterpart to confirm: the user said a guess was
    simply wrong, with no replacement given — this must land as REJECTED, not
    CONFIRMED (a rejected guess is not a fact) and must not silently vanish
    from the evidence ledger (the audit trail should show what was tried)."""

    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm"))

    new_model, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1", rejected=True)], narration="")
    )

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].status == AssumptionStatus.REJECTED
    assert new_model.assumptions[0].text == "The API is stateless"  # text preserved, only status changes

    # A rejected assumption is no longer a pending gap (it's a closed, if negative,
    # answer) — same contract as a confirmed one.
    gaps = top_gaps(new_model, n=10)
    assert not any(g.category == GapCategory.UNCONFIRMED_ASSUMPTION for g in gaps)


def test_reject_already_resolved_assumption_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm", status=AssumptionStatus.CONFIRMED))

    _, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1", rejected=True)], narration="")
    )

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "already confirmed" in results[0].reason


def test_reject_unknown_assumption_rejected():
    model = _model()
    _, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A99", rejected=True)], narration="")
    )

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "A99" in results[0].reason


def test_confirm_assumption_rejects_setting_both_updated_text_and_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm"))

    _, results = apply_patch_set(
        model,
        PatchSet(
            patches=[ConfirmAssumptionPatch(assumption_id="A1", updated_text="y", rejected=True)], narration=""
        ),
    )

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "cannot set updated_text when rejecting" in results[0].reason


def test_confirm_unknown_assumption_rejected():
    model = _model()
    _, results = apply_patch_set(model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A99")], narration=""))

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "A99" in results[0].reason


def test_confirm_already_resolved_assumption_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm", status=AssumptionStatus.CONFIRMED))

    _, results = apply_patch_set(model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1")], narration=""))

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "already confirmed" in results[0].reason


def test_confirm_assumption_with_blank_updated_text_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm"))

    _, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1", updated_text="   ")], narration="")
    )

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "empty" in results[0].reason


def test_add_open_question_records_it_unresolved_and_it_becomes_a_gap():
    """The discuss-agent mechanism (see INGEST_PATCHES's 'discuss before adding
    genuinely new, unscoped capability' rule): a proposal the ingestion LLM isn't
    confident about gets recorded here INSTEAD of silently added, so the next
    turn's gap analysis can surface it — same pattern as an LLM-raised assumption,
    but living in open_questions since it's a proposal awaiting a decision, not an
    inferred fact awaiting confirmation."""

    model = _model()
    patch_set = PatchSet(
        patches=[AddOpenQuestionPatch(text="Do we actually need a caching layer here?", related_component_ids=["api"])],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert results[0].outcome == PatchOutcome.APPLIED
    added = new_model.open_questions[-1]
    assert added.text == "Do we actually need a caching layer here?"
    assert added.resolved is False
    assert added.related_component_ids == ["api"]

    gaps = top_gaps(new_model, n=10)
    assert any(g.category == GapCategory.OPEN_QUESTION and g.description == added.text for g in gaps)


def test_add_open_question_with_blank_text_rejected():
    _, results = apply_patch_set(_model(), PatchSet(patches=[AddOpenQuestionPatch(text="   ")], narration=""))
    assert results[0].outcome == PatchOutcome.REJECTED
    assert "empty" in results[0].reason


def test_add_open_question_referencing_unknown_component_rejected():
    patch_set = PatchSet(patches=[AddOpenQuestionPatch(text="x", related_component_ids=["ghost"])], narration="")
    _, results = apply_patch_set(_model(), patch_set)

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "ghost" in results[0].reason


def test_confirmed_addition_carries_the_resolution_reason_onto_the_audit_record():
    """The discuss algorithm's auditable-record requirement: 'record what changed
    and why.' A structural patch applied in the same turn as a
    resolve_open_question carries that resolution's text as its own audit reason,
    so the AuditTrailPanel shows WHY a change was made, not just what."""

    from app.schemas.patches import AddComponentPatch, AddDependencyPatch

    model = _model()
    model.open_questions.append(
        OpenQuestion(id="Q2", text="Do we need a Redis cache for the API?", related_component_ids=["api"])
    )
    patch_set = PatchSet(
        patches=[
            ResolveOpenQuestionPatch(question_id="Q2", resolution_text="Yes, add it — read latency is too high"),
            AddComponentPatch(id="redis_cache", name="Redis Cache", workload_type="cache"),
            AddDependencyPatch(source_id="api", target_id="redis_cache", kind="data_read"),
        ],
        narration="",
    )
    _, results = apply_patch_set(model, patch_set)

    add_component_result = next(r for r in results if r.patch.op == "add_component")
    add_dependency_result = next(r for r in results if r.patch.op == "add_dependency")
    assert add_component_result.reason == "Yes, add it — read latency is too high"
    assert add_dependency_result.reason == "Yes, add it — read latency is too high"


def test_addition_without_a_confirmation_in_the_same_turn_has_no_reason():
    """A directly-justified addition (no discuss question involved) shouldn't
    fabricate a reason — None here is correct, not a bug."""

    from app.schemas.patches import AddComponentPatch

    model = _model()
    patch_set = PatchSet(patches=[AddComponentPatch(id="cache", name="Cache", workload_type="cache")], narration="")
    _, results = apply_patch_set(model, patch_set)

    assert results[0].reason is None


def test_resolving_open_question_then_adding_component_in_same_turn_works():
    """The confirmation half of the discuss flow: once the user confirms a
    previously-raised open question, the SAME turn both resolves it and adds
    whatever was being discussed — this is just ordinary patch sequencing
    (ResolveOpenQuestionPatch + AddComponentPatch in one PatchSet), verified here
    end-to-end so the two ops are confirmed to compose correctly."""

    from app.schemas.patches import AddComponentPatch, AddDependencyPatch

    model = _model()
    model.open_questions.append(
        OpenQuestion(id="Q2", text="Do we need a Redis cache for the API?", related_component_ids=["api"])
    )
    patch_set = PatchSet(
        patches=[
            ResolveOpenQuestionPatch(question_id="Q2", resolution_text="Yes, add it — read latency is too high"),
            AddComponentPatch(id="redis_cache", name="Redis Cache", workload_type="cache", criticality="tier-2"),
            AddDependencyPatch(source_id="api", target_id="redis_cache", kind="data_read"),
        ],
        narration="",
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(r.outcome == PatchOutcome.APPLIED for r in results)
    assert next(q for q in new_model.open_questions if q.id == "Q2").resolved is True
    assert new_model.get_component("redis_cache") is not None
    assert any(d.source_id == "api" and d.target_id == "redis_cache" for d in new_model.dependencies)

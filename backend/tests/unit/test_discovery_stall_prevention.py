"""Regression coverage for two live-reported stalls: a target/downtime answer
that should close the sparse-architecture intake question but didn't, and a
"just give it, proceed with a draft" command that got misclassified as a terse
confirmation and produced the exact same question again instead of drafting."""

from app.core.gap_analyzer import GapCategory, top_gaps
from app.core.patch_applier import apply_patch_set
from app.core.request_intelligence import RequestIntent, classify_user_request
from app.orchestration.nodes.discovery import (
    _adapt_gaps_to_latest_user_message,
    draft_minimal_architecture_when_user_says_proceed,
    model_has_confirmed_greenfield_fact,
    resolve_environment_open_questions_from_short_answer,
    resolve_sparse_intake_open_question_from_target_context_answer,
)
from app.schemas.architecture import ArchitectureModel, Assumption, Component, OpenQuestion, WorkloadType
from app.schemas.patches import AddComponentPatch, PatchOp, PatchSet


def test_target_context_answer_is_classified_as_target_planning_not_current_fact():
    impact = classify_user_request(
        "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs"
    )
    assert impact.intent == RequestIntent.TARGET_PLANNING
    assert impact.should_mutate_source is False


def test_target_context_answer_resolves_the_sparse_intake_open_question():
    model = ArchitectureModel(
        components=[Component(id="tracker", name="Employee Allocation Tracker", workload_type=WorkloadType.OTHER)],
        open_questions=[OpenQuestion(id="oq1", text="What does this run on today?", related_component_ids=["tracker"])],
    )
    impact = classify_user_request(
        "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs"
    )

    result = resolve_sparse_intake_open_question_from_target_context_answer(
        model, "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs",
        impact, PatchSet(patches=[], narration=""),
    )

    resolve_patches = [p for p in result.patches if p.op == PatchOp.RESOLVE_OPEN_QUESTION]
    assert len(resolve_patches) == 1
    assert resolve_patches[0].question_id == "oq1"
    assert "AWS" in resolve_patches[0].resolution_text
    assert "4hrs" in resolve_patches[0].resolution_text
    assumption_patches = [p for p in result.patches if p.op == PatchOp.ADD_ASSUMPTION]
    assert len(assumption_patches) == 1


def test_target_context_backup_is_a_noop_once_the_model_has_real_detail():
    model = ArchitectureModel(
        components=[
            Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE),
            Component(id="db", name="DB", workload_type=WorkloadType.DATABASE),
        ],
        open_questions=[OpenQuestion(id="oq1", text="What environment?", related_component_ids=["api"])],
    )
    impact = classify_user_request("target is aws, downtime is 4 hours")

    result = resolve_sparse_intake_open_question_from_target_context_answer(
        model, "target is aws, downtime is 4 hours", impact, PatchSet(patches=[], narration="")
    )

    assert result.patches == []


def test_proceed_command_is_classified_as_proceed_with_assumptions_not_terse_confirmation():
    """"i don't have more details" contains "don't", which the terse-confirmation
    term list also matches — this asserts the stronger, more specific proceed
    intent wins so should_mutate_source stays True instead of freezing the model."""

    impact = classify_user_request("just give it, proceed with a draft, i don't have more details")
    assert impact.intent == RequestIntent.PROCEED_WITH_ASSUMPTIONS
    assert impact.should_mutate_source is True


def test_proceed_command_drafts_a_placeholder_architecture_when_model_is_still_sparse():
    model = ArchitectureModel(
        components=[Component(id="inventory_app", name="Inventory Management Web App", workload_type=WorkloadType.WEB_SERVICE)]
    )
    impact = classify_user_request("just give it, proceed with a draft, i don't have more details")

    result = draft_minimal_architecture_when_user_says_proceed(model, impact, PatchSet(patches=[], narration=""))

    added_ids = {p.id for p in result.patches if p.op == PatchOp.ADD_COMPONENT}
    assert added_ids == {"primary_database"}
    assert any(p.op == PatchOp.ADD_DEPENDENCY for p in result.patches)
    assert any(p.op == PatchOp.ADD_ASSUMPTION for p in result.patches)
    assert result.narration


def test_target_context_answer_records_a_durable_auto_confirmed_greenfield_assumption():
    """The greenfield/target facts must land as an ALREADY-CONFIRMED assumption,
    not a fresh unconfirmed one — otherwise "is that correct?" just becomes the
    next thing the app repeats every turn instead of the original question."""

    model = ArchitectureModel(
        components=[Component(id="tracker", name="Employee Allocation Tracker", workload_type=WorkloadType.OTHER)]
    )
    message = "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs"
    impact = classify_user_request(message)

    patch_set = resolve_sparse_intake_open_question_from_target_context_answer(
        model, message, impact, PatchSet(patches=[], narration="")
    )
    new_model, results = apply_patch_set(model, patch_set)

    assert all(r.outcome == "applied" for r in results)
    assert model_has_confirmed_greenfield_fact(new_model)


def test_target_context_backup_does_not_duplicate_the_assumption_once_greenfield_is_already_confirmed():
    model = ArchitectureModel(
        components=[Component(id="tracker", name="Employee Allocation Tracker", workload_type=WorkloadType.OTHER)],
        assumptions=[
            Assumption(
                id="A1",
                text="User-provided migration context: this is a greenfield build with no existing deployed system yet.",
                raised_by="llm",
                resolved=True,
            )
        ],
    )
    impact = classify_user_request("target is aws, downtime is 4 hours")

    result = resolve_sparse_intake_open_question_from_target_context_answer(
        model, "target is aws, downtime is 4 hours", impact, PatchSet(patches=[], narration="")
    )

    assert result.patches == []


def test_confirmed_greenfield_fact_suppresses_current_hosting_question_on_a_later_unrelated_turn():
    """Regression for the live-reported bug: greenfield stated once (turn 2) must
    keep suppressing "what's your current hosting?" on turn 5, even though turn
    5's own message (about an unrelated SSO dependency) says nothing about
    greenfield status itself."""

    model = ArchitectureModel(
        components=[
            Component(id="tracker", name="Employee Allocation Tracker", workload_type=WorkloadType.OTHER),
            Component(id="profiles", name="Employee Profiles", workload_type=WorkloadType.OTHER),
            Component(id="sso", name="SSO Integration", workload_type=WorkloadType.THIRD_PARTY_INTEGRATION),
        ],
        assumptions=[
            Assumption(
                id="A1",
                text="User-provided migration context: target platform is AWS; this is a greenfield build with "
                "no existing deployed system yet.",
                raised_by="llm",
                resolved=True,
            )
        ],
    )
    later_message = "The SSO provider is external and is only used by the Auth Service, not by any other component directly."
    impact = classify_user_request(later_message)

    gaps = top_gaps(model, n=3)
    assert GapCategory.MISSING_ENVIRONMENT in {g.category for g in gaps}

    adapted = _adapt_gaps_to_latest_user_message(gaps, {"user_message": later_message, "request_impact": impact, "model": model})

    assert GapCategory.MISSING_ENVIRONMENT not in {g.category for g in adapted}


def test_environment_synonym_backup_does_not_fire_on_a_target_planning_message():
    """A bare "aws" answering "what's your CURRENT hosting?" should still set
    the environment (covered elsewhere) — but "want to move to aws" for a
    greenfield build must NOT be read as confirming current/source hosting.
    Regression for a bug where this backup stamped 'The current hosting
    environment is AWS.' onto a system the user said doesn't exist yet."""

    model = ArchitectureModel(
        components=[Component(id="tracker", name="Employee Allocation Tracker", workload_type=WorkloadType.OTHER)]
    )
    message = "it is nothing for now needed to build and want to move to aws and for large scale users downtime is 4hrs"
    impact = classify_user_request(message)

    result = resolve_environment_open_questions_from_short_answer(model, message, PatchSet(patches=[], narration=""), impact)

    assert result.patches == []
    assert result.narration == ""


def test_environment_synonym_backup_still_fires_on_a_plain_current_hosting_answer():
    model = ArchitectureModel(
        components=[Component(id="api", name="API", workload_type=WorkloadType.API_SERVICE)]
    )
    impact = classify_user_request("gcp")

    result = resolve_environment_open_questions_from_short_answer(model, "gcp", PatchSet(patches=[], narration=""), impact)

    assert any(p.op == PatchOp.UPDATE_COMPONENT for p in result.patches)


def test_proceed_command_is_a_noop_once_the_llm_already_drafted_something():
    model = ArchitectureModel()
    impact = classify_user_request("just give it, proceed with a draft")
    llm_patch_set = PatchSet(
        patches=[AddComponentPatch(id="web", name="Web", workload_type=WorkloadType.WEB_SERVICE)],
        narration="Drafted a web app.",
    )

    result = draft_minimal_architecture_when_user_says_proceed(model, impact, llm_patch_set)

    assert result is llm_patch_set

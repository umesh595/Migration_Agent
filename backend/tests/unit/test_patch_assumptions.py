"""Coverage for the assumption / open-question patch paths — the mechanism behind
'uncertainty as data' (technique #12). These are how an unknown surfaces instead of
being silently filled in, so their validation matters as much as the structural ops."""

from app.core.patch_applier import apply_patch_set
from app.schemas.architecture import ArchitectureModel, Component, OpenQuestion
from app.schemas.patches import (
    AddAssumptionPatch,
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

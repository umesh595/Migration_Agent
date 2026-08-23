"""Coverage for the assumption / open-question patch paths — the mechanism behind
'uncertainty as data' (technique #12). These are how an unknown surfaces instead of
being silently filled in, so their validation matters as much as the structural ops."""

from app.core.gap_analyzer import GapCategory, top_gaps
from app.core.patch_applier import apply_patch_set
from app.schemas.architecture import ArchitectureModel, Assumption, Component, OpenQuestion
from app.schemas.patches import (
    AddAssumptionPatch,
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
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm", resolved=False))

    gaps = top_gaps(model, n=10)
    assert any(g.category == GapCategory.UNCONFIRMED_ASSUMPTION for g in gaps)

    confirmed = model.model_copy(deep=True)
    confirmed.assumptions[0].resolved = True
    gaps_after = top_gaps(confirmed, n=10)
    assert not any(g.category == GapCategory.UNCONFIRMED_ASSUMPTION for g in gaps_after)


def test_confirm_assumption_marks_it_resolved():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm", resolved=False))

    new_model, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1")], narration="")
    )

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].resolved is True
    assert new_model.assumptions[0].text == "The API is stateless"


def test_confirm_assumption_can_correct_the_wording():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="The API is stateless", raised_by="llm", resolved=False))

    new_model, results = apply_patch_set(
        model,
        PatchSet(
            patches=[ConfirmAssumptionPatch(assumption_id="A1", updated_text="The API is stateless except for caching")],
            narration="",
        ),
    )

    assert results[0].outcome == PatchOutcome.APPLIED
    assert new_model.assumptions[0].resolved is True
    assert new_model.assumptions[0].text == "The API is stateless except for caching"


def test_confirm_unknown_assumption_rejected():
    model = _model()
    _, results = apply_patch_set(model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A99")], narration=""))

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "A99" in results[0].reason


def test_confirm_already_resolved_assumption_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm", resolved=True))

    _, results = apply_patch_set(model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1")], narration=""))

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "already resolved" in results[0].reason


def test_confirm_assumption_with_blank_updated_text_rejected():
    model = _model()
    model.assumptions.append(Assumption(id="A1", text="x", raised_by="llm", resolved=False))

    _, results = apply_patch_set(
        model, PatchSet(patches=[ConfirmAssumptionPatch(assumption_id="A1", updated_text="   ")], narration="")
    )

    assert results[0].outcome == PatchOutcome.REJECTED
    assert "empty" in results[0].reason

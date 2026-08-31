"""Coverage for the review-discuss graph's routing: a structural model change
(component/dependency add/update/remove) must cascade into a full replan: an
open_question/assumption-only turn must stay cheap and just report the discuss
result — see build_review_discuss_graph's docstring for why."""

from app.core.request_intelligence import classify_user_request
from app.orchestration.graph import (
    STRUCTURAL_PATCH_OPS,
    _model_materially_changed,
    _planning_intake_ready,
    build_planning_graph,
    build_review_discuss_graph,
)
from app.orchestration.nodes.planning import drop_spurious_target_state_intake_questions
from app.orchestration.nodes.review import _is_review_explanation_request
from app.schemas.patches import AddComponentPatch, AddOpenQuestionPatch, PatchOutcome, PatchResult, PatchSet


def _result(patch, outcome=PatchOutcome.APPLIED) -> PatchResult:
    return PatchResult(patch=patch, outcome=outcome, resulting_model_version=2)


def test_structural_patch_triggers_replan():
    state = {"last_patch_results": [_result(AddComponentPatch(id="x", name="X", workload_type="other"))]}
    assert _model_materially_changed(state) == "replan"


def test_open_question_only_stays_discuss_only():
    state = {"last_patch_results": [_result(AddOpenQuestionPatch(text="Do we need this?"))]}
    assert _model_materially_changed(state) == "discuss_only"


def test_rejected_structural_patch_does_not_trigger_replan():
    """A structural op that was REJECTED by validation never actually changed the
    model — routing to a full replan over a no-op would be pure waste."""

    patch = AddComponentPatch(id="x", name="X", workload_type="other")
    state = {"last_patch_results": [_result(patch, outcome=PatchOutcome.REJECTED)]}
    assert _model_materially_changed(state) == "discuss_only"


def test_no_patches_stays_discuss_only():
    assert _model_materially_changed({"last_patch_results": []}) == "discuss_only"
    assert _model_materially_changed({}) == "discuss_only"


def test_review_explanation_request_uses_answer_path():
    assert _is_review_explanation_request(
        "Explain why ECS Fargate was chosen instead of EKS. Include effort, cost, risk, validation, and rollback."
    )
    assert _is_review_explanation_request("Review the effort, cost, and efficiency estimates.")


def test_review_change_request_stays_patch_discussion_path():
    assert not _is_review_explanation_request("Change the backend from FastAPI to Java Spring Boot.")
    assert not _is_review_explanation_request("Add Stripe payment processing into the migration plan.")


def test_planning_intake_without_patches_continues_to_context_elicitation():
    assert _planning_intake_ready({"last_patch_results": []}) == "continue"
    assert _planning_intake_ready({}) == "continue"


def test_planning_intake_with_open_question_pauses_for_user():
    state = {"last_patch_results": [_result(AddOpenQuestionPatch(text="Revise the accepted source model?"))]}
    assert _planning_intake_ready(state) == "await_user"


def test_planning_intake_with_error_halts():
    assert _planning_intake_ready({"error": "bad"}) == "halt"


def test_planning_intake_drops_spurious_target_state_question_for_migration_context():
    patch_set = PatchSet(
        patches=[
            AddOpenQuestionPatch(
                text=(
                    "The user suggests hosting the React SPA on S3 behind CloudFront. Since the source "
                    "architecture has already been accepted, should we revise the accepted source model or "
                    "treat this as target-state planning input?"
                )
            )
        ],
        narration="Confirm source or target.",
    )

    cleaned = drop_spurious_target_state_intake_questions(
        "Source is AWS cloud. Target is modernized AWS. Host the React SPA in S3 behind CloudFront. "
        "Run backend workloads on ECS Fargate. Downtime tolerance is a 4-hour maintenance window.",
        patch_set,
        classify_user_request(
            "Source is AWS cloud. Target is modernized AWS. Host the React SPA in S3 behind CloudFront. "
            "Run backend workloads on ECS Fargate. Downtime tolerance is a 4-hour maintenance window.",
            after_gate_1=True,
        ),
    )

    assert cleaned.patches == []
    assert cleaned.narration == ""


def test_planning_intake_keeps_source_correction_question():
    patch_set = PatchSet(
        patches=[
            AddOpenQuestionPatch(
                text=(
                    "You mentioned adding Redis to the source architecture. Since Gate 1 has accepted the "
                    "source model, should we revise the accepted source model or treat this as target-state "
                    "planning input?"
                )
            )
        ],
        narration="Confirm Redis handling.",
    )

    cleaned = drop_spurious_target_state_intake_questions(
        "Wait, I forgot Redis in the source architecture. Add Redis cache.",
        patch_set,
    )

    assert cleaned.patches == patch_set.patches
    assert cleaned.narration == "Confirm Redis handling."


def test_planning_intake_uses_target_planning_classifier_to_drop_question():
    patch_set = PatchSet(
        patches=[
            AddOpenQuestionPatch(
                text="Should we revise the accepted source model or treat this as target-state planning input?"
            )
        ],
        narration="Confirm source or target.",
    )

    cleaned = drop_spurious_target_state_intake_questions(
        "Target is modernized AWS.",
        patch_set,
        classify_user_request("Target is modernized AWS. Downtime tolerance is flexible.", after_gate_1=True),
    )

    assert cleaned.patches == []


def test_structural_patch_ops_matches_the_component_and_dependency_ops():
    assert STRUCTURAL_PATCH_OPS == {
        "add_component",
        "update_component",
        "remove_component",
        "add_dependency",
        "remove_dependency",
    }


def test_graph_compiles_with_the_expected_nodes():
    """A pure wiring smoke test — catches typo'd node names or missing edges
    without needing a real gateway/checkpointer/LLM call."""

    from unittest.mock import MagicMock

    graph = build_review_discuss_graph(MagicMock(), MagicMock())
    compiled = graph.compile()
    nodes = set(compiled.get_graph().nodes.keys())

    expected = {
        "ingest", "apply_patches",
        "compute_sequence", "per_component_planning", "strategy", "assemble_plan", "estimate_cost",
        "rules_review", "llm_review", "judge_review", "refine", "finalize_review",
    }
    assert expected.issubset(nodes)
    assert "gap_analysis" not in nodes
    assert "generate_questions" not in nodes


def test_planning_graph_runs_after_gate_intake_before_context_elicitation():
    from unittest.mock import MagicMock

    graph = build_planning_graph(MagicMock(), MagicMock())
    compiled = graph.compile()
    nodes = set(compiled.get_graph().nodes.keys())

    assert {"after_gate_intake", "apply_patches", "elicit_context"}.issubset(nodes)

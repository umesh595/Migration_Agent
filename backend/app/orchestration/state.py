"""The typed canonical graph state (technique #2). Everything the user sees is a
render of this — the conversation transcript is disposable."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, TypedDict

from app.schemas.architecture import ArchitectureModel
from app.schemas.findings import Finding
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import MigrationPlan
from app.schemas.patches import PatchResult
from app.schemas.review_quality import ReviewQualityScore


class Stage(StrEnum):
    DISCOVERY = "discovery"
    AWAITING_MODEL_ACCEPT = "awaiting_model_accept"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    REVIEW = "review"
    COMPLETE = "complete"


def _replace[T](_old: T, new: T) -> T:
    return new


class GraphState(TypedDict, total=False):
    session_id: str
    stage: Annotated[Stage, _replace]

    # Discovery
    user_message: str
    model: Annotated[ArchitectureModel, _replace]
    last_patch_results: list[PatchResult]
    pending_questions: list[str]
    narration: str

    # Planning
    migration_context: MigrationContext | None
    context_clarifying_questions: list[str]
    plan: MigrationPlan | None

    # Review
    findings: list[Finding]
    refine_iteration: int
    review_quality_history: list[ReviewQualityScore]

    # Errors surfaced to the user without corrupting state
    error: str | None

    # Intra-turn scratch, cleared before the checkpoint is surfaced. Leading
    # underscore marks these as not part of the user-visible state contract.
    _patch_set: object | None
    _gaps: object | None
    _waves: object | None
    _component_outputs: object | None
    _target_architecture: str | None
    _cutover: object | None
    _rollback: object | None

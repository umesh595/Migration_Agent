"""Patch-based mutation contract (technique #3): the LLM emits these, and only
PatchValidator/PatchApplier (deterministic code) may turn them into model changes.
The LLM structurally cannot corrupt the model — it can only propose an operation that
gets checked against the current state before anything is written."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.architecture import Component, Dependency, DependencyKind, Environment, WorkloadType


class PatchOp(StrEnum):
    ADD_COMPONENT = "add_component"
    UPDATE_COMPONENT = "update_component"
    REMOVE_COMPONENT = "remove_component"
    ADD_DEPENDENCY = "add_dependency"
    REMOVE_DEPENDENCY = "remove_dependency"
    ADD_ASSUMPTION = "add_assumption"
    CONFIRM_ASSUMPTION = "confirm_assumption"
    RESOLVE_OPEN_QUESTION = "resolve_open_question"
    ADD_OPEN_QUESTION = "add_open_question"


class AddComponentPatch(BaseModel):
    op: Literal[PatchOp.ADD_COMPONENT] = PatchOp.ADD_COMPONENT
    id: str
    name: str
    workload_type: WorkloadType
    description: str = ""
    technology: str | None = None
    environment: Environment | None = None
    criticality: str | None = Field(
        default=None,
        description="Set when confidently inferable from the component's role at creation time "
        "(e.g. 'tier-1', 'tier-2') — avoids a separate update_component just to record it.",
    )


class UpdateComponentPatch(BaseModel):
    """Every updatable field is listed explicitly rather than a free-form
    `fields: dict[str, str]` — discovered against the live OpenAI API (not a
    design preference): Structured Outputs strict mode cannot represent an
    open-ended key→value map, since every property must be enumerable ahead of
    time. See DECISIONS.md. `id` is deliberately not one of these fields — there
    is no mechanism through which this patch could rewrite a component's identity,
    not just a validation rule against it (enforced by test)."""

    op: Literal[PatchOp.UPDATE_COMPONENT] = PatchOp.UPDATE_COMPONENT
    id: str
    name: str | None = None
    description: str | None = None
    technology: str | None = None
    owner_team: str | None = None
    criticality: str | None = None
    environment: Environment | None = None

    def updated_fields(self) -> dict[str, str | Environment]:
        """Only the fields actually set — None means 'leave unchanged', not
        'clear this field' (there's no way to blank a field via this patch)."""

        candidates = {
            "name": self.name,
            "description": self.description,
            "technology": self.technology,
            "owner_team": self.owner_team,
            "criticality": self.criticality,
            "environment": self.environment,
        }
        return {field: value for field, value in candidates.items() if value is not None}


class RemoveComponentPatch(BaseModel):
    op: Literal[PatchOp.REMOVE_COMPONENT] = PatchOp.REMOVE_COMPONENT
    id: str


class AddDependencyPatch(BaseModel):
    op: Literal[PatchOp.ADD_DEPENDENCY] = PatchOp.ADD_DEPENDENCY
    source_id: str
    target_id: str
    kind: DependencyKind
    description: str = ""


class RemoveDependencyPatch(BaseModel):
    op: Literal[PatchOp.REMOVE_DEPENDENCY] = PatchOp.REMOVE_DEPENDENCY
    source_id: str
    target_id: str
    kind: DependencyKind | None = Field(
        default=None,
        description="Set when known. If omitted and multiple dependency kinds exist "
        "between the same source/target pair, the patch is rejected asking for disambiguation.",
    )


class AddAssumptionPatch(BaseModel):
    op: Literal[PatchOp.ADD_ASSUMPTION] = PatchOp.ADD_ASSUMPTION
    text: str
    related_component_ids: list[str] = Field(default_factory=list)


class ConfirmAssumptionPatch(BaseModel):
    """The user's counterpart to add_assumption: when the user confirms, corrects,
    or rejects an assumption the LLM previously raised, this resolves it so
    GapAnalyzer stops re-flagging it every turn. Without this op, an LLM-raised
    assumption had no path to ever leave the open-gap list — 'yes, that's
    correct' had nothing to attach to."""

    op: Literal[PatchOp.CONFIRM_ASSUMPTION] = PatchOp.CONFIRM_ASSUMPTION
    assumption_id: str
    updated_text: str | None = Field(
        default=None,
        description="Set only if the user's confirmation corrects or refines the original wording; "
        "omit to confirm the assumption exactly as originally stated.",
    )


class ResolveOpenQuestionPatch(BaseModel):
    op: Literal[PatchOp.RESOLVE_OPEN_QUESTION] = PatchOp.RESOLVE_OPEN_QUESTION
    question_id: str
    resolution_text: str


class AddOpenQuestionPatch(BaseModel):
    """The discuss-before-adding counterpart to add_component/add_dependency: when
    a message proposes adding something with no discoverable basis in anything
    described so far (new, unscoped functionality — not a fact about the current
    system), this records the open question INSTEAD of silently adding it, so the
    next turn can see "this was asked and is still unresolved" (same pattern as
    AddAssumptionPatch) rather than the agent either blindly complying or having
    no memory of having asked at all."""

    op: Literal[PatchOp.ADD_OPEN_QUESTION] = PatchOp.ADD_OPEN_QUESTION
    text: str
    related_component_ids: list[str] = Field(default_factory=list)


Patch = (
    AddComponentPatch
    | UpdateComponentPatch
    | RemoveComponentPatch
    | AddDependencyPatch
    | RemoveDependencyPatch
    | AddAssumptionPatch
    | ConfirmAssumptionPatch
    | ResolveOpenQuestionPatch
    | AddOpenQuestionPatch
)


class PatchSet(BaseModel):
    """What the ingestion LLM call returns for a single user turn."""

    patches: list[Patch] = Field(default_factory=list)
    narration: str = Field(description="Plain-language summary of what was understood, for the reply.")


class PatchOutcome(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class PatchResult(BaseModel):
    """Audit record for one patch after validation — persisted regardless of outcome."""

    patch: Patch
    outcome: PatchOutcome
    reason: str | None = Field(
        default=None,
        description="Set when outcome is REJECTED (why validation refused it), or when a structural "
        "APPLIED patch (add/update/remove component or dependency) was added by confirming a prior "
        "discuss question — the user's stated justification, or 'no reason given' if they gave none "
        "(see apply_patch_set's confirmation_reason).",
    )
    resulting_model_version: int | None = None


__all__ = [
    "PatchOp",
    "AddComponentPatch",
    "UpdateComponentPatch",
    "RemoveComponentPatch",
    "AddDependencyPatch",
    "RemoveDependencyPatch",
    "AddAssumptionPatch",
    "ConfirmAssumptionPatch",
    "ResolveOpenQuestionPatch",
    "AddOpenQuestionPatch",
    "Patch",
    "PatchSet",
    "PatchOutcome",
    "PatchResult",
    "Component",
    "Dependency",
]

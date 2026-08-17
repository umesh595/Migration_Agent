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
    RESOLVE_OPEN_QUESTION = "resolve_open_question"


class AddComponentPatch(BaseModel):
    op: Literal[PatchOp.ADD_COMPONENT] = PatchOp.ADD_COMPONENT
    id: str
    name: str
    workload_type: WorkloadType
    description: str = ""
    technology: str | None = None
    environment: Environment | None = None


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


class AddAssumptionPatch(BaseModel):
    op: Literal[PatchOp.ADD_ASSUMPTION] = PatchOp.ADD_ASSUMPTION
    text: str
    related_component_ids: list[str] = Field(default_factory=list)


class ResolveOpenQuestionPatch(BaseModel):
    op: Literal[PatchOp.RESOLVE_OPEN_QUESTION] = PatchOp.RESOLVE_OPEN_QUESTION
    question_id: str
    resolution_text: str


Patch = (
    AddComponentPatch
    | UpdateComponentPatch
    | RemoveComponentPatch
    | AddDependencyPatch
    | RemoveDependencyPatch
    | AddAssumptionPatch
    | ResolveOpenQuestionPatch
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
    reason: str | None = Field(default=None, description="Set when outcome is REJECTED.")
    resulting_model_version: int | None = None


__all__ = [
    "PatchOp",
    "AddComponentPatch",
    "UpdateComponentPatch",
    "RemoveComponentPatch",
    "AddDependencyPatch",
    "RemoveDependencyPatch",
    "AddAssumptionPatch",
    "ResolveOpenQuestionPatch",
    "Patch",
    "PatchSet",
    "PatchOutcome",
    "PatchResult",
    "Component",
    "Dependency",
]

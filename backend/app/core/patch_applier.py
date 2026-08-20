"""Applies a PatchSet to an ArchitectureModel: validate-then-mutate, one patch at a
time, each against the result of the previous one in the same set. Every patch —
applied or rejected — produces a PatchResult for the audit log (Doc 3 §3.2 step 3)."""

from __future__ import annotations

from app.core.patch_validator import validate_patch
from app.schemas.architecture import ArchitectureModel, Assumption, Component, Dependency, Environment
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    Patch,
    PatchOutcome,
    PatchResult,
    PatchSet,
    RemoveComponentPatch,
    RemoveDependencyPatch,
    ResolveOpenQuestionPatch,
    UpdateComponentPatch,
)


def _apply_single(model: ArchitectureModel, patch: Patch) -> ArchitectureModel:
    """Mutates and returns a new model. Caller guarantees `patch` already passed
    validate_patch against this exact `model` state."""

    data = model.model_copy(deep=True)

    match patch:
        case AddComponentPatch():
            data.components.append(
                Component(
                    id=patch.id,
                    name=patch.name,
                    workload_type=patch.workload_type,
                    description=patch.description,
                    technology=patch.technology,
                    environment=patch.environment or Environment.UNKNOWN,
                )
            )

        case UpdateComponentPatch():
            component = data.get_component(patch.id)
            assert component is not None
            for field, value in patch.updated_fields().items():
                setattr(component, field, value)

        case RemoveComponentPatch():
            data.components = [c for c in data.components if c.id != patch.id]
            # Cascade: dangling dependencies would otherwise reference a removed
            # component; GapAnalyzer would flag them as orphans anyway, so remove
            # them now rather than leave the model transiently inconsistent.
            data.dependencies = [
                d for d in data.dependencies if patch.id not in (d.source_id, d.target_id)
            ]

        case AddDependencyPatch():
            dep_id = f"{patch.source_id}->{patch.target_id}:{patch.kind}"
            data.dependencies.append(
                Dependency(
                    id=dep_id,
                    source_id=patch.source_id,
                    target_id=patch.target_id,
                    kind=patch.kind,
                    description=patch.description,
                )
            )

        case RemoveDependencyPatch():
            data.dependencies = [
                d
                for d in data.dependencies
                if not (
                    d.source_id == patch.source_id
                    and d.target_id == patch.target_id
                    and (patch.kind is None or d.kind == patch.kind)
                )
            ]

        case AddAssumptionPatch():
            next_index = len(data.assumptions) + 1
            data.assumptions.append(
                Assumption(
                    id=f"A{next_index}",
                    text=patch.text,
                    raised_by="llm",
                    related_component_ids=patch.related_component_ids,
                )
            )

        case ResolveOpenQuestionPatch():
            question = next((q for q in data.open_questions if q.id == patch.question_id), None)
            assert question is not None
            question.resolved = True
            data.assumptions.append(
                Assumption(
                    id=f"A{len(data.assumptions) + 1}",
                    text=patch.resolution_text,
                    raised_by="user",
                    related_component_ids=question.related_component_ids,
                )
            )

    data.version = model.version + 1
    return data


def apply_patch_set(model: ArchitectureModel, patch_set: PatchSet) -> tuple[ArchitectureModel, list[PatchResult]]:
    """Returns the final model after applying every valid patch in order, plus one
    PatchResult per patch (applied or rejected) for the audit log."""

    current = model
    results: list[PatchResult] = []

    for patch in patch_set.patches:
        rejection_reason = validate_patch(current, patch)
        if rejection_reason is not None:
            results.append(PatchResult(patch=patch, outcome=PatchOutcome.REJECTED, reason=rejection_reason))
            continue

        current = _apply_single(current, patch)
        results.append(
            PatchResult(patch=patch, outcome=PatchOutcome.APPLIED, resulting_model_version=current.version)
        )

    return current, results

"""Applies a PatchSet to an ArchitectureModel: validate-then-mutate, one patch at a
time, each against the result of the previous one in the same set. Every patch —
applied or rejected — produces a PatchResult for the audit log (Doc 3 §3.2 step 3)."""

from __future__ import annotations

from app.core.patch_validator import validate_patch
from app.schemas.architecture import ArchitectureModel, Assumption, Component, Dependency, Environment, OpenQuestion
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    AddOpenQuestionPatch,
    ConfirmAssumptionPatch,
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
                    criticality=patch.criticality,
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

        case ConfirmAssumptionPatch():
            assumption = next(a for a in data.assumptions if a.id == patch.assumption_id)
            assumption.resolved = True
            if patch.updated_text is not None:
                assumption.text = patch.updated_text

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

        case AddOpenQuestionPatch():
            data.open_questions.append(
                OpenQuestion(
                    id=f"Q{len(data.open_questions) + 1}",
                    text=patch.text,
                    related_component_ids=patch.related_component_ids,
                )
            )

    data.version = model.version + 1
    return data


_STRUCTURAL_PATCH_TYPES = (
    AddComponentPatch,
    UpdateComponentPatch,
    RemoveComponentPatch,
    AddDependencyPatch,
    RemoveDependencyPatch,
)


def apply_patch_set(
    model: ArchitectureModel,
    patch_set: PatchSet,
    *,
    require_structural_confirmation: bool = False,
) -> tuple[ArchitectureModel, list[PatchResult]]:
    """Returns the final model after applying every valid patch in order, plus one
    PatchResult per patch (applied or rejected) for the audit log."""

    current = model
    results: list[PatchResult] = []

    # The discuss algorithm's auditable-record requirement ("record what changed
    # and why — the user's stated reason, or 'no reason given' if none"): when a
    # turn resolves a previously-raised open question, that resolution's text IS
    # the "why" for whatever gets added in the same breath, so it's attached to
    # the structural patch(es) in this same set rather than living only in
    # free-text narration nobody has to read.
    confirmation_reason = next(
        (p.resolution_text for p in patch_set.patches if isinstance(p, ResolveOpenQuestionPatch)), None
    )

    for patch in patch_set.patches:
        rejection_reason = validate_patch(
            current,
            patch,
            allow_high_impact_changes=confirmation_reason is not None,
            require_structural_confirmation=require_structural_confirmation and confirmation_reason is None,
        )
        if rejection_reason is not None:
            results.append(PatchResult(patch=patch, outcome=PatchOutcome.REJECTED, reason=rejection_reason))
            continue

        current = _apply_single(current, patch)
        applied_reason = confirmation_reason if isinstance(patch, _STRUCTURAL_PATCH_TYPES) else None
        results.append(
            PatchResult(
                patch=patch,
                outcome=PatchOutcome.APPLIED,
                resulting_model_version=current.version,
                reason=applied_reason,
            )
        )

    return current, results

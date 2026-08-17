"""Pure validation: given the current model and one proposed patch, decide whether it
may be applied. No mutation happens here — see patch_applier.py. This is the wall
that makes technique #3 ('LLM proposes, code disposes') real: an invalid patch never
reaches the model, regardless of how confidently the LLM phrased it."""

from __future__ import annotations

from app.schemas.architecture import ArchitectureModel, Environment
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    Patch,
    RemoveComponentPatch,
    RemoveDependencyPatch,
    ResolveOpenQuestionPatch,
    UpdateComponentPatch,
)


def validate_patch(model: ArchitectureModel, patch: Patch) -> str | None:
    """Returns None if the patch is valid against `model`, otherwise a human-readable
    rejection reason (narrated back to the user verbatim)."""

    match patch:
        case AddComponentPatch():
            if patch.id in model.component_ids():
                return f"a component with id '{patch.id}' already exists"
            if patch.environment is not None and patch.environment not in set(Environment):
                return (
                    f"invalid environment '{patch.environment}'. "
                    f"Allowed values are: {', '.join(e.value for e in Environment)}"
                )
            return None

        case UpdateComponentPatch():
            if patch.id not in model.component_ids():
                return f"no component with id '{patch.id}' exists"
            if not patch.updated_fields():
                return "update_component patch sets no fields — nothing to change"
            if patch.environment is not None and patch.environment not in set(Environment):
                return (
                    f"invalid environment '{patch.environment}'. "
                    f"Allowed values are: {', '.join(e.value for e in Environment)}"
                )
            return None

        case RemoveComponentPatch():
            if patch.id not in model.component_ids():
                return f"no component with id '{patch.id}' exists"
            return None

        case AddDependencyPatch():
            ids = model.component_ids()
            if patch.source_id not in ids:
                return f"no component with id '{patch.source_id}' exists"
            if patch.target_id not in ids:
                return f"no component with id '{patch.target_id}' exists"
            duplicate = any(
                d.source_id == patch.source_id and d.target_id == patch.target_id and d.kind == patch.kind
                for d in model.dependencies
            )
            if duplicate:
                return f"a '{patch.kind}' dependency from '{patch.source_id}' to '{patch.target_id}' already exists"
            return None

        case RemoveDependencyPatch():
            exists = any(d.source_id == patch.source_id and d.target_id == patch.target_id for d in model.dependencies)
            if not exists:
                return f"no dependency from '{patch.source_id}' to '{patch.target_id}' exists"
            return None

        case AddAssumptionPatch():
            if not patch.text.strip():
                return "assumption text cannot be empty"
            unknown = set(patch.related_component_ids) - model.component_ids()
            if unknown:
                return f"related component id(s) {sorted(unknown)} do not exist"
            return None

        case ResolveOpenQuestionPatch():
            question = next((q for q in model.open_questions if q.id == patch.question_id), None)
            if question is None:
                return f"no open question with id '{patch.question_id}' exists"
            if question.resolved:
                return f"open question '{patch.question_id}' is already resolved"
            return None

        case _:
            return f"unrecognized patch operation: {patch!r}"

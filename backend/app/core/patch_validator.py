"""Pure validation: given the current model and one proposed patch, decide whether it
may be applied. No mutation happens here — see patch_applier.py. This is the wall
that makes technique #3 ('LLM proposes, code disposes') real: an invalid patch never
reaches the model, regardless of how confidently the LLM phrased it."""

from __future__ import annotations

import re

from app.schemas.architecture import ArchitectureModel, Environment
from app.schemas.patches import (
    AddAssumptionPatch,
    AddComponentPatch,
    AddDependencyPatch,
    AddOpenQuestionPatch,
    ConfirmAssumptionPatch,
    Patch,
    RemoveComponentPatch,
    RemoveDependencyPatch,
    ResolveOpenQuestionPatch,
    UpdateComponentPatch,
)

_HIGH_IMPACT_TECH_TERMS = {
    "fastapi",
    "python",
    "java",
    "spring",
    "spring boot",
    "node",
    "node.js",
    "express",
    ".net",
    "dotnet",
    "go",
    "golang",
    "ruby",
    "rails",
}


def _text_terms(text: str | None) -> set[str]:
    lowered = (text or "").lower()
    return {
        term
        for term in _HIGH_IMPACT_TECH_TERMS
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered)
    }


def _is_high_impact_replatform(model: ArchitectureModel, patch: UpdateComponentPatch) -> bool:
    component = model.get_component(patch.id)
    if component is None:
        return False

    before_terms = _text_terms(" ".join([component.name, component.technology or "", component.description]))
    after_terms = _text_terms(" ".join([patch.name or "", patch.technology or "", patch.description or ""]))
    introduced_terms = after_terms - before_terms

    return bool(before_terms and introduced_terms)


_STRUCTURAL_PATCH_CLASSES = (
    AddComponentPatch,
    UpdateComponentPatch,
    RemoveComponentPatch,
    AddDependencyPatch,
    RemoveDependencyPatch,
)


def patches_requiring_confirmation(
    model: ArchitectureModel,
    patches: list[Patch],
    *,
    require_structural_confirmation: bool = False,
    allow_high_impact_changes: bool = False,
) -> list[Patch]:
    """Pure pre-check mirroring validate_patch's two 'discuss before applying' gates
    below, WITHOUT the rest of validate_patch's mechanical checks (id conflicts,
    duplicates, etc. — those are always hard rejections, never a confirm/reject
    decision). Used by apply_patches_node to decide, before mutating anything,
    whether this turn needs a human-in-the-loop pause (see interrupt() call there)."""

    if allow_high_impact_changes:
        return []

    pending: list[Patch] = []
    for patch in patches:
        if require_structural_confirmation and isinstance(patch, _STRUCTURAL_PATCH_CLASSES):
            pending.append(patch)
        elif isinstance(patch, UpdateComponentPatch) and _is_high_impact_replatform(model, patch):
            pending.append(patch)
    return pending


def validate_patch(
    model: ArchitectureModel,
    patch: Patch,
    *,
    allow_high_impact_changes: bool = False,
    require_structural_confirmation: bool = False,
    bypass_confirmation: bool = False,
) -> str | None:
    """Returns None if the patch is valid against `model`, otherwise a human-readable
    rejection reason (narrated back to the user verbatim).

    `bypass_confirmation` is distinct from `allow_high_impact_changes`: the latter is
    set when the SAME turn already resolved the open question this gate would have
    raised (see apply_patch_set's confirmation_reason); the former is set when a human
    explicitly approved the change through the interrupt() flow in apply_patches_node
    — both skip the same two gates below, for different reasons."""

    if bypass_confirmation:
        allow_high_impact_changes = True
        require_structural_confirmation = False

    if require_structural_confirmation and isinstance(patch, _STRUCTURAL_PATCH_CLASSES):
        return (
            "source architecture changes after Gate 1 require explicit discussion first; "
            "ask whether to revise the accepted source model or treat this as a target-state planning change"
        )

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
            if not allow_high_impact_changes and _is_high_impact_replatform(model, patch):
                return (
                    "major technology/platform rewrite requires discussion first; "
                    "ask whether this is a real target architecture decision or just an exploratory idea"
                )
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
            if patch.source_id == patch.target_id:
                return f"a dependency cannot connect '{patch.source_id}' to itself"
            duplicate = any(
                d.source_id == patch.source_id and d.target_id == patch.target_id and d.kind == patch.kind
                for d in model.dependencies
            )
            if duplicate:
                return f"a '{patch.kind}' dependency from '{patch.source_id}' to '{patch.target_id}' already exists"
            return None

        case RemoveDependencyPatch():
            matches = [
                d
                for d in model.dependencies
                if d.source_id == patch.source_id and d.target_id == patch.target_id
                and (patch.kind is None or d.kind == patch.kind)
            ]
            if not matches:
                return f"no dependency from '{patch.source_id}' to '{patch.target_id}' exists"
            if patch.kind is None and len({d.kind for d in matches}) > 1:
                kinds = ", ".join(sorted(d.kind for d in matches))
                return (
                    f"multiple dependency kinds exist from '{patch.source_id}' to '{patch.target_id}' "
                    f"({kinds}) — specify which kind to remove"
                )
            return None

        case AddAssumptionPatch():
            if not patch.text.strip():
                return "assumption text cannot be empty"
            unknown = set(patch.related_component_ids) - model.component_ids()
            if unknown:
                return f"related component id(s) {sorted(unknown)} do not exist"
            return None

        case ConfirmAssumptionPatch():
            assumption = next((a for a in model.assumptions if a.id == patch.assumption_id), None)
            if assumption is None:
                return f"no assumption with id '{patch.assumption_id}' exists"
            if assumption.resolved:
                return f"assumption '{patch.assumption_id}' is already resolved"
            if patch.updated_text is not None and not patch.updated_text.strip():
                return "updated_text cannot be empty — omit it to confirm as originally stated"
            return None

        case ResolveOpenQuestionPatch():
            question = next((q for q in model.open_questions if q.id == patch.question_id), None)
            if question is None:
                return f"no open question with id '{patch.question_id}' exists"
            if question.resolved:
                return f"open question '{patch.question_id}' is already resolved"
            return None

        case AddOpenQuestionPatch():
            if not patch.text.strip():
                return "open question text cannot be empty"
            unknown = set(patch.related_component_ids) - model.component_ids()
            if unknown:
                return f"related component id(s) {sorted(unknown)} do not exist"
            return None

        case _:
            return f"unrecognized patch operation: {patch!r}"

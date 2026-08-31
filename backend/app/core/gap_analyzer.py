"""Computes what's still unknown about the model. Question generation (LLM) works
from this list, not from free-association — 'questions come from computed unknowns,
not LLM imagination' (technique #4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.schemas.architecture import ArchitectureModel, Environment


class GapCategory(StrEnum):
    OPEN_QUESTION = "open_question"
    SPARSE_ARCHITECTURE_CONTEXT = "sparse_architecture_context"
    ORPHAN_COMPONENT = "orphan_component"
    MISSING_ENVIRONMENT = "missing_environment"
    MISSING_CRITICALITY = "missing_criticality"
    UNCONFIRMED_ASSUMPTION = "unconfirmed_assumption"


_PRIORITY = {
    GapCategory.OPEN_QUESTION: 100,
    GapCategory.SPARSE_ARCHITECTURE_CONTEXT: 95,
    GapCategory.ORPHAN_COMPONENT: 80,
    GapCategory.MISSING_ENVIRONMENT: 60,
    GapCategory.UNCONFIRMED_ASSUMPTION: 50,
    GapCategory.MISSING_CRITICALITY: 20,
}


class Gap(BaseModel):
    category: GapCategory
    description: str
    related_component_ids: list[str] = []
    priority: int


def _format_names(names: list[str], limit: int = 6) -> str:
    """'these 13 components' beats 13 near-identical sentences — a senior migration
    architect asks about a category of unknown once, grouped, not once per row."""

    shown = ", ".join(f"'{n}'" for n in names[:limit])
    if len(names) > limit:
        shown += f", and {len(names) - limit} more"
    return shown


def analyze_gaps(model: ArchitectureModel) -> list[Gap]:
    gaps: list[Gap] = []

    for question in model.open_questions:
        if not question.resolved:
            gaps.append(
                Gap(
                    category=GapCategory.OPEN_QUESTION,
                    description=question.text,
                    related_component_ids=question.related_component_ids,
                    priority=_PRIORITY[GapCategory.OPEN_QUESTION],
                )
            )

    connected_ids = {d.source_id for d in model.dependencies} | {d.target_id for d in model.dependencies}
    is_multi_component = len(model.components) > 1

    if len(model.components) <= 1 and not model.dependencies:
        component_names = [c.name for c in model.components] or ["the application"]
        gaps.append(
            Gap(
                category=GapCategory.SPARSE_ARCHITECTURE_CONTEXT,
                description=(
                    "The input describes the business purpose but not enough architecture to produce a "
                    "credible migration model. Ask a compact consultant-style intake question covering: "
                    "current major components or tech stack, current hosting/source environment, target "
                    "cloud or desired outcome if known, rough scale/data volume, and downtime tolerance. "
                    f"Known so far: {_format_names(component_names)}."
                ),
                related_component_ids=[c.id for c in model.components],
                priority=_PRIORITY[GapCategory.SPARSE_ARCHITECTURE_CONTEXT],
            )
        )

    orphans = [c for c in model.components if is_multi_component and c.id not in connected_ids]
    missing_environment = [c for c in model.components if c.environment == Environment.UNKNOWN]
    missing_criticality = [c for c in model.components if not c.criticality]

    # One gap per CATEGORY across every affected component, never one gap per
    # component — the mechanical "how critical is X?" repeated for every row is
    # exactly the schema-validator behavior this function must not produce.
    if orphans:
        verb = "has" if len(orphans) == 1 else "have"
        gaps.append(
            Gap(
                category=GapCategory.ORPHAN_COMPONENT,
                description=f"{_format_names([c.name for c in orphans])} {verb} no known dependencies in or "
                "out — is that accurate, or are there connections we haven't captured yet?",
                related_component_ids=[c.id for c in orphans],
                priority=_PRIORITY[GapCategory.ORPHAN_COMPONENT],
            )
        )

    if missing_environment:
        # Framed as a system-level question, not a per-component checklist: in the
        # overwhelmingly common case every affected component belongs to the one
        # system being discovered and shares one hosting model — "confirm the
        # environment for A, B, C, D, E" reads like a form even when it's grouped
        # into a single sentence. Component names are still attached below (for
        # code/audit use, e.g. narrowing which components a correction applies to)
        # but generate_questions is told to lead with the system-level framing.
        gaps.append(
            Gap(
                category=GapCategory.MISSING_ENVIRONMENT,
                description="This system's current hosting model (on-prem, cloud, hybrid) is unstated — ask "
                "about it once, at the system level, rather than per component, unless the message already "
                f"gives contradictory signals for specific ones. Affects: {_format_names([c.name for c in missing_environment])}",
                related_component_ids=[c.id for c in missing_environment],
                priority=_PRIORITY[GapCategory.MISSING_ENVIRONMENT],
            )
        )

    if missing_criticality:
        gaps.append(
            Gap(
                category=GapCategory.MISSING_CRITICALITY,
                description="Business criticality is still unconfirmed for: "
                f"{_format_names([c.name for c in missing_criticality])}. Infer a reasonable tier from each "
                "component's role (user-facing/auth/API/core data store/queue/AI runtime usually tier-1; "
                "observability/export/reporting helpers usually tier-2) and ask for confirmation in one "
                "grouped question rather than one question per component.",
                related_component_ids=[c.id for c in missing_criticality],
                priority=_PRIORITY[GapCategory.MISSING_CRITICALITY],
            )
        )

    for assumption in model.assumptions:
        if assumption.raised_by == "llm" and not assumption.resolved:
            gaps.append(
                Gap(
                    category=GapCategory.UNCONFIRMED_ASSUMPTION,
                    description=f"Assuming: {assumption.text} — is that correct? "
                    f"(assumption id: {assumption.id})",
                    related_component_ids=assumption.related_component_ids,
                    priority=_PRIORITY[GapCategory.UNCONFIRMED_ASSUMPTION],
                )
            )

    return sorted(gaps, key=lambda g: g.priority, reverse=True)


def top_gaps(model: ArchitectureModel, n: int = 3) -> list[Gap]:
    return analyze_gaps(model)[:n]

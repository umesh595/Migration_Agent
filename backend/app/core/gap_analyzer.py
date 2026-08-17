"""Computes what's still unknown about the model. Question generation (LLM) works
from this list, not from free-association — 'questions come from computed unknowns,
not LLM imagination' (technique #4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.schemas.architecture import ArchitectureModel, Environment


class GapCategory(StrEnum):
    OPEN_QUESTION = "open_question"
    ORPHAN_COMPONENT = "orphan_component"
    MISSING_ENVIRONMENT = "missing_environment"
    MISSING_TECHNOLOGY = "missing_technology"
    MISSING_CRITICALITY = "missing_criticality"
    UNCONFIRMED_ASSUMPTION = "unconfirmed_assumption"


_PRIORITY = {
    GapCategory.OPEN_QUESTION: 100,
    GapCategory.ORPHAN_COMPONENT: 80,
    GapCategory.MISSING_ENVIRONMENT: 60,
    GapCategory.UNCONFIRMED_ASSUMPTION: 50,
    GapCategory.MISSING_TECHNOLOGY: 40,
    GapCategory.MISSING_CRITICALITY: 20,
}


class Gap(BaseModel):
    category: GapCategory
    description: str
    related_component_ids: list[str] = []
    priority: int


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
    for component in model.components:
        if component.id not in connected_ids and len(model.components) > 1:
            gaps.append(
                Gap(
                    category=GapCategory.ORPHAN_COMPONENT,
                    description=f"'{component.name}' has no known dependencies in or out — is that accurate, "
                    "or are there connections we haven't captured yet?",
                    related_component_ids=[component.id],
                    priority=_PRIORITY[GapCategory.ORPHAN_COMPONENT],
                )
            )

        if component.environment == Environment.UNKNOWN:
            gaps.append(
                Gap(
                    category=GapCategory.MISSING_ENVIRONMENT,
                    description=f"Which environment does '{component.name}' currently run in (on-prem, cloud, hybrid)?",
                    related_component_ids=[component.id],
                    priority=_PRIORITY[GapCategory.MISSING_ENVIRONMENT],
                )
            )

        if not component.technology:
            gaps.append(
                Gap(
                    category=GapCategory.MISSING_TECHNOLOGY,
                    description=f"What technology/version does '{component.name}' run on?",
                    related_component_ids=[component.id],
                    priority=_PRIORITY[GapCategory.MISSING_TECHNOLOGY],
                )
            )

        if not component.criticality:
            gaps.append(
                Gap(
                    category=GapCategory.MISSING_CRITICALITY,
                    description=f"How business-critical is '{component.name}' (e.g. tier-1, best-effort)?",
                    related_component_ids=[component.id],
                    priority=_PRIORITY[GapCategory.MISSING_CRITICALITY],
                )
            )

    for assumption in model.assumptions:
        if assumption.raised_by == "llm":
            gaps.append(
                Gap(
                    category=GapCategory.UNCONFIRMED_ASSUMPTION,
                    description=f"Assuming: {assumption.text} — is that correct?",
                    related_component_ids=assumption.related_component_ids,
                    priority=_PRIORITY[GapCategory.UNCONFIRMED_ASSUMPTION],
                )
            )

    return sorted(gaps, key=lambda g: g.priority, reverse=True)


def top_gaps(model: ArchitectureModel, n: int = 3) -> list[Gap]:
    return analyze_gaps(model)[:n]

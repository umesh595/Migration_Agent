"""Discovery Confidence Score: a computed, auditable summary of how ready the
current model actually is for migration planning — deliberately deterministic
code, not an LLM judgment call, so the number can never be gamed or drift from
what the model actually contains ("don't fake the score — compute it from
fields completed, unresolved high-impact categories, dependency gaps, and
assumptions")."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.gap_analyzer import analyze_gaps
from app.schemas.architecture import ArchitectureModel, AssumptionStatus, Environment

# Gaps at or above this priority represent a genuine planning blocker (an
# explicit open question, a sparse-context intake gap, an unconnected
# component, or a high-risk requirement-coverage gap) — mirrors
# gap_analyzer._PRIORITY's own tiering rather than inventing a new threshold.
_BLOCKING_GAP_PRIORITY_FLOOR = 80


class DiscoveryConfidence(BaseModel):
    completeness_percent: int
    blocking_unknowns: int
    high_risk_assumptions: int
    ready_for_planning: bool


def compute_discovery_confidence(model: ArchitectureModel) -> DiscoveryConfidence:
    """Every number here is counted from fields actually present on `model` —
    never estimated, never LLM-scored. `completeness_percent` is the fraction
    of expected per-component/per-fact fields that are actually filled in;
    `blocking_unknowns` and `high_risk_assumptions` come straight from the same
    deterministic gap/evidence-ledger data the discovery loop itself reads
    every turn, so this can never say something is fine that discovery itself
    would still ask a question about."""

    gaps = analyze_gaps(model)
    blocking_unknowns = sum(1 for g in gaps if g.priority >= _BLOCKING_GAP_PRIORITY_FLOOR)
    high_risk_assumptions = sum(
        1 for a in model.assumptions if a.raised_by == "llm" and a.text.startswith("FLAGGED RISK")
    )

    total_fields = 0
    filled_fields = 0

    for component in model.components:
        total_fields += 2  # environment, criticality
        if component.environment != Environment.UNKNOWN:
            filled_fields += 1
        if component.criticality:
            filled_fields += 1

    if len(model.components) > 1:
        connected = {d.source_id for d in model.dependencies} | {d.target_id for d in model.dependencies}
        for component in model.components:
            total_fields += 1
            if component.id in connected:
                filled_fields += 1

    for assumption in model.assumptions:
        if assumption.raised_by != "llm":
            continue
        total_fields += 1
        if assumption.status != AssumptionStatus.OPEN:
            filled_fields += 1

    for question in model.open_questions:
        total_fields += 1
        if question.resolved:
            filled_fields += 1

    # total_fields is 0 only for a genuinely blank model (no components, no
    # assumptions, no open questions) — that is 0% complete, not 100%; every
    # component always contributes at least 2 fields, so this can't trigger
    # once anything has actually been captured.
    completeness_percent = 0 if total_fields == 0 else round(100 * filled_fields / total_fields)
    ready_for_planning = bool(model.components) and blocking_unknowns == 0 and high_risk_assumptions == 0

    return DiscoveryConfidence(
        completeness_percent=completeness_percent,
        blocking_unknowns=blocking_unknowns,
        high_risk_assumptions=high_risk_assumptions,
        ready_for_planning=ready_for_planning,
    )

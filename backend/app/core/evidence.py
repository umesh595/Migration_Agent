"""Evidence Ledger (Discovery Agent upgrade): every LLM-inferred or externally
sourced fact already lives as an Assumption or Dependency with a status/
confidence/source (see app.schemas.architecture) — this module adds the one
remaining piece, and it must stay deterministic code, never LLM-authored:
which downstream planning artifacts actually consume a given fact RIGHT NOW.

This is computed fresh from the model's current structure on every read
rather than stored on the fact itself, because a stored "used_by" would
silently go stale the moment the model changes shape around it (a component
gets removed, the model moves from discovery into planning) — a wrong answer
here is worse than none, so it is never allowed to become one."""

from __future__ import annotations

from app.schemas.architecture import ArchitectureModel, Assumption, AssumptionStatus, Dependency, ModelStatus


def assumption_used_by(assumption: Assumption, model: ArchitectureModel) -> list[str]:
    """What actually reads this assumption right now. An OPEN assumption is
    still just a guess awaiting confirmation — nothing downstream should be
    treated as resting on it yet (that is exactly why GapAnalyzer keeps
    surfacing it). A REJECTED one feeds nothing either: it is a corrected
    dead end, kept only for audit history."""

    if assumption.status != AssumptionStatus.CONFIRMED:
        return []

    used = ["gap analysis"]
    related = [c for cid in assumption.related_component_ids if (c := model.get_component(cid)) is not None]
    if related:
        used.append("component planning")
        if any(c.criticality for c in related):
            used.append("criticality classification")
    if model.status == ModelStatus.ACCEPTED:
        used.append("migration wave sequencing")
    return used


def dependency_used_by(dependency: Dependency, model: ArchitectureModel) -> list[str]:
    """A dependency is a structural fact the moment it exists — unlike an
    assumption there is no separate 'confirmed' gate, so this only branches on
    whether planning has actually started consuming the dependency graph yet."""

    used = ["dependency graph"]
    if model.status == ModelStatus.ACCEPTED:
        used.extend(["migration wave sequencing", "coexistence and rollback planning"])
    return used

"""Verifies a MigrationPlan accounts for every component in the frozen model. Runs
before a draft plan is ever shown to the user — a plan that silently drops a
component is worse than one that's late."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.architecture import ArchitectureModel
from app.schemas.migration_plan import MigrationPlan


@dataclass
class CoverageResult:
    missing_mappings: list[str] = field(default_factory=list)
    missing_plans: list[str] = field(default_factory=list)
    unassigned_to_wave: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not (self.missing_mappings or self.missing_plans or self.unassigned_to_wave)


def check_coverage(model: ArchitectureModel, plan: MigrationPlan) -> CoverageResult:
    component_ids = model.component_ids()
    mapped_ids = {m.component_id for m in plan.component_mappings}
    planned_ids = {p.component_id for p in plan.component_plans}
    waved_ids = {cid for w in plan.waves for cid in w.component_ids}

    return CoverageResult(
        missing_mappings=sorted(component_ids - mapped_ids),
        missing_plans=sorted(component_ids - planned_ids),
        unassigned_to_wave=sorted(component_ids - waved_ids),
    )

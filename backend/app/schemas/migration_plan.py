"""The canonical MigrationPlan. Fields map 1:1 onto the PoC brief's 10 deliverables
(see DECISIONS.md for how 'Validation Approach' and 'Migration Roadmap' — the two
deliverables with no natural field in the original design — were given typed homes
instead of being synthesized as export-time prose)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SevenR(StrEnum):
    REHOST = "rehost"
    REPLATFORM = "replatform"
    REPURCHASE = "repurchase"
    REFACTOR = "refactor"
    RETAIN = "retain"
    RETIRE = "retire"
    RELOCATE = "relocate"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ComponentMapping(BaseModel):
    """Deliverable 3 — Component Mapping."""

    component_id: str
    target_description: str
    disposition: SevenR


class ValidationCheck(BaseModel):
    description: str
    check_type: str = Field(description="e.g. 'smoke_test', 'data_parity', 'load_test', 'manual_signoff'.")


class ComponentPlan(BaseModel):
    """Deliverable 4 — Component Migration Approach. Planned by the LLM, but only
    ever *within* the wave that compute_sequence already fixed (technique #6) —
    the model plans HOW, never WHEN relative to other components."""

    component_id: str
    disposition: SevenR
    wave_index: int = Field(description="Assigned by GraphEngine before this plan exists; immutable here.")
    steps: list[str]
    validation_checks: list[ValidationCheck]
    rollback_notes: str
    estimated_effort: str | None = Field(default=None, description="Free-text, e.g. '3-5 days'.")
    dependencies_considered: list[str] = Field(default_factory=list)


class CoexistenceGroup(BaseModel):
    """Components whose mutual dependency cycle means they must move together,
    or whose target-environment split requires a defined coexistence period."""

    component_ids: list[str]
    reason: str
    coexistence_strategy: str


class Wave(BaseModel):
    """Deliverable 5 — Migration Sequence. Computed by GraphEngine.compute_sequence
    via topological sort; never proposed by the LLM (technique #5)."""

    index: int
    component_ids: list[str]
    rationale: str
    coexistence_groups: list[CoexistenceGroup] = Field(default_factory=list)


class Risk(BaseModel):
    """Deliverable 6 (part) — Risks & Assumptions."""

    id: str
    description: str
    severity: RiskSeverity
    mitigation: str
    related_component_ids: list[str] = Field(default_factory=list)
    source: str = Field(description="'rule:<RULE-ID>', 'llm_review', or 'unresolved_finding'.")


class CutoverStrategy(BaseModel):
    """Deliverable 8 — Cutover Strategy."""

    approach: str = Field(description="e.g. 'blue-green', 'phased-by-wave', 'big-bang'.")
    steps: list[str]
    go_no_go_criteria: list[str]
    communication_plan: str


class RollbackStrategy(BaseModel):
    """Deliverable 9 — Rollback Strategy."""

    approach: str
    triggers: list[str] = Field(description="Conditions that invoke rollback.")
    steps: list[str]
    data_reconciliation_notes: str | None = None


class ValidationSummary(BaseModel):
    """Deliverable 7 — Validation Approach, as a first-class field (see DECISIONS.md).
    Computed by PlanAssembler by rolling up component_plans[].validation_checks plus
    cross-cutting checks that only make sense at the plan level (end-to-end,
    cross-wave data-consistency checks) — never freehand LLM prose."""

    overall_strategy: str
    cross_component_checks: list[ValidationCheck] = Field(default_factory=list)
    sign_off_gates: list[str] = Field(default_factory=list)


class RoadmapItem(BaseModel):
    """One row of Deliverable 10 — Migration Roadmap. Derived by PlanAssembler from
    waves[] + component_plans[], not re-generated as fresh prose (see DECISIONS.md)."""

    wave_index: int
    component_id: str
    disposition: SevenR
    summary: str
    owner_placeholder: str = "TBD"
    estimated_effort: str | None = None
    depends_on_waves: list[int] = Field(default_factory=list)


class PlanStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    FINAL = "final"


class MigrationPlan(BaseModel):
    target_architecture_description: str = Field(description="Deliverable 2 — Target Architecture narrative.")
    component_mappings: list[ComponentMapping] = Field(default_factory=list)
    component_plans: list[ComponentPlan] = Field(default_factory=list)
    waves: list[Wave] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    cutover_strategy: CutoverStrategy | None = None
    rollback_strategy: RollbackStrategy | None = None
    validation_summary: ValidationSummary | None = None
    roadmap_items: list[RoadmapItem] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    version: int = 1

    def component_plan_for(self, component_id: str) -> ComponentPlan | None:
        return next((p for p in self.component_plans if p.component_id == component_id), None)

"""Deterministic review rules RULE-001..007 (technique #8). Runs at zero token cost
before the LLM critic sees anything — roughly 80% of review checks are mechanical and
belong here, not in a prompt."""

from __future__ import annotations

from collections.abc import Callable

from app.core.coverage_checker import check_coverage
from app.core.graph_engine import compute_cross_wave_dependencies
from app.schemas.architecture import ArchitectureModel, Environment
from app.schemas.findings import Finding, FindingSeverity, FindingSource
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import MigrationPlan, SevenR


def _rule_001_sequencing_validity(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    """Belt-and-suspenders re-check (Doc 3 §3.3): for every dependency, the source's
    wave must not be scheduled before the target's wave."""

    wave_of = {cid: w.index for w in plan.waves for cid in w.component_ids}
    findings = []
    for dep in model.dependencies:
        source_wave = wave_of.get(dep.source_id)
        target_wave = wave_of.get(dep.target_id)
        if source_wave is None or target_wave is None:
            continue
        if source_wave < target_wave:
            findings.append(
                Finding(
                    id=f"RULE-001-{dep.id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-001",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"'{dep.source_id}' is scheduled in wave {source_wave} but depends on "
                        f"'{dep.target_id}' which isn't scheduled until wave {target_wave} — invalid order"
                    ),
                    related_component_ids=[dep.source_id, dep.target_id],
                )
            )
    return findings


def _rule_002_coverage_completeness(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    result = check_coverage(model, plan)
    findings = []
    for cid in result.missing_mappings:
        findings.append(
            Finding(
                id=f"RULE-002-mapping-{cid}",
                source=FindingSource.RULE,
                rule_id="RULE-002",
                severity=FindingSeverity.ERROR,
                message=f"component '{cid}' has no component mapping in the plan",
                related_component_ids=[cid],
            )
        )
    for cid in result.missing_plans:
        findings.append(
            Finding(
                id=f"RULE-002-plan-{cid}",
                source=FindingSource.RULE,
                rule_id="RULE-002",
                severity=FindingSeverity.ERROR,
                message=f"component '{cid}' has no per-component migration plan",
                related_component_ids=[cid],
            )
        )
    for cid in result.unassigned_to_wave:
        findings.append(
            Finding(
                id=f"RULE-002-wave-{cid}",
                source=FindingSource.RULE,
                rule_id="RULE-002",
                severity=FindingSeverity.ERROR,
                message=f"component '{cid}' is not assigned to any migration wave",
                related_component_ids=[cid],
            )
        )
    return findings


def _rule_003_no_dangling_retirements(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    retired_ids = {m.component_id for m in plan.component_mappings if m.disposition == SevenR.RETIRE}
    findings = []
    for dep in model.dependencies:
        if dep.target_id in retired_ids and dep.source_id not in retired_ids:
            findings.append(
                Finding(
                    id=f"RULE-003-{dep.id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-003",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"'{dep.target_id}' is marked RETIRE but '{dep.source_id}' still depends on it "
                        "and is not itself being retired"
                    ),
                    related_component_ids=[dep.source_id, dep.target_id],
                )
            )
    return findings


def _rule_004_rollback_present(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    findings = []
    if plan.rollback_strategy is None or not plan.rollback_strategy.steps:
        findings.append(
            Finding(
                id="RULE-004-plan-level",
                source=FindingSource.RULE,
                rule_id="RULE-004",
                severity=FindingSeverity.ERROR,
                message="plan has no plan-level rollback strategy with concrete steps",
            )
        )
    for cp in plan.component_plans:
        if not cp.rollback_notes.strip():
            findings.append(
                Finding(
                    id=f"RULE-004-{cp.component_id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-004",
                    severity=FindingSeverity.ERROR,
                    message=f"component '{cp.component_id}' has no rollback notes",
                    related_component_ids=[cp.component_id],
                )
            )
    return findings


def _rule_005_validation_and_cutover_present(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    findings = []
    if plan.cutover_strategy is None or not plan.cutover_strategy.go_no_go_criteria:
        findings.append(
            Finding(
                id="RULE-005-cutover",
                source=FindingSource.RULE,
                rule_id="RULE-005",
                severity=FindingSeverity.ERROR,
                message="plan has no cutover strategy with go/no-go criteria",
            )
        )
    for cp in plan.component_plans:
        if not cp.validation_checks:
            findings.append(
                Finding(
                    id=f"RULE-005-{cp.component_id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-005",
                    severity=FindingSeverity.ERROR,
                    message=f"component '{cp.component_id}' has no validation checks defined",
                    related_component_ids=[cp.component_id],
                )
            )
    return findings


def _rule_006_mapping_plan_disposition_consistency(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    plans_by_id = {p.component_id: p for p in plan.component_plans}
    findings = []
    for mapping in plan.component_mappings:
        component_plan = plans_by_id.get(mapping.component_id)
        if component_plan is not None and component_plan.disposition != mapping.disposition:
            findings.append(
                Finding(
                    id=f"RULE-006-{mapping.component_id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-006",
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"'{mapping.component_id}' has disposition '{mapping.disposition}' in its mapping "
                        f"but '{component_plan.disposition}' in its component plan"
                    ),
                    related_component_ids=[mapping.component_id],
                )
            )
    return findings


def _rule_007_cross_wave_coexistence_covered(model: ArchitectureModel, plan: MigrationPlan) -> list[Finding]:
    required = compute_cross_wave_dependencies(model, plan.waves)
    documented_pairs = {
        frozenset(g.component_ids)
        for w in plan.waves
        for g in w.coexistence_groups
    }
    findings = []
    for group in required:
        if frozenset(group.component_ids) not in documented_pairs:
            findings.append(
                Finding(
                    id=f"RULE-007-{'-'.join(sorted(group.component_ids))}",
                    source=FindingSource.RULE,
                    rule_id="RULE-007",
                    severity=FindingSeverity.WARNING,
                    message=f"{group.reason} — no coexistence strategy documented for this cross-wave dependency",
                    related_component_ids=group.component_ids,
                )
            )
    return findings


def _rule_008_source_environment_consistency(model: ArchitectureModel, context: MigrationContext) -> list[Finding]:
    """A discovered component tagged environment=cloud (or hybrid) while the elicited
    migration context claims a pure on-prem source (or vice versa) usually means the
    current-state description and the migration context were captured inconsistently
    — e.g. someone described an already-cloud-native piece while the overall source
    was declared on_prem. Left undetected, this silently corrupts every downstream
    disposition and coexistence decision, so it's flagged for the user to reconcile
    rather than guessed at."""

    if context.source_environment not in (Environment.ON_PREM, Environment.CLOUD):
        return []

    contradicting = {Environment.ON_PREM: Environment.CLOUD, Environment.CLOUD: Environment.ON_PREM}[
        context.source_environment
    ]
    findings = []
    for component in model.components:
        if component.environment == contradicting:
            findings.append(
                Finding(
                    id=f"RULE-008-{component.id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-008",
                    severity=FindingSeverity.INFO,
                    message=(
                        f"'{component.id}' is tagged environment='{component.environment}' but the migration "
                        f"context declares the source environment as '{context.source_environment}' — verify "
                        "whether this component was mislabeled during discovery or the source is actually hybrid"
                    ),
                    related_component_ids=[component.id],
                )
            )
    return findings


_RULES: list[Callable[[ArchitectureModel, MigrationPlan], list[Finding]]] = [
    _rule_001_sequencing_validity,
    _rule_002_coverage_completeness,
    _rule_003_no_dangling_retirements,
    _rule_004_rollback_present,
    _rule_005_validation_and_cutover_present,
    _rule_006_mapping_plan_disposition_consistency,
    _rule_007_cross_wave_coexistence_covered,
]


def run_rules(model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(model, plan))
    if context is not None:
        findings.extend(_rule_008_source_environment_consistency(model, context))
    return findings

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
                    violated_requirement=(
                        f"dependency '{dep.id}' ({dep.kind}) requires '{dep.target_id}' to be available no "
                        f"later than the wave that migrates '{dep.source_id}'"
                    ),
                    suggested_fix=(
                        f"Move '{dep.source_id}' to wave {target_wave} or later, or move '{dep.target_id}' to "
                        f"wave {source_wave} or earlier."
                    ),
                    risk_if_ignored=(
                        f"'{dep.source_id}' migrates before '{dep.target_id}' exists in the target environment "
                        "and will fail or silently misbehave at cutover."
                    ),
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
                violated_requirement=f"every discovered component ('{cid}' included) must have a component mapping before review can pass",
                suggested_fix=f"Add a component_mapping entry for '{cid}' with its disposition and target service.",
                risk_if_ignored=f"'{cid}' has no recorded migration path and will be silently left behind at cutover.",
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
                violated_requirement=f"every discovered component ('{cid}' included) must have a per-component plan with concrete steps",
                suggested_fix=f"Generate a component plan for '{cid}' (steps, validation checks, rollback notes).",
                risk_if_ignored=f"No one knows HOW '{cid}' actually moves — cutover for it has no defined procedure.",
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
                violated_requirement=f"every discovered component ('{cid}' included) must be assigned to a migration wave",
                suggested_fix=f"Assign '{cid}' to a wave consistent with its dependencies.",
                risk_if_ignored=f"'{cid}' has no scheduled migration date and will not move with the rest of the system.",
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
                    violated_requirement=f"a component marked RETIRE ('{dep.target_id}') must have no remaining active dependents",
                    suggested_fix=(
                        f"Either retire '{dep.source_id}' too, or change '{dep.target_id}''s disposition away "
                        "from RETIRE (e.g. keep it running until its last dependent moves)."
                    ),
                    risk_if_ignored=(
                        f"'{dep.source_id}' will call '{dep.target_id}' after it no longer exists, causing a "
                        "runtime failure the first time that dependency is exercised post-cutover."
                    ),
                )
            )
    return findings


def _rule_004_rollback_present(
    model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext | None = None
) -> list[Finding]:
    """Evidence-backed review upgrade: this is the rule the "rollback is weak"
    example is built around. A missing rollback plan is worse the more it
    actually matters — a tier-1 component under a zero-downtime constraint has
    no room for an unplanned outage, so that combination is called out
    explicitly rather than treated the same as a low-stakes gap."""

    findings = []
    zero_downtime = context is not None and str(context.downtime_tolerance) == "zero_downtime"
    if plan.rollback_strategy is None or not plan.rollback_strategy.steps:
        tier1_ids = [c.id for c in model.components if c.criticality and "1" in c.criticality]
        violated = "the plan must define a plan-level rollback strategy with concrete steps"
        risk = "a failed cutover has no defined path back to a known-good state."
        if zero_downtime and tier1_ids:
            violated += (
                f", and the accepted model marks {', '.join(tier1_ids)} as tier-1 (business-critical) "
                "while the migration context states downtime_tolerance is zero_downtime"
            )
            risk = (
                f"a failed cutover would have no path back to a consistent state, and "
                f"{', '.join(tier1_ids)} — tier-1 under a zero-downtime constraint — would be down with no rollback."
            )
        findings.append(
            Finding(
                id="RULE-004-plan-level",
                source=FindingSource.RULE,
                rule_id="RULE-004",
                severity=FindingSeverity.ERROR,
                message="plan has no plan-level rollback strategy with concrete steps",
                related_component_ids=tier1_ids,
                violated_requirement=violated,
                suggested_fix="Add a plan-level rollback_strategy with concrete steps (and a rollback trigger/go-no-go check) before Gate 2.",
                risk_if_ignored=risk,
            )
        )
    for cp in plan.component_plans:
        if not cp.rollback_notes.strip():
            component = model.get_component(cp.component_id)
            is_tier1 = bool(component and component.criticality and "1" in component.criticality)
            violated = f"component '{cp.component_id}' must have rollback_notes describing how to undo this component's migration"
            risk = f"'{cp.component_id}' has no undo procedure if its migration fails partway through."
            if zero_downtime and is_tier1:
                violated += (
                    f" — the accepted model marks '{cp.component_id}' as tier-1 and the migration context "
                    "states downtime_tolerance is zero_downtime"
                )
                risk = (
                    f"'{cp.component_id}' is tier-1 under a zero-downtime constraint with no replica sync or "
                    "rollback trigger — a failed cutover leaves a business-critical component down with no way back."
                )
            findings.append(
                Finding(
                    id=f"RULE-004-{cp.component_id}",
                    source=FindingSource.RULE,
                    rule_id="RULE-004",
                    severity=FindingSeverity.ERROR,
                    message=f"component '{cp.component_id}' has no rollback notes",
                    related_component_ids=[cp.component_id],
                    violated_requirement=violated,
                    suggested_fix=f"Add rollback_notes for '{cp.component_id}' (e.g. replica sync direction, a rollback trigger, and the steps to revert).",
                    risk_if_ignored=risk,
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
                violated_requirement="the plan must define go_no_go_criteria before cutover can be executed responsibly",
                suggested_fix="Add concrete go/no-go criteria to the cutover_strategy (e.g. error-rate thresholds, data-parity checks).",
                risk_if_ignored="Cutover proceeds on judgment call alone, with no objective signal for when to abort.",
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
                    violated_requirement=f"component '{cp.component_id}' must define validation_checks to confirm a successful migration",
                    suggested_fix=f"Add validation_checks for '{cp.component_id}' (e.g. a smoke test, a data-parity check).",
                    risk_if_ignored=f"A broken migration of '{cp.component_id}' could go undetected until a real user hits it in production.",
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
                    violated_requirement=f"'{mapping.component_id}''s disposition must be the same in its component_mapping and its component_plan",
                    suggested_fix=f"Reconcile the two dispositions for '{mapping.component_id}' — pick one and update the other.",
                    risk_if_ignored="Cost estimates, effort estimates, and the actual plan steps disagree about what's actually happening to this component.",
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
                    violated_requirement=f"{group.reason}, so a coexistence strategy must be documented for how old and new stacks interoperate during the gap",
                    suggested_fix=f"Add a coexistence_group for {sorted(group.component_ids)} describing how they talk to each other across waves.",
                    risk_if_ignored="These components will sit in different waves with no documented plan for how they interoperate in between, risking a broken or inconsistent state mid-migration.",
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
                    violated_requirement=(
                        f"the accepted model tags '{component.id}' environment='{component.environment}' but "
                        f"the migration context declares source_environment='{context.source_environment}'"
                    ),
                    suggested_fix=(
                        f"Confirm with the user whether '{component.id}' was mislabeled during discovery, or "
                        "correct the migration context to 'hybrid' if the source genuinely spans both."
                    ),
                    risk_if_ignored=(
                        "Disposition and coexistence decisions for this component are based on the wrong "
                        "starting environment, which can silently invalidate its wave assignment and cutover approach."
                    ),
                )
            )
    return findings


_RULES: list[Callable[[ArchitectureModel, MigrationPlan], list[Finding]]] = [
    _rule_001_sequencing_validity,
    _rule_002_coverage_completeness,
    _rule_003_no_dangling_retirements,
    _rule_005_validation_and_cutover_present,
    _rule_006_mapping_plan_disposition_consistency,
    _rule_007_cross_wave_coexistence_covered,
]


def run_rules(model: ArchitectureModel, plan: MigrationPlan, context: MigrationContext | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(model, plan))
    # Called separately (not via _RULES) since it needs `context` to enrich its
    # evidence when a tier-1 component meets a zero-downtime constraint —
    # still runs even without context, just without that extra specificity.
    findings.extend(_rule_004_rollback_present(model, plan, context))
    if context is not None:
        findings.extend(_rule_008_source_environment_consistency(model, context))
    return findings

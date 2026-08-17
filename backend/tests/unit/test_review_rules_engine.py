from app.core.review_rules_engine import run_rules
from app.schemas.architecture import ArchitectureModel, Component, Dependency
from app.schemas.migration_plan import (
    ComponentMapping,
    ComponentPlan,
    CutoverStrategy,
    MigrationPlan,
    RollbackStrategy,
    ValidationCheck,
    Wave,
)


def _component(cid: str) -> Component:
    return Component(id=cid, name=cid, workload_type="other")


def _complete_plan(model: ArchitectureModel, waves: list[Wave]) -> MigrationPlan:
    wave_of = {cid: w.index for w in waves for cid in w.component_ids}
    return MigrationPlan(
        target_architecture_description="target",
        component_mappings=[
            ComponentMapping(component_id=c.id, target_description="t", disposition="rehost")
            for c in model.components
        ],
        component_plans=[
            ComponentPlan(
                component_id=c.id,
                disposition="rehost",
                wave_index=wave_of[c.id],
                steps=["do it"],
                validation_checks=[ValidationCheck(description="check", check_type="smoke_test")],
                rollback_notes="revert",
            )
            for c in model.components
        ],
        waves=waves,
        cutover_strategy=CutoverStrategy(
            approach="phased", steps=["go"], go_no_go_criteria=["all green"], communication_plan="email"
        ),
        rollback_strategy=RollbackStrategy(approach="revert", triggers=["errors"], steps=["revert"]),
    )


def test_complete_plan_has_no_rule_findings():
    model = ArchitectureModel(components=[_component("a"), _component("b")])
    waves = [Wave(index=0, component_ids=["a"], rationale="r"), Wave(index=1, component_ids=["b"], rationale="r")]
    plan = _complete_plan(model, waves)

    findings = run_rules(model, plan)
    assert findings == []


def test_rule_001_flags_invalid_sequencing():
    model = ArchitectureModel(
        components=[_component("api"), _component("db")],
        dependencies=[Dependency(id="d1", source_id="api", target_id="db", kind="data_read")],
    )
    # Deliberately WRONG: api (depends on db) scheduled before db
    waves = [Wave(index=0, component_ids=["api"], rationale="r"), Wave(index=1, component_ids=["db"], rationale="r")]
    plan = _complete_plan(model, waves)

    findings = run_rules(model, plan)
    assert any(f.rule_id == "RULE-001" for f in findings)


def test_rule_002_flags_missing_coverage():
    model = ArchitectureModel(components=[_component("a"), _component("b")])
    plan = MigrationPlan(target_architecture_description="t")  # nothing assembled

    findings = run_rules(model, plan)
    rule_ids = {f.rule_id for f in findings}
    assert "RULE-002" in rule_ids


def test_rule_003_flags_dangling_retirement():
    model = ArchitectureModel(
        components=[_component("legacy_db"), _component("service")],
        dependencies=[Dependency(id="d1", source_id="service", target_id="legacy_db", kind="data_read")],
    )
    waves = [Wave(index=0, component_ids=["legacy_db"], rationale="r"), Wave(index=1, component_ids=["service"], rationale="r")]
    plan = _complete_plan(model, waves)
    for m in plan.component_mappings:
        if m.component_id == "legacy_db":
            m.disposition = "retire"

    findings = run_rules(model, plan)
    assert any(f.rule_id == "RULE-003" for f in findings)


def test_rule_004_flags_missing_rollback():
    model = ArchitectureModel(components=[_component("a")])
    waves = [Wave(index=0, component_ids=["a"], rationale="r")]
    plan = _complete_plan(model, waves)
    plan.rollback_strategy = None

    findings = run_rules(model, plan)
    assert any(f.rule_id == "RULE-004" for f in findings)


def test_rule_006_flags_mapping_plan_disposition_mismatch():
    model = ArchitectureModel(components=[_component("a")])
    waves = [Wave(index=0, component_ids=["a"], rationale="r")]
    plan = _complete_plan(model, waves)
    plan.component_mappings[0].disposition = "retire"  # plan says rehost

    findings = run_rules(model, plan)
    assert any(f.rule_id == "RULE-006" for f in findings)

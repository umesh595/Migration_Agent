"""Turns the PRD's Performance NFR from an assumed target into a measured, enforced
one: 'Deterministic nodes (patch application, gap analysis, sequencing, rules
review): under 100 ms each at the scale cap.' Previously this was listed as a
'design target (to be measured, not assumed)' — this suite is the measurement.

Deliberately does NOT raise MAX_COMPONENTS/MAX_DEPENDENCIES. Doing so without real
load-testing against actual LLM latency (which nothing in this repo can honestly
simulate — the LLM boundary is mocked everywhere else for determinism) would be an
unverified promise, worse than leaving the cap alone. This suite proves the
deterministic core comfortably clears its target at the CURRENT cap; raising the
cap is a separate decision that needs real infra benchmarking, not a number bump.
"""

from __future__ import annotations

import time

from app.core.coverage_checker import check_coverage
from app.core.gap_analyzer import top_gaps
from app.core.graph_engine import compute_sequence
from app.core.patch_applier import apply_patch_set
from app.core.plan_assembler import assemble_plan
from app.core.review_rules_engine import run_rules
from app.llm.schemas import ComponentPlanLLMOutput, CutoverReviewOutput, RollbackPlanOutput
from app.schemas.architecture import ArchitectureModel, Component, Dependency
from app.schemas.migration_plan import ValidationCheck
from app.schemas.patches import AddComponentPatch, PatchSet

# The v1 scale envelope (DECISIONS.md / MAX_COMPONENTS / MAX_DEPENDENCIES).
COMPONENT_COUNT = 50
DEPENDENCY_COUNT = 200

# The PRD's literal number. Pure dict/list/networkx operations on 50 nodes should
# clear this with wide margin on any real hardware — if this ever gets flaky in CI,
# that is itself signal the deterministic core regressed, not a reason to loosen it.
PER_NODE_BUDGET_MS = 100.0


def _at_cap_model() -> ArchitectureModel:
    components = [
        Component(id=f"c{i}", name=f"Component {i}", workload_type="api_service")
        for i in range(COMPONENT_COUNT)
    ]

    dependencies: list[Dependency] = []
    seen: set[tuple[str, str]] = set()
    # Deterministic pseudo-random-ish spread of edges across the node set so the
    # graph isn't a single trivial chain — still produces a DAG (i -> j only when
    # j's index is "ahead" in this modular ordering), which is the common case;
    # compute_sequence handles cycles too but a cap-sized cycle test isn't the point here.
    for i in range(COMPONENT_COUNT):
        for step in (1, 3, 7, 11, 17):
            j = (i + step) % COMPONENT_COUNT
            if i == j:
                continue
            pair = (f"c{i}", f"c{j}")
            if pair in seen:
                continue
            seen.add(pair)
            dependencies.append(Dependency(id=f"c{i}->c{j}", source_id=f"c{i}", target_id=f"c{j}", kind="sync_call"))
            if len(dependencies) >= DEPENDENCY_COUNT:
                break
        if len(dependencies) >= DEPENDENCY_COUNT:
            break

    assert len(dependencies) == DEPENDENCY_COUNT, "test fixture must actually hit the dependency cap"
    return ArchitectureModel(components=components, dependencies=dependencies)


def _timed_ms(fn) -> tuple[float, object]:
    start = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, result


def test_gap_analysis_under_budget_at_cap():
    model = _at_cap_model()
    elapsed_ms, _ = _timed_ms(lambda: top_gaps(model, n=3))
    assert elapsed_ms < PER_NODE_BUDGET_MS, f"gap analysis took {elapsed_ms:.1f}ms at the 50/200 cap"


def test_sequencing_under_budget_at_cap():
    model = _at_cap_model()
    elapsed_ms, waves = _timed_ms(lambda: compute_sequence(model))
    assert elapsed_ms < PER_NODE_BUDGET_MS, f"compute_sequence took {elapsed_ms:.1f}ms at the 50/200 cap"
    assert sum(len(w.component_ids) for w in waves) == COMPONENT_COUNT


def test_incremental_patch_application_under_budget_at_cap():
    """The realistic steady-state case: one more patch against an already-at-cap
    model — not bulk-constructing all 250 patches at once (that's setup, not the
    per-turn operation the NFR is actually about)."""

    model = _at_cap_model()
    one_more_component = PatchSet(
        patches=[AddComponentPatch(id="c_extra", name="Extra", workload_type="api_service")],
        narration="",
    )
    elapsed_ms, (new_model, results) = _timed_ms(lambda: apply_patch_set(model, one_more_component))
    assert elapsed_ms < PER_NODE_BUDGET_MS, f"incremental patch application took {elapsed_ms:.1f}ms at the cap"
    assert results[0].outcome == "applied"
    assert len(new_model.components) == COMPONENT_COUNT + 1


def test_rules_review_under_budget_at_cap():
    model = _at_cap_model()
    waves = compute_sequence(model)
    wave_of = {cid: w.index for w in waves for cid in w.component_ids}

    outputs = [
        ComponentPlanLLMOutput(
            component_id=c.id, target_description="t", disposition="rehost", steps=["migrate"],
            validation_checks=[ValidationCheck(description="smoke", check_type="smoke_test")],
            rollback_notes="revert",
            dependencies_considered=[d.target_id for d in model.dependencies if d.source_id == c.id],
        )
        for c in model.components
    ]
    plan = assemble_plan(
        model, waves, outputs, "target architecture",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    assert all(p.wave_index == wave_of[p.component_id] for p in plan.component_plans)

    elapsed_ms, findings = _timed_ms(lambda: run_rules(model, plan))
    assert elapsed_ms < PER_NODE_BUDGET_MS, f"run_rules took {elapsed_ms:.1f}ms at the 50/200 cap"
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], errors  # a well-formed at-cap plan should still be clean


def test_coverage_check_under_budget_at_cap():
    model = _at_cap_model()
    waves = compute_sequence(model)
    outputs = [
        ComponentPlanLLMOutput(
            component_id=c.id, target_description="t", disposition="rehost", steps=["migrate"],
            validation_checks=[ValidationCheck(description="smoke", check_type="smoke_test")], rollback_notes="revert",
        )
        for c in model.components
    ]
    plan = assemble_plan(
        model, waves, outputs, "target",
        cutover=CutoverReviewOutput(approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"),
        rollback=RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"]),
    )
    elapsed_ms, result = _timed_ms(lambda: check_coverage(model, plan))
    assert elapsed_ms < PER_NODE_BUDGET_MS, f"check_coverage took {elapsed_ms:.1f}ms at the 50/200 cap"
    assert result.is_complete

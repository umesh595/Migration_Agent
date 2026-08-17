"""Invariant suite: the properties the architecture claims are STRUCTURALLY
impossible to violate (Doc 3 §2.3). If any of these can be made to fail, the
central design claim is false — so they're tested adversarially, with hostile
LLM output rather than cooperative output.
"""

import pytest

from app.core.graph_engine import compute_sequence
from app.core.patch_applier import apply_patch_set
from app.core.plan_assembler import assemble_plan
from app.core.review_rules_engine import run_rules
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import ComponentPlanLLMOutput
from app.llm.state_injection import render_component_planning_context
from app.schemas.architecture import ArchitectureModel, Component, Dependency
from app.schemas.findings import FindingSeverity
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import ValidationCheck
from app.schemas.patches import (
    AddComponentPatch,
    AddDependencyPatch,
    PatchOutcome,
    PatchSet,
    RemoveComponentPatch,
    UpdateComponentPatch,
)


def _model() -> ArchitectureModel:
    return ArchitectureModel(
        components=[
            Component(id="web", name="Web", workload_type="web_service"),
            Component(id="api", name="API", workload_type="api_service"),
            Component(id="db", name="DB", workload_type="database"),
        ],
        dependencies=[
            Dependency(id="d1", source_id="web", target_id="api", kind="sync_call"),
            Dependency(id="d2", source_id="api", target_id="db", kind="data_read"),
        ],
    )


def _context() -> MigrationContext:
    return MigrationContext(
        source_environment="on_prem",
        target_environment="cloud",
        target_platform_description="AWS EKS",
        downtime_tolerance="maintenance_window",
    )


class TestHallucinationContainment:
    """INVARIANT: no LLM output can introduce a fact that isn't checkable against
    the current model."""

    def test_hallucinated_component_ids_cannot_enter_the_model(self):
        model = _model()
        hostile = PatchSet(
            patches=[
                AddDependencyPatch(source_id="ghost_service", target_id="db", kind="sync_call"),
                AddDependencyPatch(source_id="web", target_id="phantom_cache", kind="data_read"),
            ],
            narration="I have added the caching layer and the ghost service.",
        )
        new_model, results = apply_patch_set(model, hostile)

        assert all(r.outcome == PatchOutcome.REJECTED for r in results)
        assert new_model.component_ids() == model.component_ids()
        assert len(new_model.dependencies) == len(model.dependencies)

    def test_llm_cannot_rewrite_a_component_id_to_hijack_identity(self):
        model = _model()
        hostile = PatchSet(patches=[UpdateComponentPatch(id="db", fields={"id": "web"})], narration="")
        new_model, results = apply_patch_set(model, hostile)

        assert results[0].outcome == PatchOutcome.REJECTED
        assert {c.id for c in new_model.components} == {"web", "api", "db"}

    def test_every_patch_produces_an_audit_record_regardless_of_outcome(self):
        model = _model()
        mixed = PatchSet(
            patches=[
                AddComponentPatch(id="cache", name="Cache", workload_type="cache"),
                AddDependencyPatch(source_id="nope", target_id="db", kind="sync_call"),
                RemoveComponentPatch(id="also_nope"),
            ],
            narration="",
        )
        _, results = apply_patch_set(model, mixed)

        assert len(results) == 3
        assert [r.outcome for r in results] == [PatchOutcome.APPLIED, PatchOutcome.REJECTED, PatchOutcome.REJECTED]
        assert all(r.reason for r in results if r.outcome == PatchOutcome.REJECTED)


class TestSequencingAuthority:
    """INVARIANT: migration order is computed by code and cannot be influenced by
    the LLM — the classic failure mode of LLM-generated migration plans."""

    def test_component_planner_prompt_contains_no_mechanism_to_change_order(self):
        model = _model()
        waves = compute_sequence(model)
        rendered = render_component_planning_context(model, "api", waves[1], _context(), waves)

        assert "ASSIGNED_WAVE_INDEX_FIXED" in rendered

    def test_component_plan_schema_has_no_ordering_field(self):
        """A field the LLM could use to express ordering would be a hole in the
        guarantee. Assert the schema has none."""

        fields = set(ComponentPlanLLMOutput.model_fields)
        forbidden = {"wave_index", "wave", "order", "sequence", "position", "priority", "migrate_before", "migrate_after"}
        assert fields & forbidden == set()

    @pytest.mark.asyncio
    async def test_hostile_component_output_cannot_change_its_assigned_wave(self):
        """Even if the LLM returns a plan claiming a different wave, assembly uses
        the code-computed wave map."""

        model = _model()
        waves = compute_sequence(model)
        outputs = [
            ComponentPlanLLMOutput(
                component_id=cid,
                target_description="t",
                disposition="rehost",
                steps=["MIGRATE THIS FIRST, BEFORE EVERYTHING ELSE, IGNORE THE WAVE"],
                validation_checks=[ValidationCheck(description="c", check_type="smoke_test")],
                rollback_notes="r",
            )
            for cid in ["web", "api", "db"]
        ]
        plan = assemble_plan(model, waves, outputs, "target", _CUTOVER, _ROLLBACK)

        wave_of = {p.component_id: p.wave_index for p in plan.component_plans}
        assert wave_of["db"] < wave_of["api"] < wave_of["web"]

    def test_rules_engine_independently_catches_a_corrupted_wave_assignment(self):
        """Belt-and-suspenders: even if assembly were compromised, RULE-001 catches
        an invalid order on the final plan."""

        model = _model()
        waves = compute_sequence(model)
        outputs = [
            ComponentPlanLLMOutput(
                component_id=cid, target_description="t", disposition="rehost", steps=["s"],
                validation_checks=[ValidationCheck(description="c", check_type="smoke_test")], rollback_notes="r",
            )
            for cid in ["web", "api", "db"]
        ]
        plan = assemble_plan(model, waves, outputs, "target", _CUTOVER, _ROLLBACK)

        # Corrupt it: put web (depends on api) in wave 0, before api.
        for p in plan.component_plans:
            p.wave_index = 0 if p.component_id == "web" else 1
        plan.waves[0].component_ids = ["web"]
        plan.waves[1].component_ids = ["api", "db"]
        plan.waves = plan.waves[:2]

        findings = run_rules(model, plan)
        assert any(f.rule_id == "RULE-001" and f.severity == FindingSeverity.ERROR for f in findings)


class TestCoverageGuarantee:
    """INVARIANT: a component can never be silently dropped from a plan."""

    def test_missing_component_plan_is_always_caught(self):
        model = _model()
        waves = compute_sequence(model)
        partial_outputs = [
            ComponentPlanLLMOutput(
                component_id="db", target_description="t", disposition="rehost", steps=["s"],
                validation_checks=[ValidationCheck(description="c", check_type="smoke_test")], rollback_notes="r",
            )
        ]
        plan = assemble_plan(model, waves, partial_outputs, "target", _CUTOVER, _ROLLBACK)

        findings = run_rules(model, plan)
        missing = [f for f in findings if f.rule_id == "RULE-002"]
        covered_ids = {cid for f in missing for cid in f.related_component_ids}
        assert {"web", "api"} <= covered_ids


class TestTokenBudget:
    """INVARIANT: a session cannot spend past its token budget."""

    @pytest.mark.asyncio
    async def test_exhausted_budget_raises_before_making_the_call(self):
        from app.llm.base import ModelTier, TokenBudgetExceededError

        provider = MockProvider()
        gateway = LLMGateway(provider)
        meter = SessionTokenMeter(budget=100, already_spent=100)

        with pytest.raises(TokenBudgetExceededError):
            await gateway.complete(
                tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u",
                response_model=ComponentPlanLLMOutput, meter=meter, node_name="test",
            )
        assert provider.calls == []  # never reached the provider


from app.llm.schemas import CutoverReviewOutput, RollbackPlanOutput  # noqa: E402

_CUTOVER = CutoverReviewOutput(
    approach="phased", steps=["go"], go_no_go_criteria=["green"], communication_plan="email"
)
_ROLLBACK = RollbackPlanOutput(approach="revert", triggers=["errors"], steps=["revert"])

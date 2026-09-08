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
from app.llm.prompts.registry import get_prompt
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

    def test_update_component_patch_schema_has_no_mechanism_to_rewrite_id(self):
        """Identity hijacking isn't just rejected by validation — the schema has no
        field through which the LLM could even attempt it. `fields` used to be a
        free-form dict (validated against an allow-list at runtime); it's now
        explicit named fields with no `id` among them, so the attack is
        structurally impossible rather than merely caught (see DECISIONS.md —
        this shape was also forced by OpenAI's Structured Outputs strict mode,
        which can't represent an open-ended dict at all)."""

        updatable_fields = set(UpdateComponentPatch.model_fields) - {"op", "id"}
        assert "id" not in updatable_fields
        assert updatable_fields == {"name", "description", "technology", "owner_team", "criticality", "environment"}

    def test_update_component_patch_cannot_alter_the_targeted_components_id_at_apply_time(self):
        model = _model()
        hostile = PatchSet(patches=[UpdateComponentPatch(id="db", name="Definitely Not DB")], narration="")
        new_model, results = apply_patch_set(model, hostile)

        assert results[0].outcome == PatchOutcome.APPLIED
        assert {c.id for c in new_model.components} == {"web", "api", "db"}
        assert new_model.get_component("db").name == "Definitely Not DB"

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
                target_cloud_provider="aws",
                target_service_category="compute_vm",
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
                component_id=cid, target_description="t", disposition="rehost",
                target_cloud_provider="aws", target_service_category="compute_vm", steps=["s"],
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
                component_id="db", target_description="t", disposition="rehost",
                target_cloud_provider="aws", target_service_category="compute_vm", steps=["s"],
                validation_checks=[ValidationCheck(description="c", check_type="smoke_test")], rollback_notes="r",
            )
        ]
        plan = assemble_plan(model, waves, partial_outputs, "target", _CUTOVER, _ROLLBACK)

        findings = run_rules(model, plan)
        missing = [f for f in findings if f.rule_id == "RULE-002"]
        covered_ids = {cid for f in missing for cid in f.related_component_ids}
        assert {"web", "api"} <= covered_ids


class TestSeniorArchitectPromptBehavior:
    """Prompt-level invariants for the product behavior that keeps the agent from
    behaving like a form filler or a blind patch applicator."""

    def test_ingest_prompt_requires_intent_classification_before_patching(self):
        prompt = get_prompt("ingest_patches")

        assert prompt.version == "v23"
        assert "FIRST, CLASSIFY THE USER'S INTENT BEFORE PATCHING" in prompt.system
        assert "HIGH-IMPACT ARCHITECTURE DECISION" in prompt.system
        assert "NEW UNSCOPED BUSINESS CAPABILITY" in prompt.system
        assert "Do not treat every imperative from the user as permission to mutate" in prompt.system
        assert "DETERMINISTIC REQUEST CLASSIFICATION" in prompt.system
        assert "intent=target_planning" in prompt.system
        assert "intent=proceed_with_assumptions" in prompt.system
        assert "ARCHITECTURAL STYLE, PATTERN, OR PROTOCOL NAME IS NEVER A COMPONENT" in prompt.system
        assert "ONCE THE USER NAMES CONCRETE FUNCTIONALITY THE SYSTEM PERFORMS" in prompt.system

    def test_ingest_prompt_protects_accepted_source_model_after_gate_1(self):
        prompt = get_prompt("ingest_patches")

        assert "CURRENT_STAGE: AFTER_GATE_1" in prompt.system
        assert "source architecture has already been" in prompt.system
        assert "accepted. Do NOT emit add/update/remove component/dependency patches" in prompt.system
        assert "revise the accepted source model or treat the request as a target-state" in prompt.system

    def test_question_prompt_filters_out_low_value_form_questions(self):
        prompt = get_prompt("generate_questions")

        assert prompt.version == "v10"
        assert "Never use generic boilerplate" in prompt.system
        assert "Would a different answer change wave order" in prompt.system
        assert "do not enumerate all component names" in prompt.system

    def test_question_prompt_produces_hypothesis_cards_and_answer_options(self):
        """Hypothesis Cards + multiple-choice-with-free-text: the agent should
        propose a reasoned best guess when it has one (not just ask a blank
        question), and offer 2-3 concrete answers to pick from — never
        fabricated filler options, and never forced onto genuinely open-ended
        questions that have no natural discrete answers."""

        prompt = get_prompt("generate_questions")
        assert "HYPOTHESIS CARDS" in prompt.system
        assert "fabricate a guess just to fill the field" in prompt.system
        assert "ANSWER OPTIONS" in prompt.system
        assert "never a filler option with no real chance of being right just to reach three" in prompt.system

    def test_semantic_review_prompt_checks_cost_efficiency_and_strategy_justification(self):
        prompt = get_prompt("semantic_review")

        assert prompt.version == "v3"
        assert "cost or efficiency claims" in prompt.system
        assert "why this over alternatives" in prompt.system
        assert "alter source architecture after Gate 1 without explicit user" in prompt.system

    def test_semantic_review_prompt_requires_evidence_backed_findings(self):
        """Review Upgrade: a finding must cite the specific fact it violates, a
        concrete fix, and a concrete consequence — 'rollback is weak' alone is
        not an acceptable finding anymore. Also pins that the judge now
        specifically penalizes findings with empty/generic evidence fields,
        not just vague messages."""

        prompt = get_prompt("semantic_review")
        assert "EVIDENCE-BACKED FINDINGS" in prompt.system
        assert "violated_requirement" in prompt.system
        assert "suggested_fix" in prompt.system
        assert "risk_if_ignored" in prompt.system

        judge = get_prompt("semantic_review_judge")
        assert judge.version == "v2"
        assert "violated_requirement field is empty" in judge.system
        assert "suggested_fix is empty or" in judge.system

    def test_review_discussion_prompt_requires_plan_grounding(self):
        prompt = get_prompt("review_discussion")

        assert prompt.version == "v1"
        assert "Name concrete affected components" in prompt.system
        assert "Do not claim cost savings" in prompt.system
        assert "Do not mutate the architecture model" in prompt.system

    def test_ingest_completeness_critic_does_not_flag_intentional_criticality_defaults(self):
        """A critic that flags correct, by-design behavior as an error trains
        everyone to ignore it — the exact failure mode the user called out
        live (repeated "invented facts" warnings for role-based criticality
        defaults that ingest_patches.py deliberately narrates instead of
        recording via add_assumption, per its own "Do NOT also emit an
        add_assumption/open_question for a role-based criticality default"
        rule). The critic must know about that carve-out, not contradict it."""

        prompt = get_prompt("ingest_completeness_critic")

        assert prompt.version == "v3"
        assert "DO NOT flag a component's `criticality` field as invented merely because" in prompt.system
        assert "expected, correct behavior, not a gap" in prompt.system
        assert "Absence of an add_assumption patch is never itself a defect" in prompt.system

    def test_requirement_coverage_prompts_drive_adaptive_risk_weighted_ranking(self):
        """Adaptive Question Ranking: risk_score must be reasoned holistically per
        category (business risk, migration impact, dependency uncertainty,
        security/privacy, planning-blocker level), not left at a flat default —
        otherwise 'ask the next best question' degrades back into 'ask questions
        in the order gaps happened to be generated,' which is the exact
        complaint this upgrade fixes. Also pins the domain-reasoning examples
        (payments/healthcare/marketplace) that replace a hardcoded per-domain
        checklist — illustrative anchors the LLM generalizes from, never a fixed
        lookup keyed off a literal domain name."""

        generator = get_prompt("assess_requirement_coverage")
        assert generator.version == "v6"
        assert "ADAPTIVE QUESTION RANKING" in generator.system
        assert "do not default every category to the same middling number" in generator.system
        assert "idempotent charge handling" in generator.system
        assert "never ask about a category from an example above that this system" in generator.system

        critic = get_prompt("requirement_coverage_critic")
        assert critic.version == "v4"
        assert "Also re-check risk_score itself on every verdict you keep" in critic.system

    def test_ingest_completeness_critic_detects_contradictions_not_just_gaps(self):
        """The Contradiction Detector upgrade: same LLM call as the completeness
        critic (shares its exact inputs — model before this turn, message,
        proposed patches — so this adds real intelligence without a second
        round-trip's latency). Must catch genuine 'both cannot be true' clashes
        while explicitly NOT flagging refinements, new components, or a hedge
        later confirmed more confidently."""

        prompt = get_prompt("ingest_completeness_critic")

        assert "CONTRADICTIONS" in prompt.system
        assert "all services run on AWS ECS" in prompt.system
        assert "zero downtime" in prompt.system
        assert "Do NOT flag" in prompt.system
        assert "a hedge (\"maybe X\") followed by" in prompt.system

    def test_high_impact_replatform_confirmation_is_crisp_not_a_wall_of_text(self):
        """Regression test for a real bad-output live with a weaker model: the
        high-impact-replatform confirmation question used to instruct dumping
        effort/cost/testing/rollback/team-skill impact into one paragraph,
        which produced exactly the wall-of-text confirmation this system
        exists to avoid. The rule must now mirror generate_questions' own
        one-question-plus-one-consequence discipline instead."""

        prompt = get_prompt("ingest_patches")

        assert prompt.version == "v23"
        assert "NEVER a paragraph enumerating every impact" in prompt.system
        assert "exactly two sentences" in prompt.system
        assert "SINGLE biggest consequence of this specific change" in prompt.system

    def test_question_prompt_reasons_in_known_unknown_blocker_buckets(self):
        """Question Prioritizer upgrade: the model must silently triage gaps into
        known/unknown/blocker-tier before writing anything, and only blocker-tier
        unknowns (ones that would change which migration strategy is even
        viable) become primary questions — not every computed gap treated as
        equally urgent."""

        prompt = get_prompt("generate_questions")

        assert prompt.version == "v10"
        assert "REASON IN THREE BUCKETS BEFORE WRITING ANYTHING" in prompt.system
        assert "BLOCKER is the subset of UNKNOWN" in prompt.system
        assert "NAMES THE DECISION IT UNLOCKS" in prompt.system

    def test_question_prompt_forbids_echoing_the_gap_description_verbatim(self):
        """Regression test for a real bad-output live with a weaker model: a
        gap's `description` carries reasoning guidance for the LLM (e.g. how to
        frame a question) and was observed leaking through verbatim as the
        user-facing question text when a weaker model failed to translate it.
        The prompt must explicitly forbid this and give the model a concrete
        self-check to catch it before returning."""

        prompt = get_prompt("generate_questions")

        assert "NEVER COPY A GAP'S OWN WORDING INTO THE QUESTION YOU RETURN" in prompt.system
        assert "BEFORE RETURNING, SILENTLY VERIFY EVERY QUESTION AGAINST THIS CHECKLIST" in prompt.system
        assert "talk ABOUT gaps" in prompt.system


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

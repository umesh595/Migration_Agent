"""Coverage for MigrationStrategyPreference — the fact that changes nearly every
downstream recommendation once it's known (lift-and-shift vs. re-architect vs.
undecided) but had no home in the schema at all until now.

Unlike the business-content this system otherwise derives entirely per-system (no
fixed topic lists — see test_prompt_neutrality.py), this is a structural axis every
migration project has regardless of domain, the same category as downtime_tolerance
already was. It's a real enum for that reason, not a hardcoded business assumption.
"""

from app.llm.prompts.registry import REGISTRY
from app.llm.schemas import MigrationContextElicitationOutput
from app.llm.state_injection import render_context_for_prompt
from app.orchestration.nodes.planning import _coerce_strategy_preference
from app.schemas.migration_context import MigrationContext, MigrationStrategyPreference


def _context(**overrides) -> MigrationContext:
    base = dict(
        source_environment="cloud",
        target_environment="cloud",
        target_platform_description="AWS",
        downtime_tolerance="flexible",
    )
    base.update(overrides)
    return MigrationContext(**base)


class TestSchema:
    def test_defaults_to_undecided_not_a_silent_guess(self):
        """undecided must be the default, never a guess toward either extreme —
        guessing here would corrupt every downstream recommendation exactly the
        way an unstated fact should not."""

        assert _context().strategy_preference == MigrationStrategyPreference.UNDECIDED

    def test_accepts_explicit_values(self):
        assert _context(strategy_preference="lift_and_shift").strategy_preference == MigrationStrategyPreference.LIFT_AND_SHIFT
        assert _context(strategy_preference="re_architect").strategy_preference == MigrationStrategyPreference.RE_ARCHITECT

    def test_elicitation_output_defaults_to_undecided_string(self):
        output = MigrationContextElicitationOutput(
            source_environment="cloud", target_environment="cloud",
            target_platform_description="AWS", downtime_tolerance="flexible",
        )
        assert output.strategy_preference == "undecided"


class TestCoercion:
    def test_valid_values_roundtrip(self):
        assert _coerce_strategy_preference("lift_and_shift") == MigrationStrategyPreference.LIFT_AND_SHIFT
        assert _coerce_strategy_preference("RE_ARCHITECT") == MigrationStrategyPreference.RE_ARCHITECT
        assert _coerce_strategy_preference(" undecided ") == MigrationStrategyPreference.UNDECIDED

    def test_garbage_falls_back_to_undecided_not_a_crash_or_a_guess(self):
        """A hallucinated value from the LLM must degrade to the same safe default
        as never having asked at all — never raise, never silently pick a side."""

        assert _coerce_strategy_preference("something the model made up") == MigrationStrategyPreference.UNDECIDED


def test_strategy_preference_flows_into_every_downstream_prompt_automatically():
    """render_context_for_prompt is the single function PLAN_COMPONENT,
    TARGET_ARCHITECTURE, CUTOVER_STRATEGY, and ROLLBACK_STRATEGY all consume (via
    strategy_node's shared_context / render_component_planning_context) — this is
    the one place that has to carry it for every downstream call to see it for free."""

    rendered = render_context_for_prompt(_context(strategy_preference="re_architect"))
    assert "re_architect" in rendered
    assert "strategy_preference" in rendered


class TestPromptsReasonFromStrategyPreference:
    """Regression guard: these four prompts were verified (against a real
    lift-and-shift vs. re-architect comparison) to need explicit reasoning
    instructions tied to strategy_preference — not just access to the value,
    which is necessary but not sufficient. A prompt that receives the field but
    is never told to condition on it will silently ignore it."""

    def test_elicit_migration_context_asks_for_it_in_business_language(self):
        body = REGISTRY["elicit_migration_context"].system
        assert "strategy_preference" in body
        assert "lift_and_shift" in body and "re_architect" in body
        # Must be phrased as a business question, not a technical term dropped on the user.
        assert "never as a technical checklist item" in body

    def test_plan_component_conditions_disposition_choice_on_it(self):
        body = REGISTRY["plan_component"].system
        assert "lift_and_shift: the priority is speed" in body
        assert "re_architect: the user explicitly invited redesign" in body

    def test_target_architecture_does_not_force_modernization_under_lift_and_shift(self):
        """The bug this closes: TARGET_ARCHITECTURE unconditionally pushed toward
        consolidation/elimination/new-requirements framing, which actively
        contradicts a user who explicitly asked for minimal redesign."""

        body = REGISTRY["target_architecture"].system
        assert "GENUINE REASONING" in body and "MAXIMAL TRANSFORMATION" in body
        assert "explaining why MOST things stay" in body

    def test_cutover_strategy_weighs_risk_by_strategy_preference(self):
        body = REGISTRY["cutover_strategy"].system
        assert "strategy_preference" in body
        assert "dependency order IS the" in body


def test_no_prompt_references_a_classification_mechanism_that_no_longer_exists():
    """Regression guard for a real bug this session caught by inspection, not by a
    test: after the pre-computed request-classification hint was removed from the
    graph (classification moved into the same LLM call that extracts patches),
    two prompts still told the model to look for 'a DETERMINISTIC REQUEST
    CLASSIFICATION block' or 'the supplied deterministic request classification'
    that would never actually be injected again. A prompt instructing the model to
    consult something that doesn't exist is worse than no instruction at all."""

    stale_phrases = [
        "DETERMINISTIC REQUEST CLASSIFICATION",
        "CLASSIFICATION BLOCK",
        "supplied deterministic request classification",
        "deterministic classification",
    ]
    for prompt_id, prompt in REGISTRY.items():
        for phrase in stale_phrases:
            assert phrase not in prompt.system, (
                f"prompt '{prompt_id}' references '{phrase}', a classification mechanism that is "
                "no longer injected anywhere in the pipeline"
            )

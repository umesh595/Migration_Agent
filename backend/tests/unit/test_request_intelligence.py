"""Tests for request POLICY derivation.

request_intent is now judged by the LLM in the same call that extracts patches
(PatchSet.request_intent) — it generalizes to any technology, business domain, or
language, which a keyword classifier structurally cannot. That judgment isn't
something a cheap unit test can verify (it would need a real model call); see
tests/eval/test_golden_fixture.py and tests/integration/ for that, via a
MockProvider standing in for the LLM.

What IS deterministic, and what belongs here: given an intent the LLM already
decided, what is the app allowed to do as a result? That mapping must be
identical every time, regardless of model behavior — these tests pin it.
"""

from app.core.request_intelligence import derive_request_impact
from app.schemas.requests import RequestIntent


def test_current_fact_mutates_source_before_gate_1():
    impact = derive_request_impact(RequestIntent.CURRENT_FACT)

    assert impact.should_mutate_source is True
    assert impact.requires_confirmation is False


def test_current_fact_never_mutates_source_after_gate_1():
    """The one override that must never depend on the LLM's own judgment: once
    the source model is frozen, nothing may silently mutate it."""

    impact = derive_request_impact(RequestIntent.CURRENT_FACT, after_gate_1=True)

    assert impact.should_mutate_source is False


def test_sparse_intake_never_mutates_source():
    impact = derive_request_impact(RequestIntent.SPARSE_INTAKE)

    assert impact.should_mutate_source is False
    assert impact.requires_confirmation is False


def test_source_correction_requires_confirmation_and_does_not_mutate():
    impact = derive_request_impact(RequestIntent.SOURCE_CORRECTION, after_gate_1=True)

    assert impact.requires_confirmation is True
    assert impact.should_mutate_source is False
    assert {"effort", "cost", "sequencing", "validation", "rollback"} <= set(impact.impact_dimensions)


def test_target_planning_does_not_mutate_source_or_require_confirmation():
    impact = derive_request_impact(RequestIntent.TARGET_PLANNING, after_gate_1=True)

    assert impact.should_mutate_source is False
    assert impact.requires_confirmation is False


def test_high_impact_replatform_requires_confirmation():
    impact = derive_request_impact(RequestIntent.HIGH_IMPACT_REPLATFORM)

    assert impact.requires_confirmation is True
    assert "team_skills" in impact.impact_dimensions


def test_unscoped_capability_requires_confirmation():
    impact = derive_request_impact(RequestIntent.UNSCOPED_CAPABILITY)

    assert impact.requires_confirmation is True
    assert "scope" in impact.impact_dimensions


def test_review_explanation_never_mutates_and_needs_no_confirmation():
    impact = derive_request_impact(RequestIntent.REVIEW_EXPLANATION, after_gate_1=True)

    assert impact.requires_confirmation is False
    assert impact.should_mutate_source is False


def test_proceed_with_assumptions_mutates_source_pre_gate():
    impact = derive_request_impact(RequestIntent.PROCEED_WITH_ASSUMPTIONS)

    assert impact.should_mutate_source is True
    assert impact.requires_confirmation is False


def test_terse_confirmation_never_mutates_or_requires_confirmation():
    impact = derive_request_impact(RequestIntent.TERSE_CONFIRMATION)

    assert impact.should_mutate_source is False
    assert impact.requires_confirmation is False


def test_none_intent_falls_back_to_unknown_conservatively():
    impact = derive_request_impact(None)

    assert impact.intent == RequestIntent.UNKNOWN
    assert impact.should_mutate_source is False
    assert impact.requires_confirmation is False


def test_unrecognized_intent_string_falls_back_to_unknown_conservatively():
    """A model returning something outside the enum (a hallucinated intent name,
    or a future value from a newer prompt version this code hasn't been
    updated for) must degrade safely, never raise or silently mutate."""

    impact = derive_request_impact("some_intent_that_does_not_exist")

    assert impact.intent == RequestIntent.UNKNOWN
    assert impact.should_mutate_source is False


def test_every_intent_has_a_policy_defined():
    """Regression guard: a new RequestIntent value added without a matching
    policy entry would KeyError deep inside a live turn instead of failing here."""

    for intent in RequestIntent:
        impact = derive_request_impact(intent)
        assert impact.intent == intent
        assert isinstance(impact.should_mutate_source, bool)
        assert isinstance(impact.requires_confirmation, bool)
        assert impact.rationale

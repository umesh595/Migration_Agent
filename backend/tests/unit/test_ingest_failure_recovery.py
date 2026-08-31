"""Regression coverage for a live-reported bug: when the ingest LLM call fails
outright (e.g. a provider-side request-size/quota error — confirmed live
against Groq's free-tier TPM cap, which the ~6,700-token ingest_patches
prompt exceeds on every single call), two things went wrong together:

1. apply_patches_node's "nothing to apply" early-return unconditionally set
   error=None, silently erasing the real error ingest_node had just set —
   the failure was invisible, indistinguishable from an ordinary
   error-free turn.
2. The model stayed completely untouched, so gap analysis saw "still no
   detail" and re-asked the identical intake question, forever, with no
   sign anything was wrong.
"""

from __future__ import annotations

import pytest

from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.orchestration.nodes.discovery import (
    _capture_raw_message_after_total_ingest_failure,
    apply_patches_node,
    ingest_node,
)
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel


def test_apply_patches_preserves_a_real_error_when_theres_nothing_to_apply():
    result = apply_patches_node({"_patch_set": None, "error": "ingest failed for real"})

    assert result == {"last_patch_results": [], "error": "ingest failed for real"}


def test_apply_patches_leaves_error_none_on_an_ordinary_no_op_turn():
    result = apply_patches_node({"_patch_set": None, "error": None})

    assert result == {"last_patch_results": [], "error": None}


def test_raw_message_capture_records_a_clearly_labeled_low_confidence_assumption():
    model = ArchitectureModel()
    message = "just a simple web app, postgres db, email login, admin sees bookings, on-prem now, want gcp"

    new_model = _capture_raw_message_after_total_ingest_failure(model, message)

    assert len(new_model.assumptions) == 1
    assumption = new_model.assumptions[0]
    assert assumption.confidence == "unsure"
    assert "UNPARSED" in assumption.text
    assert message in assumption.text


def test_raw_message_capture_ignores_trivially_short_messages():
    model = ArchitectureModel()

    new_model = _capture_raw_message_after_total_ingest_failure(model, "yes")

    assert new_model is model
    assert new_model.assumptions == []


@pytest.mark.asyncio
async def test_ingest_node_total_failure_captures_raw_message_and_surfaces_a_real_error():
    """End-to-end through ingest_node itself: a total extraction failure must
    not look like an ordinary quiet turn — the model gains a raw fallback
    note and a genuine error is returned, not silently swallowed."""

    provider = MockProvider()  # no PatchSet registered -> every attempt raises StructuredOutputError
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)
    message = "just a simple web app, postgres db, email login, admin sees bookings, on-prem now, want gcp"

    result = await ingest_node(
        {
            "session_id": "s1",
            "stage": Stage.DISCOVERY,
            "model": ArchitectureModel(),
            "user_message": message,
            "request_impact": None,
        },
        gateway=gateway,
        meter=meter,
    )

    assert result["error"] is not None
    assert result["_patch_set"] is None
    assert len(result["model"].assumptions) == 1
    assert "UNPARSED" in result["model"].assumptions[0].text
    # narration (not just error) carries the degradation notice: generate_questions_node
    # legitimately clears `error` back to None once it recovers a usable follow-up
    # question, but only ever APPENDS to narration — never overwrites it — so
    # narration is what reliably survives to reach the user either way.
    assert result["narration"] == result["error"]


@pytest.mark.asyncio
async def test_full_turn_error_survives_through_apply_patches_after_total_ingest_failure():
    """The exact regression, end to end: ingest fails, apply_patches runs
    next with nothing to apply — the error from ingest must still be present
    afterward, not wiped back to None."""

    provider = MockProvider()
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)
    message = "just a simple web app, postgres db, email login, admin sees bookings, on-prem now, want gcp"

    state = {
        "session_id": "s1",
        "stage": Stage.DISCOVERY,
        "model": ArchitectureModel(),
        "user_message": message,
        "request_impact": None,
    }
    ingest_result = await ingest_node(state, gateway=gateway, meter=meter)
    state.update(ingest_result)

    apply_result = apply_patches_node(state)

    assert apply_result["error"] is not None
    assert apply_result["error"] == ingest_result["error"]

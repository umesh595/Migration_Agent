"""FR-E6: idempotent turn processing via client-supplied message ids. A
retried/double-submitted request with the same (session, message_id) must not
re-run the graph and double-apply patches. Requires Postgres + Redis; skips
cleanly without them.
"""

from __future__ import annotations

import pytest

from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.schemas.patches import AddComponentPatch, PatchSet
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_duplicate_message_id_is_not_reprocessed(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "idempotency test"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(
            patches=[AddComponentPatch(id="api", name="API", workload_type="api_service")],
            narration="Added the API.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )

    payload = {"message": "We have an API.", "message_id": "same-id-twice"}

    first = await client.post(f"/sessions/{session_id}/messages", headers=auth_headers, json=payload)
    assert first.status_code == 200, first.text

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert {c["id"] for c in state["model"]["components"]} == {"api"}
    version_after_first = state["model"]["version"]

    second = await client.post(f"/sessions/{session_id}/messages", headers=auth_headers, json=payload)
    assert second.status_code == 409

    state_after_retry = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert state_after_retry["model"]["version"] == version_after_first
    assert {c["id"] for c in state_after_retry["model"]["components"]} == {"api"}


@pytest.mark.asyncio
async def test_different_message_ids_both_process(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "idempotency test 2"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(
            patches=[AddComponentPatch(id="api", name="API", workload_type="api_service")],
            narration="Added the API.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )

    first = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "We have an API.", "message_id": "id-1"},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "We have an API.", "message_id": "id-2"},
    )
    assert second.status_code == 200, second.text

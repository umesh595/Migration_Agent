"""message_id idempotency (test_idempotent_messages.py) only rejects a REPLAY of
the same message. It does nothing for two genuinely DIFFERENT messages sent
concurrently for the same session — both would read the same latest model, both
advance the same LangGraph checkpoint thread, and race on the next ModelVersion
insert (uq_model_version_per_session), which without serialization surfaces as an
unhandled IntegrityError/500 for the loser. This file proves the Redis-backed
SessionTurnLock actually prevents that: one of two truly parallel requests must be
rejected with 409, and the survivor's turn must complete cleanly. Requires
Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import asyncio

import pytest

from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.schemas.patches import AddComponentPatch, PatchSet
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_concurrent_turns_on_same_session_one_is_rejected_not_racing(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "concurrency test"})).json()["id"]

    # Enough registered responses for both turns, whichever wins the race.
    for _ in range(2):
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

    async def send(message_id: str):
        return await client.post(
            f"/sessions/{session_id}/messages", headers=auth_headers,
            json={"message": "We have an API.", "message_id": message_id},
        )

    first, second = await asyncio.gather(send("concurrent-a"), send("concurrent-b"))
    statuses = sorted([first.status_code, second.status_code])

    # Exactly one succeeds; the other is rejected as busy (409) rather than both
    # racing to write the same next ModelVersion.
    assert statuses == [200, 409], (first.status_code, first.text, second.status_code, second.text)

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert state["model"]["version"] >= 1


@pytest.mark.asyncio
async def test_lock_is_released_after_a_turn_so_the_next_sequential_turn_succeeds(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "concurrency test 2"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(patches=[AddComponentPatch(id="api", name="API", workload_type="api_service")], narration="n1"),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )

    first = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "We have an API.", "message_id": "seq-1"},
    )
    assert first.status_code == 200, first.text

    provider.register(
        PatchSet,
        PatchSet(patches=[AddComponentPatch(id="db", name="DB", workload_type="database")], narration="n2"),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q2", related_gap_description="g2")], narration="n"),
    )

    # A second, sequential (non-overlapping) turn must succeed — the lock from
    # the first turn must have been released, not left held.
    second = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "We also have a database.", "message_id": "seq-2"},
    )
    assert second.status_code == 200, second.text

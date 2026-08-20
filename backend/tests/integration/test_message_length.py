"""The message length cap was raised from 10k to 50k characters to let a user
paste a config file (docker-compose.yml, a Terraform summary, a README section)
straight into discovery — still conversational ingestion, not an automated parser
(see DECISIONS.md). Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import pytest

from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.schemas.patches import AddComponentPatch, PatchSet
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_message_up_to_50k_characters_is_accepted(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "paste test"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(
            patches=[AddComponentPatch(id="pasted_service", name="Pasted Service", workload_type="api_service")],
            narration="Captured the pasted config.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )

    long_message = "# pasted docker-compose.yml\n" + ("service_line: value\n" * 2000)  # well under 50k, over 10k
    assert 10_000 < len(long_message) <= 50_000

    response = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": long_message, "message_id": "paste-1"}
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_message_over_50k_characters_is_rejected(app_client, auth_headers):
    client, _ = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "oversized paste"})).json()["id"]

    too_long = "x" * 50_001
    response = await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": too_long, "message_id": "paste-2"}
    )
    assert response.status_code == 422

"""GET /sessions/{id}/impact/{component_id} — reachability-based impact analysis
over the current model (GraphEngine.compute_impact), previously implemented and
unit-tested but never reachable through the API. Requires Postgres + Redis; skips
cleanly without them.
"""

from __future__ import annotations

import pytest

from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.schemas.patches import AddComponentPatch, AddDependencyPatch, PatchSet
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_impact_analysis_returns_upstream_and_downstream(app_client, auth_headers):
    client, provider = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "impact test"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(
            patches=[
                AddComponentPatch(id="web", name="Web", workload_type="web_service"),
                AddComponentPatch(id="api", name="API", workload_type="api_service"),
                AddComponentPatch(id="db", name="DB", workload_type="database"),
                AddDependencyPatch(source_id="web", target_id="api", kind="sync_call"),
                AddDependencyPatch(source_id="api", target_id="db", kind="data_read"),
            ],
            narration="Built the chain.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )

    async with client.stream(
        "POST", f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "web calls api calls db", "message_id": "impact-1"},
    ) as response:
        assert response.status_code == 200
        async for _ in response.aiter_lines():
            pass

    impact = (await client.get(f"/sessions/{session_id}/impact/api", headers=auth_headers)).json()
    assert impact["upstream"] == ["web"]
    assert impact["downstream"] == ["db"]


@pytest.mark.asyncio
async def test_impact_analysis_404s_for_unknown_component(app_client, auth_headers):
    client, _ = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "impact test 2"})).json()["id"]

    response = await client.get(f"/sessions/{session_id}/impact/ghost", headers=auth_headers)
    assert response.status_code == 404

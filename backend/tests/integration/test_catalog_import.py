"""Enterprise catalog import endpoint, exercised over the full HTTP path with a
MockCatalogProvider standing in for a real external system -- same "swap the
external boundary for a deterministic double after startup" pattern
conftest.py already uses for the LLM gateway. Requires Postgres + Redis;
skips cleanly without them (see conftest.requires_infra)."""

from __future__ import annotations

import pytest

from app.integrations.catalog_provider import CatalogComponentRecord, CatalogFetchResult, CatalogProviderError
from app.integrations.mock_catalog_provider import MockCatalogProvider
from app.llm.schemas import GeneratedQuestion, QuestionGenerationOutput
from app.main import app as fastapi_app
from app.schemas.patches import AddComponentPatch, PatchSet
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_catalog_import_returns_503_when_not_configured(app_client, auth_headers):
    client, _ = app_client
    fastapi_app.state.catalog_provider = None
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "catalog test"})).json()["id"]

    response = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "orders"}
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_catalog_import_applies_patches_through_the_real_validation_pipeline(app_client, auth_headers):
    client, _ = app_client
    fastapi_app.state.catalog_provider = MockCatalogProvider(
        result=CatalogFetchResult(
            components=[CatalogComponentRecord(external_id="crm-1", name="CRM Service", workload_type_hint="api_service")]
        )
    )
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "catalog test"})).json()["id"]

    response = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "crm"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == 1
    assert body["rejected"] == 0

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert any(c["id"] == "ext:crm-1" for c in state["model"]["components"])


@pytest.mark.asyncio
async def test_catalog_import_reimport_is_a_no_op_not_a_duplicate(app_client, auth_headers):
    client, _ = app_client
    fastapi_app.state.catalog_provider = MockCatalogProvider(
        result=CatalogFetchResult(
            components=[CatalogComponentRecord(external_id="crm-1", name="CRM Service", workload_type_hint="api_service")]
        )
    )
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "catalog test"})).json()["id"]

    first = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "crm"}
    )
    second = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "crm"}
    )
    assert first.json()["applied"] == 1
    assert second.json()["applied"] == 0
    assert second.json()["rejected"] == 1

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert len([c for c in state["model"]["components"] if c["id"] == "ext:crm-1"]) == 1


@pytest.mark.asyncio
async def test_catalog_import_returns_502_on_provider_error(app_client, auth_headers):
    client, _ = app_client
    fastapi_app.state.catalog_provider = MockCatalogProvider(error=CatalogProviderError("catalog system down"))
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "catalog test"})).json()["id"]

    response = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "orders"}
    )
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_catalog_import_rejected_once_session_has_left_discovery(app_client, auth_headers):
    client, provider = app_client
    fastapi_app.state.catalog_provider = MockCatalogProvider()
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "catalog test"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(patches=[AddComponentPatch(id="api", name="API", workload_type="api_service")], narration="n"),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(questions=[GeneratedQuestion(text="q", related_gap_description="g")], narration="n"),
    )
    await client.post(
        f"/sessions/{session_id}/messages", headers=auth_headers,
        json={"message": "We have an API.", "message_id": "m1"},
    )
    accept = await client.post(f"/sessions/{session_id}/model/accept", headers=auth_headers)
    assert accept.status_code == 200, accept.text

    response = await client.post(
        f"/sessions/{session_id}/integrations/catalog/import", headers=auth_headers, json={"query": "orders"}
    )
    assert response.status_code == 409

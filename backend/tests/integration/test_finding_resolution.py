"""Findings can be marked resolved or accepted-as-risk instead of staying open
forever — ResolutionStatus.RESOLVED/.ACCEPTED_AS_RISK previously had no code path
that ever assigned them. Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_finding_can_be_marked_resolved_and_reopened(app_client, auth_headers):
    client, _ = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "finding test"})).json()["id"]

    from app.db.session import AsyncSessionLocal
    from app.schemas.findings import Finding, FindingSeverity, FindingSource
    from app.services import session_service

    finding = Finding(
        id="RULE-004-test",
        source=FindingSource.RULE,
        rule_id="RULE-004",
        severity=FindingSeverity.ERROR,
        message="plan has no plan-level rollback strategy with concrete steps",
    )
    async with AsyncSessionLocal() as db:
        await session_service.save_findings(db, uuid.UUID(session_id), None, [finding])
        await db.commit()

    findings = (await client.get(f"/sessions/{session_id}/findings", headers=auth_headers)).json()["findings"]
    assert len(findings) == 1
    assert findings[0]["resolution_status"] == "open"
    finding_id = findings[0]["id"]

    resolve = await client.patch(
        f"/sessions/{session_id}/findings/{finding_id}", headers=auth_headers,
        json={"resolution_status": "resolved"},
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["resolution_status"] == "resolved"

    findings_after = (await client.get(f"/sessions/{session_id}/findings", headers=auth_headers)).json()["findings"]
    assert findings_after[0]["resolution_status"] == "resolved"

    reopen = await client.patch(
        f"/sessions/{session_id}/findings/{finding_id}", headers=auth_headers,
        json={"resolution_status": "open"},
    )
    assert reopen.status_code == 200
    assert reopen.json()["resolution_status"] == "open"


@pytest.mark.asyncio
async def test_resolve_finding_rejects_invalid_status(app_client, auth_headers):
    client, _ = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "finding test 2"})).json()["id"]

    from app.db.session import AsyncSessionLocal
    from app.schemas.findings import Finding, FindingSeverity, FindingSource
    from app.services import session_service

    finding = Finding(
        id="RULE-005-test", source=FindingSource.RULE, rule_id="RULE-005",
        severity=FindingSeverity.ERROR, message="no validation checks",
    )
    async with AsyncSessionLocal() as db:
        await session_service.save_findings(db, uuid.UUID(session_id), None, [finding])
        await db.commit()

    findings = (await client.get(f"/sessions/{session_id}/findings", headers=auth_headers)).json()["findings"]
    finding_id = findings[0]["id"]

    response = await client.patch(
        f"/sessions/{session_id}/findings/{finding_id}", headers=auth_headers,
        json={"resolution_status": "not_a_real_status"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_resolve_finding_404s_for_nonexistent_finding(app_client, auth_headers):
    client, _ = app_client
    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "finding test 3"})).json()["id"]

    response = await client.patch(
        f"/sessions/{session_id}/findings/{uuid.uuid4()}", headers=auth_headers,
        json={"resolution_status": "resolved"},
    )
    assert response.status_code == 404

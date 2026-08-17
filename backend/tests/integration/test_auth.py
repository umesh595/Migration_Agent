"""Self-service password change — distinct from admin provisioning/reset (FR-A5).
Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_user_can_change_their_own_password(app_client):
    client, _ = app_client

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    email = f"selfservice-{uuid.uuid4().hex[:12]}@example.com"
    old_password = "a-sufficiently-long-old-password"
    new_password = "a-sufficiently-long-new-password"
    async with AsyncSessionLocal() as db:
        await user_service.create_user(db, email=email, password=old_password)

    login = await client.post("/auth/login", json={"email": email, "password": old_password})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        "/auth/change-password",
        headers=headers,
        json={"current_password": old_password, "new_password": new_password},
    )
    assert response.status_code == 204

    assert (await client.post("/auth/login", json={"email": email, "password": old_password})).status_code == 401
    assert (await client.post("/auth/login", json={"email": email, "password": new_password})).status_code == 200


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(app_client, auth_headers):
    client, _ = app_client
    response = await client.post(
        "/auth/change-password",
        headers=auth_headers,
        json={"current_password": "definitely-not-it", "new_password": "a-new-long-enough-password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_change_password_requires_authentication(app_client):
    client, _ = app_client
    response = await client.post(
        "/auth/change-password",
        json={"current_password": "whatever", "new_password": "a-new-long-enough-password"},
    )
    assert response.status_code in (401, 403)

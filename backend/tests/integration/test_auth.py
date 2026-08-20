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


@pytest.mark.asyncio
async def test_change_password_revokes_existing_refresh_token(app_client):
    """Password change bumps token_version — a refresh token issued before the
    change must be rejected afterward, closing the window a leaked refresh token
    would otherwise have for its full TTL."""

    client, _ = app_client

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    email = f"revoke-{uuid.uuid4().hex[:12]}@example.com"
    old_password = "a-sufficiently-long-old-password"
    new_password = "a-sufficiently-long-new-password"
    async with AsyncSessionLocal() as db:
        await user_service.create_user(db, email=email, password=old_password)

    login = await client.post("/auth/login", json={"email": email, "password": old_password})
    assert login.status_code == 200
    tokens = login.json()
    access_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    change = await client.post(
        "/auth/change-password",
        headers=access_headers,
        json={"current_password": old_password, "new_password": new_password},
    )
    assert change.status_code == 204

    # The old refresh token was issued before the version bump — must be rejected.
    stale_refresh = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert stale_refresh.status_code == 401

    # The old access token is likewise no longer honored.
    stale_me = await client.get("/auth/me", headers=access_headers)
    assert stale_me.status_code == 401

    # A fresh login with the new password gets a working token pair.
    fresh_login = await client.post("/auth/login", json={"email": email, "password": new_password})
    assert fresh_login.status_code == 200
    fresh_headers = {"Authorization": f"Bearer {fresh_login.json()['access_token']}"}
    assert (await client.get("/auth/me", headers=fresh_headers)).status_code == 200


@pytest.mark.asyncio
async def test_logout_everywhere_revokes_all_outstanding_tokens(app_client, auth_headers):
    client, _ = app_client

    me_before = await client.get("/auth/me", headers=auth_headers)
    assert me_before.status_code == 200

    logout = await client.post("/auth/logout-everywhere", headers=auth_headers)
    assert logout.status_code == 204

    me_after = await client.get("/auth/me", headers=auth_headers)
    assert me_after.status_code == 401

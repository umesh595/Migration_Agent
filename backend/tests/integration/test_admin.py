"""Admin user provisioning (FR-A5): create/disable/reset, and the absence of any
self-service registration path. Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_me_reports_admin_status_without_embedding_it_in_the_token(app_client, auth_headers, admin_auth_headers):
    """The web app needs to know is_admin to decide whether to show admin UI, but it
    must come from a live lookup, not a JWT claim — a role change shouldn't wait for
    the old token to expire."""

    client, _ = app_client

    regular = await client.get("/auth/me", headers=auth_headers)
    assert regular.status_code == 200
    assert regular.json()["is_admin"] is False

    admin = await client.get("/auth/me", headers=admin_auth_headers)
    assert admin.status_code == 200
    assert admin.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_signup_endpoint_does_not_exist(app_client):
    """FR-A5: no self-service registration in v1."""

    client, _ = app_client
    response = await client.post(
        "/auth/signup", json={"email": "nope@example.com", "password": "irrelevant-password"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_reach_admin_endpoints(app_client, auth_headers):
    client, _ = app_client
    response = await client.get("/admin/users", headers=auth_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(app_client):
    client, _ = app_client
    response = await client.get("/admin/users")
    assert response.status_code == 403 or response.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_create_list_disable_and_reset_a_user(app_client, admin_auth_headers):
    client, _ = app_client
    email = f"provisioned-{uuid.uuid4().hex[:12]}@example.com"
    password = "a-sufficiently-long-password"

    # --- create ---
    create_resp = await client.post(
        "/admin/users", headers=admin_auth_headers, json={"email": email, "password": password}
    )
    assert create_resp.status_code == 201, create_resp.text
    user_id = create_resp.json()["id"]
    assert create_resp.json()["is_active"] is True
    assert create_resp.json()["is_admin"] is False

    # duplicate email is rejected, not silently accepted
    dup_resp = await client.post(
        "/admin/users", headers=admin_auth_headers, json={"email": email, "password": password}
    )
    assert dup_resp.status_code == 409

    # --- the new user can log in ---
    login_resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    user_token = login_resp.json()["access_token"]

    # --- list includes it ---
    list_resp = await client.get("/admin/users", headers=admin_auth_headers)
    assert list_resp.status_code == 200
    assert any(u["id"] == user_id for u in list_resp.json())

    # --- disable takes effect immediately, not just at next login ---
    disable_resp = await client.patch(
        f"/admin/users/{user_id}/active", headers=admin_auth_headers, json={"active": False}
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["is_active"] is False

    # the already-issued access token must be rejected now, before it expires.
    # current_user is resolved as a dependency before the route body runs, so this
    # 401s regardless of whether the session id in the path is real.
    blocked_resp = await client.get(
        f"/sessions/{uuid.uuid4()}/state", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert blocked_resp.status_code == 401

    # disabled account cannot obtain a new token either
    relogin_resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert relogin_resp.status_code == 401

    # --- re-enable ---
    enable_resp = await client.patch(
        f"/admin/users/{user_id}/active", headers=admin_auth_headers, json={"active": True}
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["is_active"] is True

    # --- reset password: old password stops working, new one returned once works ---
    reset_resp = await client.post(f"/admin/users/{user_id}/reset-password", headers=admin_auth_headers)
    assert reset_resp.status_code == 200
    new_password = reset_resp.json()["temporary_password"]
    assert new_password != password

    old_login = await client.post("/auth/login", json={"email": email, "password": password})
    assert old_login.status_code == 401

    new_login = await client.post("/auth/login", json={"email": email, "password": new_password})
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_once_an_admin_exists(app_client):
    """The startup bootstrap (app/services/user_service.py) must not re-provision or
    duplicate once any admin already exists — otherwise leaving BOOTSTRAP_ADMIN_* set
    in a long-running deployment's env would be a standing risk, not a one-time seed."""

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    async with AsyncSessionLocal() as db:
        # An admin already exists from other tests/fixtures in this run, or we make
        # one directly — either way, a second bootstrap call with different
        # credentials must be a no-op.
        existing = await user_service.create_user(
            db, email=f"first-admin-{uuid.uuid4().hex[:8]}@example.com", password="whatever-long-enough", is_admin=True
        )

        other_email = f"second-admin-{uuid.uuid4().hex[:8]}@example.com"
        await user_service.bootstrap_admin_if_configured(db, email=other_email, password="also-long-enough")

        from sqlalchemy import select

        from app.db.models import User

        result = await db.execute(select(User).where(User.email == other_email))
        assert result.scalar_one_or_none() is None, "bootstrap must not create a second admin"

        # the original admin is untouched
        result = await db.execute(select(User).where(User.id == existing.id))
        assert result.scalar_one().is_admin is True


@pytest.mark.asyncio
async def test_admin_actions_on_unknown_user_return_404(app_client, admin_auth_headers):
    client, _ = app_client
    ghost_id = uuid.uuid4()

    response = await client.patch(
        f"/admin/users/{ghost_id}/active", headers=admin_auth_headers, json={"active": False}
    )
    assert response.status_code == 404

    response = await client.post(f"/admin/users/{ghost_id}/reset-password", headers=admin_auth_headers)
    assert response.status_code == 404

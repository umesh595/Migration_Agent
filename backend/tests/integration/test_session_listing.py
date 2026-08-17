"""GET /sessions — the dashboard a 'resume days later' story needs (Migration —
Story 9); without it a user can only resume a session whose URL they bookmarked.
Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


@pytest.mark.asyncio
async def test_list_sessions_returns_only_the_caller_own_sessions_most_recent_first(app_client, auth_headers):
    client, _ = app_client

    first = await client.post("/sessions", headers=auth_headers, json={"name": "older"})
    second = await client.post("/sessions", headers=auth_headers, json={"name": "newer"})
    assert first.status_code == 201 and second.status_code == 201

    listing = await client.get("/sessions", headers=auth_headers)
    assert listing.status_code == 200
    ids = [s["id"] for s in listing.json()]
    assert first.json()["id"] in ids
    assert second.json()["id"] in ids
    # most recently created/updated first
    assert ids.index(second.json()["id"]) < ids.index(first.json()["id"])


@pytest.mark.asyncio
async def test_list_sessions_does_not_leak_another_user_sessions(app_client, auth_headers):
    client, _ = app_client

    import uuid

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    other_email = f"other-{uuid.uuid4().hex[:12]}@example.com"
    other_password = "a-sufficiently-long-password"
    async with AsyncSessionLocal() as db:
        await user_service.create_user(db, email=other_email, password=other_password)
    other_login = await client.post("/auth/login", json={"email": other_email, "password": other_password})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    mine = await client.post("/sessions", headers=auth_headers, json={"name": "mine"})
    theirs = await client.post("/sessions", headers=other_headers, json={"name": "theirs"})

    my_listing = (await client.get("/sessions", headers=auth_headers)).json()
    my_ids = {s["id"] for s in my_listing}
    assert mine.json()["id"] in my_ids
    assert theirs.json()["id"] not in my_ids

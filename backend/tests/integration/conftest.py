"""Integration-test harness: real FastAPI app, real Postgres, real Redis, mocked LLM.

These tests skip (rather than fail) when Postgres/Redis aren't reachable, so the
unit and eval suites still run standalone on a bare checkout.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest


def _postgres_available() -> bool:
    """Opens a real connection rather than probing the port. A TCP check is not
    enough: an unrelated Postgres on the same port looks 'reachable' but has
    different credentials and no such database, which would surface as a confusing
    test failure instead of a clean skip."""

    try:
        import psycopg

        dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3)
        client.ping()
        client.close()
        return True
    except Exception:
        return False


requires_infra = pytest.mark.skipif(
    not (_postgres_available() and _redis_available()),
    reason="Postgres/Redis not available — see README to start them and run integration tests",
)


# psycopg's async driver cannot run on Windows' default ProactorEventLoop, so
# integration tests (the only ones that touch Postgres) run on a SelectorEventLoop.
# Declared via pytest-asyncio's loop-factory hook rather than the deprecated
# `event_loop_policy` fixture — asyncio policies are slated for removal in 3.16.
# The hook must return a non-empty mapping, so it is only defined on Windows;
# Linux/macOS defaults already work with psycopg.
if sys.platform == "win32":

    def pytest_asyncio_loop_factories():
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture
async def app_client():
    """Yields (httpx client, app) with the LLM gateway swapped for a MockProvider
    so the whole HTTP path runs deterministically."""

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from app.llm.gateway import LLMGateway
    from app.llm.providers.openai_provider import MockProvider
    from app.main import app

    provider = MockProvider()

    async with LifespanManager(app, startup_timeout=60, shutdown_timeout=30):
        # Replace the real OpenAI-backed gateway after startup built it.
        app.state.gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, provider


@pytest.fixture
async def auth_headers(app_client):
    """Provisions a user the way production does — there is no self-service signup
    (FR-A5) — then logs in over the real HTTP path to get tokens."""

    client, _ = app_client

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    password = "a-sufficiently-long-password"
    async with AsyncSessionLocal() as db:
        await user_service.create_user(db, email=email, password=password)

    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def admin_auth_headers(app_client):
    """A provisioned admin account, for exercising /admin endpoints directly."""

    client, _ = app_client

    from app.db.session import AsyncSessionLocal
    from app.services import user_service

    email = f"admin-{uuid.uuid4().hex[:12]}@example.com"
    password = "a-sufficiently-long-admin-password"
    async with AsyncSessionLocal() as db:
        await user_service.create_user(db, email=email, password=password, is_admin=True)

    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.routers import admin, auth, sessions
from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.llm.gateway import LLMGateway
from app.llm.providers.openai_provider import OpenAIProvider
from app.observability.tracing import flush as tracing_flush
from app.observability.tracing import tracing_status
from app.orchestration.checkpointer import close_checkpointer, init_checkpointer
from app.security.rate_limit import RateLimiter
from app.services.user_service import bootstrap_admin_if_configured

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.rate_limiter = RateLimiter(app.state.redis)

    provider = OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        cheap_model=settings.llm_cheap_model,
        strong_model=settings.llm_strong_model,
        timeout_s=settings.llm_request_timeout_s,
    )
    app.state.gateway = LLMGateway(
        provider,
        cheap_tier_max_retries=settings.llm_cheap_tier_max_retries,
        strong_tier_max_retries=settings.llm_strong_tier_max_retries,
    )

    await init_checkpointer()

    # FR-A5: no self-service registration. Without this, a fresh deployment has no
    # way to create the first admin account at all — this is the only account this
    # process ever creates outside of an authenticated /admin call.
    async with AsyncSessionLocal() as db:
        await bootstrap_admin_if_configured(
            db,
            email=settings.bootstrap_admin_email,
            password=settings.bootstrap_admin_password.get_secret_value()
            if settings.bootstrap_admin_password
            else None,
        )

    logger.info("migration agent API ready (env=%s)", settings.env)

    status = tracing_status()
    if status["configured"] and not status["active"]:
        logger.error("Langfuse is configured but inactive: %s", status["error"])
    elif status["active"]:
        logger.info("Langfuse tracing active")

    yield

    tracing_flush()
    await close_checkpointer()
    await app.state.redis.aclose()


app = FastAPI(
    title="Enterprise Architecture Migration Agent",
    version="1.0.0",
    description="Conversational migration planning with a deterministic decision core.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(admin.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
async def readiness() -> dict:
    """Checks the dependencies a request actually needs, so an unready replica is
    pulled from the load balancer instead of failing user turns."""

    checks: dict[str, str] = {}
    try:
        await app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    from sqlalchemy import text

    from app.db.session import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return {
        "status": "ready" if healthy else "degraded",
        "checks": checks,
        # Not part of readiness — tracing being down shouldn't pull a replica from
        # the load balancer — but surfaced so it can't fail silently.
        "tracing": tracing_status(),
    }

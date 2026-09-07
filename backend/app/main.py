from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.routers import admin, ag_ui, auth, integrations, sessions
from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.integrations.rest_catalog_provider import RestCatalogProvider
from app.llm.gateway import LLMGateway
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.fallback_provider import FallbackLLMProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.providers.groq_provider import GroqProvider
from app.observability.tracing import flush as tracing_flush
from app.observability.tracing import tracing_status
from app.orchestration.checkpointer import close_checkpointer, init_checkpointer
from app.security.in_memory_redis import InMemoryRedis
from app.security.rate_limit import RateLimiter
from app.security.session_lock import SessionTurnLock
from app.services.user_service import bootstrap_admin_if_configured

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if settings.redis_url == "memory://":
        logger.warning("using in-memory Redis substitute; this is for local single-process development only")
        app.state.redis = InMemoryRedis()
    else:
        app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.rate_limiter = RateLimiter(app.state.redis, fail_open=settings.rate_limit_fail_open)
    app.state.session_lock = SessionTurnLock(app.state.redis)

    # Optional, no-op if unset — mirrors the Langfuse pattern below.
    if settings.catalog_base_url and settings.catalog_token_url and settings.catalog_client_id and settings.catalog_client_secret:
        app.state.catalog_provider = RestCatalogProvider(
            base_url=settings.catalog_base_url,
            token_url=settings.catalog_token_url,
            client_id=settings.catalog_client_id,
            client_secret=settings.catalog_client_secret.get_secret_value(),
            timeout_s=settings.catalog_request_timeout_s,
        )
    else:
        app.state.catalog_provider = None

    anthropic_provider = AnthropicProvider(
        api_key=settings.anthropic_api_key.get_secret_value(),
        cheap_model=settings.anthropic_cheap_model,
        strong_model=settings.anthropic_strong_model,
        timeout_s=settings.llm_request_timeout_s,
        workspace_id=settings.anthropic_workspace_id,
    )
    # Gemini and Groq are an optional, ordered fallback chain behind Anthropic,
    # unwound in that order only once the tier ahead of it has no quota/credits
    # left (see FallbackLLMProvider). Unset either key to skip that stage.
    gemini_provider = (
        GeminiProvider(
            api_keys=[key.get_secret_value() for key in settings.gemini_api_keys],
            cheap_model=settings.gemini_cheap_model,
            strong_model=settings.gemini_strong_model,
            timeout_s=settings.llm_request_timeout_s,
        )
        if settings.gemini_api_keys
        else None
    )
    groq_provider = (
        GroqProvider(
            api_key=settings.groq_api_key.get_secret_value(),
            cheap_model=settings.groq_cheap_model,
            strong_model=settings.groq_strong_model,
            timeout_s=settings.llm_request_timeout_s,
        )
        if settings.groq_api_key
        else None
    )

    # Fold the configured optional tiers right-to-left behind Anthropic —
    # FallbackLLMProvider(primary=anthropic, fallback=FallbackLLMProvider(
    # primary=gemini, fallback=groq)) when both are configured — without
    # hand-nesting a conditional per tier, so a future fourth provider is one
    # more list entry rather than another manually-nested ternary.
    optional_fallback_tiers = [tier for tier in (gemini_provider, groq_provider) if tier is not None]
    accumulated_fallback = None
    for tier in reversed(optional_fallback_tiers):
        accumulated_fallback = tier if accumulated_fallback is None else FallbackLLMProvider(primary=tier, fallback=accumulated_fallback)
    provider = (
        anthropic_provider
        if accumulated_fallback is None
        else FallbackLLMProvider(primary=anthropic_provider, fallback=accumulated_fallback)
    )

    # Each optional tier past the first costs one gateway attempt to switch
    # into (FallbackLLMProvider forces a retry to move the active provider —
    # see its own docstring), so the cheap tier's retry budget — tuned for a
    # single provider with no fallback chain — needs one extra attempt per
    # configured fallback tier or it can exhaust itself switching providers
    # before ever reaching the last one, silently escalating to the strong
    # tier instead of actually trying it.
    effective_cheap_tier_max_retries = settings.llm_cheap_tier_max_retries + len(optional_fallback_tiers)
    app.state.gateway = LLMGateway(
        provider,
        cheap_tier_max_retries=effective_cheap_tier_max_retries,
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
    # PATCH is required for /admin/users/{id}/active and /sessions/{id}/findings/{id} —
    # a browser preflight (OPTIONS with Access-Control-Request-Method: PATCH) was
    # rejected with this list missing PATCH, silently breaking both endpoints
    # cross-origin (the default dev topology: frontend :3000, API :8000).
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(admin.router)
app.include_router(integrations.router)
app.include_router(ag_ui.router)


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
        # Neither is part of readiness — an unconfigured optional integration
        # shouldn't pull a healthy replica from the load balancer — but both
        # are surfaced so "configured but silently no-op" can't hide.
        "tracing": tracing_status(),
        "catalog_integration": {"configured": app.state.catalog_provider is not None},
    }

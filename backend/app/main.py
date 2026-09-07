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
from app.llm.providers.codevector_provider import CodeVectorProvider
from app.llm.providers.fallback_provider import FallbackLLMProvider
from app.llm.providers.gemini_provider import GeminiProvider
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

    codevector_api_key = settings.active_codevector_api_key
    codevector_base_url = settings.active_codevector_base_url
    if not codevector_api_key or not codevector_base_url:
        raise RuntimeError(
            "CodeVector/Fision Labs Kimi is the configured primary LLM provider. "
            "Set CODEVECTOR_API_KEY and CODEVECTOR_BASE_URL, or the FISION_LABS_* / KIMI_* aliases."
        )

    codevector_provider = CodeVectorProvider(
        api_key=codevector_api_key.get_secret_value(),
        base_url=codevector_base_url,
        cheap_model=settings.active_codevector_cheap_model,
        strong_model=settings.active_codevector_strong_model,
        timeout_s=settings.llm_request_timeout_s,
    )
    # Gemini is a fallback only (see FallbackLLMProvider) - activated automatically
    # if the primary gateway is unavailable/quota-limited. Unset GOOGLE_AI_STUDIO_API_KEY to
    # run CodeVector-only.
    gemini_provider = (
        GeminiProvider(
            api_key=settings.google_ai_studio_api_key.get_secret_value(),
            cheap_model=settings.google_ai_studio_cheap_model,
            strong_model=settings.google_ai_studio_strong_model,
            timeout_s=settings.llm_request_timeout_s,
        )
        if settings.google_ai_studio_api_key
        else None
    )
    provider = FallbackLLMProvider(primary=codevector_provider, fallback=gemini_provider)
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

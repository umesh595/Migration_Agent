"""Langfuse tracing (SDK v4). Silent no-op when keys are unset (DECISIONS.md) — the
app must behave identically with or without observability configured.

Written against the v4 `start_observation` API. The older v2 surface
(`client.generation(...)`, `client.span(...)`) does not exist in v4; calling it would
raise inside the guard below and silently emit nothing, which is worse than no
tracing at all — hence `tracing_status()`, so a misconfiguration is visible rather
than quietly swallowed.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_INIT_ERROR: str | None = None


@lru_cache
def _client() -> Any | None:
    global _INIT_ERROR

    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        if not hasattr(client, "start_observation"):
            _INIT_ERROR = (
                "installed langfuse SDK lacks start_observation() — expected v4.x; "
                "tracing disabled to avoid silently dropping spans"
            )
            logger.error(_INIT_ERROR)
            return None
        return client
    except Exception as exc:  # pragma: no cover - depends on external service
        _INIT_ERROR = f"Langfuse init failed: {exc}"
        logger.warning("%s — tracing disabled", _INIT_ERROR)
        return None


def tracing_status() -> dict[str, Any]:
    """Exposed on /health/ready so 'observability configured but broken' is a
    visible state rather than a silent one."""

    settings = get_settings()
    configured = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
    return {
        "configured": configured,
        "active": _client() is not None,
        "error": _INIT_ERROR,
    }


def trace_llm_call(
    *,
    node_name: str,
    model: str,
    tier: str,
    prompt_tokens: int,
    completion_tokens: int,
    attempts: int,
    session_id: str | None = None,
) -> None:
    """Records one LLM generation with token usage, for per-node cost/latency
    attribution (technique #15)."""

    client = _client()
    if client is None:
        return

    try:  # pragma: no cover - external service
        observation = client.start_observation(
            name=node_name,
            as_type="generation",
            model=model,
            usage_details={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            metadata={"tier": tier, "attempts": attempts, "session_id": session_id},
        )
        observation.end()
    except Exception as exc:
        logger.warning("Langfuse generation trace failed (non-fatal): %s", exc)


def trace_node(*, node_name: str, session_id: str, metadata: dict[str, Any] | None = None) -> None:
    """Records a deterministic (zero-token) node execution."""

    client = _client()
    if client is None:
        return

    try:  # pragma: no cover - external service
        observation = client.start_observation(
            name=node_name,
            as_type="span",
            metadata={**(metadata or {}), "session_id": session_id},
        )
        observation.end()
    except Exception as exc:
        logger.warning("Langfuse span trace failed (non-fatal): %s", exc)


def flush() -> None:
    """Flush buffered events on shutdown so short-lived processes don't drop traces."""

    client = _client()
    if client is None:
        return
    try:  # pragma: no cover - external service
        client.flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed (non-fatal): %s", exc)

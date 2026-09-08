"""Gemini adapter with client-side round-robin across multiple API keys. Each
GEMINI_API_KEYS entry gets its own google-genai Client; complete_structured()
advances a shared counter on *every* call, not just on failure, so load
spreads evenly across all configured keys instead of hammering one until it
errors.

Uses the google-genai SDK's native structured-output support
(GenerateContentConfig.response_schema + response.parsed) — the same
"provider enforces the schema server-side, code re-validates as a safety net"
boundary AnthropicProvider and OpenAIProvider already use.
"""

from __future__ import annotations

import json
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

from app.llm.base import (
    LLMProvider,
    LLMUsage,
    ModelTier,
    ProviderQuotaExceededError,
    ProviderRequestError,
    StructuredOutputError,
    StructuredResponse,
    normalize_llm_text,
)

# Gemini reports both a per-minute rate limit and a hard quota/billing cutoff as
# HTTP 429 RESOURCE_EXHAUSTED — there's no separate code for "this key is truly
# out of quota" the way Anthropic/Groq/OpenAI expose via error body hints. Every
# sibling provider only ever treats a 429 as ProviderQuotaExceededError after
# narrowing to a billing-specific signal (Anthropic greps the body for
# credit/quota/billing keywords; Groq/OpenAI match specific error codes) —
# precisely because that signal is meant to trigger FallbackLLMProvider's
# *permanent*, sticky-for-the-process switch away from this provider, and a
# transient rate limit must never cause that.
#
# Since Gemini can't be narrowed the same way, a key is only pulled from
# rotation for this cooldown window rather than forever (see _exhausted_until
# below) — a real per-minute rate limit clears on its own, and the key
# rejoins rotation automatically once it does. ProviderQuotaExceededError is
# still raised, but only for calls made *while every key is currently within
# its cooldown* — not as a permanent, cumulative "this key is dead" record.
_QUOTA_STATUS_CODE = 429
_QUOTA_STATUS_NAME = "RESOURCE_EXHAUSTED"
_RATE_LIMIT_COOLDOWN_S = 60.0


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        api_keys: list[str],
        cheap_model: str,
        strong_model: str,
        timeout_s: float = 60.0,
    ) -> None:
        if not api_keys:
            raise ValueError("GeminiProvider requires at least one API key")
        http_options = types.HttpOptions(timeout=int(timeout_s * 1000))
        self._clients = [genai.Client(api_key=key, http_options=http_options) for key in api_keys]
        self._cheap_model = cheap_model
        self._strong_model = strong_model
        self._next_index = 0
        # Index into self._clients -> the time.monotonic() timestamp its key
        # becomes usable again. A key past a 429 is skipped only until then,
        # never permanently — see the module docstring above for why this is
        # time-bounded rather than a sticky "dead forever" set.
        self._exhausted_until: dict[int, float] = {}

    def model_for_tier(self, tier: ModelTier) -> str:
        return self._cheap_model if tier == ModelTier.CHEAP else self._strong_model

    def _is_in_cooldown(self, idx: int, *, now: float) -> bool:
        return now < self._exhausted_until.get(idx, 0.0)

    def _any_key_available(self) -> bool:
        """Read-only check — unlike _next_client_index, never advances the
        round-robin pointer, so it's safe to call just to test availability."""

        now = time.monotonic()
        return any(not self._is_in_cooldown(idx, now=now) for idx in range(len(self._clients)))

    def _next_client_index(self) -> int | None:
        """Advances the round-robin pointer by exactly one usable key, skipping
        any currently in their post-429 cooldown. Returns None only when every
        key is in cooldown right now."""

        n = len(self._clients)
        now = time.monotonic()
        for _ in range(n):
            idx = self._next_index % n
            self._next_index = (self._next_index + 1) % n
            if not self._is_in_cooldown(idx, now=now):
                return idx
        return None

    async def complete_structured[T: BaseModel](
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> StructuredResponse[T]:
        idx = self._next_client_index()
        if idx is None:
            raise ProviderQuotaExceededError(
                f"all {len(self._clients)} configured Gemini API keys are currently rate-limited/quota-exhausted "
                f"(cooldown: {_RATE_LIMIT_COOLDOWN_S}s)"
            )
        client = self._clients[idx]

        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=normalize_llm_text(user_prompt),
                config=types.GenerateContentConfig(
                    system_instruction=normalize_llm_text(system_prompt),
                    response_mime_type="application/json",
                    response_schema=response_model,
                    temperature=temperature,
                ),
            )
        except APIError as exc:
            if exc.code == _QUOTA_STATUS_CODE or (exc.status or "").upper() == _QUOTA_STATUS_NAME:
                self._exhausted_until[idx] = time.monotonic() + _RATE_LIMIT_COOLDOWN_S
                if not self._any_key_available():
                    # Every key is in cooldown right now — could still be a
                    # simultaneous rate-limit blip rather than genuine
                    # exhaustion, but there's nothing usable this instant.
                    raise ProviderQuotaExceededError(
                        f"Gemini key #{idx} rate-limited/quota-exhausted, and every other configured key is "
                        f"currently in cooldown too: {exc}"
                    ) from exc
                # Not a permanent signal: LLMGateway's retry loop will call
                # model_for_tier()/complete_structured() again, which lands on
                # _next_client_index() and rotates onto a still-usable key.
                # This key rejoins rotation on its own once its cooldown ends.
                raise StructuredOutputError(f"Gemini key #{idx} rate-limited/quota-exhausted: {exc}") from exc
            # A non-quota API error status (auth failure, server error, bad
            # request, ...) - the call reached Gemini and got a real error
            # response back, as opposed to the broad except below, which
            # covers the call never reaching Gemini at all.
            raise ProviderRequestError(f"Gemini API error: {exc}") from exc
        except Exception as exc:
            # Anything else - connection refused, DNS failure, a timeout -
            # happens before any APIError could even be constructed, so it's
            # never one. Without this, such a failure would propagate as a
            # raw, unrecognized exception instead of the StructuredOutputError
            # subclass every caller of complete_structured is entitled to
            # expect (see LLMProvider's own docstring).
            raise ProviderRequestError(f"Gemini request failed: {exc}") from exc

        parsed = response.parsed
        if parsed is None:
            raise StructuredOutputError("Gemini returned no structured output")

        # Mirrors AnthropicProvider/OpenAIProvider's identical pass over their own
        # parsed response: re-validate after normalize_llm_text() so output text
        # can't reintroduce Unicode punctuation the WIN1252-encoded local dev
        # Postgres instance can't store (see DECISIONS.md).
        try:
            parsed = response_model.model_validate(json.loads(normalize_llm_text(parsed.model_dump_json())))
        except ValidationError as exc:
            raise StructuredOutputError(f"normalized Gemini output failed validation: {exc}") from exc

        usage = response.usage_metadata
        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(
                prompt_tokens=(usage.prompt_token_count or 0) if usage else 0,
                completion_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
            model=model,
            attempts=1,
        )

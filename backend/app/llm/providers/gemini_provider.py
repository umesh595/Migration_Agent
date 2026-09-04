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

from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, ValidationError

from app.llm.base import (
    LLMProvider,
    LLMUsage,
    ModelTier,
    ProviderQuotaExceededError,
    StructuredOutputError,
    StructuredResponse,
    normalize_llm_text,
)

# Gemini reports both a per-minute rate limit and a hard quota/billing cutoff as
# HTTP 429 RESOURCE_EXHAUSTED — there's no separate code for "this key is truly
# out of quota" the way Anthropic/Groq/OpenAI expose via error body hints. So a
# single 429 here only knocks its own key out of rotation (see _exhausted
# below); ProviderQuotaExceededError — the signal FallbackLLMProvider acts on —
# is only raised once every configured key has hit this.
_QUOTA_STATUS_CODE = 429
_QUOTA_STATUS_NAME = "RESOURCE_EXHAUSTED"


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
        # Indices into self._clients whose key has come back quota-exhausted.
        # Sticky for this process's lifetime, same as FallbackLLMProvider's own
        # switch — no value in re-trying a key that already proved it can't serve.
        self._exhausted: set[int] = set()

    def model_for_tier(self, tier: ModelTier) -> str:
        return self._cheap_model if tier == ModelTier.CHEAP else self._strong_model

    def _next_client_index(self) -> int | None:
        """Advances the round-robin pointer by exactly one usable key, skipping
        any already-exhausted ones. Returns None only once every key has been
        exhausted."""

        n = len(self._clients)
        for _ in range(n):
            idx = self._next_index % n
            self._next_index += 1
            if idx not in self._exhausted:
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
            raise ProviderQuotaExceededError(f"all {len(self._clients)} configured Gemini API keys are quota-exhausted")
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
                self._exhausted.add(idx)
                remaining = len(self._clients) - len(self._exhausted)
                if remaining <= 0:
                    raise ProviderQuotaExceededError(
                        f"Gemini key #{idx} exhausted and it was the last usable key: {exc}"
                    ) from exc
                # Not a plain retry-forever signal: LLMGateway's retry loop will
                # call model_for_tier()/complete_structured() again, which lands
                # on _next_client_index() and rotates onto a still-good key.
                raise StructuredOutputError(
                    f"Gemini key #{idx} quota/rate-limit exhausted, {remaining} key(s) left in rotation: {exc}"
                ) from exc
            raise StructuredOutputError(f"Gemini API error: {exc}") from exc

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

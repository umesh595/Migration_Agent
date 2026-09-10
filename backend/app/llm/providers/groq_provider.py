"""Groq adapter — used only as a fallback when Anthropic (the primary)
has no quota/credits left, via FallbackLLMProvider. Groq's
chat completions endpoint is OpenAI-compatible, so this reuses the `openai`
SDK client pointed at Groq's base URL rather than adding a new dependency.

Unlike OpenAIProvider, this does NOT use `chat.completions.parse()` — that
convenience method relies on OpenAI's own strict json_schema mode, which
Groq's API does not advertise the same guarantees for. Instead this asks for
plain JSON mode and validates the response against the Pydantic schema
itself, the same "provider returns text, code enforces the schema" boundary
technique #10 already uses everywhere else in this codebase.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMProvider, LLMUsage, ModelTier, ProviderQuotaExceededError, StructuredOutputError, StructuredResponse

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_QUOTA_EXHAUSTED_CODES = frozenset({"insufficient_quota", "credit_balance_exhausted"})


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, cheap_model: str, strong_model: str, timeout_s: float = 60.0) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=_GROQ_BASE_URL, timeout=timeout_s)
        self._cheap_model = cheap_model
        self._strong_model = strong_model

    def model_for_tier(self, tier: ModelTier) -> str:
        return self._cheap_model if tier == ModelTier.CHEAP else self._strong_model

    async def complete_structured[T: BaseModel](
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> StructuredResponse[T]:
        schema_instruction = (
            "\n\nRespond with a single JSON object only — no prose, no markdown fences — matching exactly "
            f"this JSON schema:\n{json.dumps(response_model.model_json_schema())}"
        )

        try:
            completion = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt + schema_instruction},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
        except APIError as exc:
            if getattr(exc, "code", None) in _QUOTA_EXHAUSTED_CODES:
                raise ProviderQuotaExceededError(f"Groq quota/credits exhausted: {exc}") from exc
            raise StructuredOutputError(f"Groq API error: {exc}") from exc

        message = completion.choices[0].message
        content = message.content
        if not content:
            raise StructuredOutputError("Groq returned no content")

        try:
            parsed = response_model.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise StructuredOutputError(f"Groq response did not match the required schema: {exc}") from exc

        usage = completion.usage
        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            ),
            model=model,
            attempts=1,
        )

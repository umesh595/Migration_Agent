"""Google AI Studio Gemini adapter for fallback structured-output calls."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import (
    LLMProvider,
    LLMUsage,
    ModelTier,
    ProviderRequestError,
    StructuredOutputError,
    StructuredResponse,
    normalize_llm_text,
)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, cheap_model: str, strong_model: str, timeout_s: float = 60.0) -> None:
        self._api_key = api_key
        self._cheap_model = cheap_model
        self._strong_model = strong_model
        self._timeout_s = timeout_s

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
            "\n\nRespond with a single JSON object only - no prose, no markdown fences - matching exactly "
            f"this JSON schema:\n{json.dumps(response_model.model_json_schema())}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": normalize_llm_text(system_prompt) + schema_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": normalize_llm_text(user_prompt)}]}],
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    f"{_GEMINI_BASE_URL}/models/{model}:generateContent",
                    params={"key": self._api_key},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderRequestError(f"Gemini API error: {exc.response.status_code} {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Gemini request failed: {exc}") from exc

        data = response.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = response_model.model_validate(json.loads(normalize_llm_text(text)))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise StructuredOutputError(f"Gemini response did not match the required schema: {exc}") from exc

        usage = data.get("usageMetadata", {})
        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(
                prompt_tokens=int(usage.get("promptTokenCount", 0) or 0),
                completion_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
            ),
            model=model,
            attempts=1,
        )

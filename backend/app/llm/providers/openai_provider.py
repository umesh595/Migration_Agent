"""OpenAI adapter. Uses the Responses API's native structured-output parsing so
schema conformance is enforced by the provider, not by post-hoc regex on prose."""

from __future__ import annotations

import json

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.llm.base import (
    LLMProvider,
    LLMUsage,
    ModelTier,
    StructuredOutputError,
    StructuredResponse,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, cheap_model: str, strong_model: str, timeout_s: float = 60.0) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_s)
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
    ) -> StructuredResponse[T]:
        try:
            # Stable path in openai>=1.92 (`beta.chat.completions.parse` is the
            # older, deprecated location for the same feature).
            completion = await self._client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_model,
                temperature=temperature,
            )
        except APIError as exc:
            raise StructuredOutputError(f"OpenAI API error: {exc}") from exc

        message = completion.choices[0].message
        if message.refusal:
            raise StructuredOutputError(f"model refused to answer: {message.refusal}")
        if message.parsed is None:
            raise StructuredOutputError("model returned no parseable structured output")

        usage = completion.usage
        return StructuredResponse(
            parsed=message.parsed,
            usage=LLMUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            ),
            model=model,
            attempts=1,
        )


class MockProvider(LLMProvider):
    """Deterministic provider for the CI eval suite (DECISIONS.md: the LLM boundary
    is mocked so golden-fixture tests can't flake on model non-determinism).
    Responses are keyed by response_model type name."""

    def __init__(self, responses: dict[str, list[BaseModel]] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[dict[str, str]] = []

    def register(self, response_model: type[BaseModel], *responses: BaseModel) -> None:
        self._responses.setdefault(response_model.__name__, []).extend(responses)

    def model_for_tier(self, tier: ModelTier) -> str:
        return f"mock-{tier}"

    async def complete_structured[T: BaseModel](
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> StructuredResponse[T]:
        self.calls.append({"model": model, "system": system_prompt, "user": user_prompt})

        queue = self._responses.get(response_model.__name__)
        if not queue:
            raise StructuredOutputError(f"MockProvider has no registered response for {response_model.__name__}")

        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response

        try:
            parsed = response_model.model_validate(json.loads(response.model_dump_json()))
        except ValidationError as exc:
            raise StructuredOutputError(f"registered mock response failed validation: {exc}") from exc

        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(prompt_tokens=100, completion_tokens=50),
            model=model,
            attempts=1,
        )

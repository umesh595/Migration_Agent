"""CodeVector/Fision Labs Kimi adapter.

The office gateway is treated as OpenAI-compatible: configurable base URL,
configurable Kimi model names, and local Pydantic validation of the JSON result.
"""

from __future__ import annotations

import json

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, BadRequestError
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

_QUOTA_HINTS = ("credit", "quota", "billing", "balance", "insufficient", "rate_limit")


class CodeVectorProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, cheap_model: str, strong_model: str, timeout_s: float = 60.0) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url.rstrip("/"), timeout=timeout_s)
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
        schema_instruction = (
            "\n\nRespond with a single JSON object only - no prose, no markdown fences - matching exactly "
            f"this JSON schema:\n{json.dumps(response_model.model_json_schema())}"
        )
        schema_name = response_model.__name__

        # Three-tier degradation, most-constrained first. `json_schema` (OpenAI's
        # "Structured Outputs") asks the gateway to constrain token generation itself
        # against the schema server-side — the same reliability class as Anthropic's
        # beta.messages.parse(output_format=...), and a real step up from `json_object`
        # mode, which only guarantees syntactically-valid JSON with no guarantee it
        # matches OUR schema at all. A weaker/cheaper model benefits from this far more
        # than a stronger one does, since it's no longer relying purely on prompt-
        # following to hit the shape — go straight to the ceiling and fall back only
        # if the gateway actually rejects it, never assume it's unsupported up front.
        attempts: list[dict] = [
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": response_model.model_json_schema(), "strict": True},
                }
            },
            {"response_format": {"type": "json_object"}},
            {},
        ]

        completion = None
        last_bad_request: BadRequestError | None = None
        for i, response_format_kwargs in enumerate(attempts):
            try:
                completion = await self._create_completion(
                    model=model,
                    system_prompt=normalize_llm_text(system_prompt) + schema_instruction,
                    user_prompt=normalize_llm_text(user_prompt),
                    temperature=temperature,
                    response_format_kwargs=response_format_kwargs,
                )
                break
            except BadRequestError as exc:
                last_bad_request = exc
                body = str(getattr(exc, "body", "") or exc)
                # A rejection unrelated to response_format/schema support (bad api key,
                # content policy, etc.) won't be fixed by degrading the format — surface
                # it immediately instead of burning the remaining fallback tiers on it.
                if "response_format" not in body.lower() and "json" not in body.lower() and "schema" not in body.lower():
                    raise self._classify_api_error(exc) from exc
                if i == len(attempts) - 1:
                    raise self._classify_api_error(exc) from exc
                continue
            except (APITimeoutError, APIConnectionError) as exc:
                raise ProviderRequestError(f"CodeVector request failed: {exc}") from exc
            except APIError as exc:
                raise self._classify_api_error(exc) from exc

        if completion is None:
            # Unreachable in practice (the loop always either returns or raises), but
            # keeps the type checker honest and fails loudly instead of silently.
            raise self._classify_api_error(last_bad_request) if last_bad_request else StructuredOutputError(
                "CodeVector produced no completion"
            )

        message = completion.choices[0].message
        content = message.content
        if not content:
            raise StructuredOutputError("CodeVector returned no content")

        try:
            parsed_data = json.loads(normalize_llm_text(content))
            parsed = response_model.model_validate(parsed_data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise StructuredOutputError(f"CodeVector response did not match the required schema: {exc}") from exc

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

    async def _create_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        response_format_kwargs: dict,
    ):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            **response_format_kwargs,
        }
        return await self._client.chat.completions.create(**kwargs)

    def _classify_api_error(self, exc: APIError) -> StructuredOutputError:
        body = str(getattr(exc, "body", "") or exc)
        code = str(getattr(exc, "code", "") or "")
        haystack = f"{code} {body}".lower()
        if any(hint in haystack for hint in _QUOTA_HINTS):
            return ProviderQuotaExceededError(f"CodeVector quota/rate limit hit: {exc}")
        return ProviderRequestError(f"CodeVector API error: {exc}")

"""CodeVector/Fision Labs Kimi adapter.

The office gateway is treated as OpenAI-compatible: configurable base URL,
configurable Kimi model names, and local Pydantic validation of the JSON result.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

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
logger = logging.getLogger(__name__)


class _AccumulatedMessage:
    """Mimics the shape of an OpenAI ChatCompletionMessage closely enough that
    downstream parsing (JSON parse, fence-stripping, reasoning capture) never
    needs to know whether this call streamed or not."""

    __slots__ = ("content", "reasoning_content")

    def __init__(self, content: str, reasoning_content: str | None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content


class _AccumulatedChoice:
    __slots__ = ("message",)

    def __init__(self, message: _AccumulatedMessage) -> None:
        self.message = message


class _AccumulatedCompletion:
    __slots__ = ("choices", "usage")

    def __init__(self, choices: list[_AccumulatedChoice], usage) -> None:
        self.choices = choices
        self.usage = usage


class _EstimatedUsage:
    """Streaming here deliberately never sends `stream_options.include_usage`
    (unverified whether this gateway supports it — a rejected unknown param
    would hard-fail every streaming call, not just degrade it) so a real
    completion never arrives with token counts. A rough chars/4 estimate
    keeps SessionTokenMeter's budget enforcement conservative-by-overcount
    rather than silently under-billing a streamed call as free."""

    __slots__ = ("prompt_tokens", "completion_tokens")

    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class CodeVectorProvider(LLMProvider):
    def __init__(
        self, api_key: str, base_url: str, cheap_model: str, strong_model: str, timeout_s: float = 60.0,
        response_format: Literal["json_schema", "json_object"] = "json_schema",
        fallback_model: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url.rstrip("/"), timeout=timeout_s, max_retries=0
        )
        self._cheap_model = cheap_model
        self._strong_model = strong_model
        self._format_start: dict[str, int] = {}
        self._default_format_start = 0 if response_format == "json_schema" else 1
        self._fallback_model = fallback_model

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
        try:
            return await self._complete_structured_for_model(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                on_delta=on_delta,
            )
        except (ProviderQuotaExceededError, ProviderRequestError) as exc:
            if not self._fallback_model or model == self._fallback_model:
                raise
            logger.warning(
                "CodeVector model %s unavailable (%s); retrying this request with CodeVector fallback model %s",
                model,
                exc,
                self._fallback_model,
            )
            return await self._complete_structured_for_model(
                model=self._fallback_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                on_delta=on_delta,
            )

    async def _complete_structured_for_model[T: BaseModel](
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
            f"this JSON schema:\n{json.dumps(response_model.model_json_schema(), separators=(',', ':'))}"
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
            if i < self._format_start.get(model, self._default_format_start):
                continue
            try:
                completion = await self._create_completion(
                    model=model,
                    system_prompt=normalize_llm_text(system_prompt) + (
                        "\n\nReturn only JSON matching the supplied response schema." if i == 0 else schema_instruction
                    ),
                    user_prompt=normalize_llm_text(user_prompt),
                    temperature=temperature,
                    response_format_kwargs=response_format_kwargs,
                    on_delta=on_delta,
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
                # An invalid individual schema must not disable schema mode for all calls.
                if any(word in body.lower() for word in ("unsupported", "not supported", "not support")):
                    self._format_start[model] = i + 1
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
            json_text = content.strip()
            lines = json_text.splitlines()
            if len(lines) >= 3 and lines[0].lower() in {"```json", "```"} and lines[-1] == "```":
                # Some compatible gateways wrap valid JSON despite response_format.
                # Only remove a complete outer fence; never repair or truncate the data.
                json_text = "\n".join(lines[1:-1])
            parsed_data = json.loads(normalize_llm_text(json_text))
            parsed = response_model.model_validate(parsed_data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise StructuredOutputError(f"CodeVector response did not match the required schema: {exc}") from exc

        usage = completion.usage
        # A reasoning-tier model (verified live against this gateway's deepseekv4p:
        # a hidden chain-of-thought comes back as `reasoning_content`, separate from
        # the schema-constrained `content` it's scored against) — capture it when
        # present rather than silently discarding real, already-paid-for reasoning.
        reasoning_content = getattr(message, "reasoning_content", None) or None
        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            ),
            model=model,
            attempts=1,
            reasoning=reasoning_content,
        )

    async def _create_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        response_format_kwargs: dict,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 1.0 if "kimi" in model.lower() else temperature,
            **response_format_kwargs,
        }
        if on_delta is None:
            return await self._client.chat.completions.create(**kwargs)

        # Same request, consumed incrementally: a reasoning delta reaches the
        # caller as it's generated instead of only once the whole response is
        # done — same total call, no extra latency, strictly better PERCEIVED
        # latency (verified live: first delta arrived well before the full
        # response). Never request stream_options.include_usage here — this
        # gateway's support for it is unverified, and a rejected unknown
        # param would hard-fail every streaming call rather than degrade.
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        stream = await self._client.chat.completions.create(**kwargs, stream=True)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning_delta = getattr(delta, "reasoning_content", None)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)
                await on_delta(reasoning_delta)
            if delta.content:
                content_parts.append(delta.content)

        full_content = "".join(content_parts)
        full_reasoning = "".join(reasoning_parts) or None
        estimated_usage = _EstimatedUsage(
            prompt_tokens=(len(system_prompt) + len(user_prompt)) // 4,
            completion_tokens=(len(full_content) + len(full_reasoning or "")) // 4,
        )
        return _AccumulatedCompletion(
            choices=[_AccumulatedChoice(message=_AccumulatedMessage(content=full_content, reasoning_content=full_reasoning))],
            usage=estimated_usage,
        )

    def _classify_api_error(self, exc: APIError) -> StructuredOutputError:
        body = str(getattr(exc, "body", "") or exc)
        code = str(getattr(exc, "code", "") or "")
        haystack = f"{code} {body}".lower()
        if any(hint in haystack for hint in _QUOTA_HINTS):
            return ProviderQuotaExceededError(f"CodeVector quota/rate limit hit: {exc}")
        return ProviderRequestError(f"CodeVector API error: {exc}")

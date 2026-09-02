"""Anthropic adapter. Uses the Messages API's structured-outputs beta
(`beta.messages.parse` with `output_format`) as the structured-output boundary:
the Pydantic response model is sent as a real JSON-schema output format and
Anthropic's backend does constrained decoding against it server-side — the
same reliability bar as OpenAI's `.parse()`, and a level up from best-effort
forced tool-use (which this provider used before switching: tool-use asks the
model to *produce* schema-shaped JSON but doesn't constrain generation against
it, so malformed/mis-nested tool input was an observed live failure mode)."""

from __future__ import annotations

import json

from anthropic import APIConnectionError, APIStatusError, APITimeoutError, AsyncAnthropic
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

_QUOTA_HINTS = ("credit", "quota", "billing", "balance", "insufficient")


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        cheap_model: str,
        strong_model: str,
        timeout_s: float = 60.0,
        workspace_id: str | None = None,
    ) -> None:
        # Only identity-linked keys (minted from a personal Console profile) need
        # this — they can't infer a workspace to bill against, so every request
        # must declare one explicitly via this header. A key generated from
        # inside a workspace's own API Keys tab is already bound to it and never
        # needs this header, so `workspace_id` stays unset for that case.
        default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_s, default_headers=default_headers)
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
        temperature: float = 0.0,  # noqa: ARG002 — see comment below on why this isn't forwarded
    ) -> StructuredResponse[T]:
        try:
            # `temperature` is deliberately not forwarded: newer models (e.g.
            # claude-opus-5) reject it outright with a 400 ("temperature is
            # deprecated for this model") no matter the value, while older ones
            # (e.g. claude-sonnet-5) accept it fine — since every call site in
            # this codebase always requests 0.0 anyway (deterministic structured
            # output), omitting it is strictly safe and keeps this provider
            # working across both model generations without per-model branching.
            response = await self._client.beta.messages.parse(
                model=model,
                # A live PatchSet response (several patches + narration) was
                # observed truncating mid-JSON at 4096 — OpenAI's provider sets
                # no cap at all (relies on the model's own stop token), so this
                # is sized generously rather than tightly to avoid repeating
                # that failure on any other schema in this codebase.
                max_tokens=16384,
                system=normalize_llm_text(system_prompt),
                messages=[{"role": "user", "content": normalize_llm_text(user_prompt)}],
                output_format=response_model,
            )
        except APIStatusError as exc:
            body = str(getattr(exc, "body", "") or exc)
            if exc.status_code == 429 and any(hint in body.lower() for hint in _QUOTA_HINTS):
                raise ProviderQuotaExceededError(f"Anthropic quota/credits exhausted: {exc}") from exc
            raise StructuredOutputError(f"Anthropic API error: {exc}") from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise StructuredOutputError(f"Anthropic request failed: {exc}") from exc
        except ValidationError as exc:
            # The API's own constrained decoding should already guarantee schema
            # conformance, but the SDK still locally re-validates the returned
            # JSON text against response_model — this is that safety net firing.
            raise StructuredOutputError(f"Anthropic response did not match the required schema: {exc}") from exc

        parsed = next(
            (block.parsed_output for block in response.content if getattr(block, "type", None) == "text"),
            None,
        )
        if parsed is None:
            raise StructuredOutputError("Anthropic returned no structured output")

        # Mirrors OpenAIProvider's identical pass over its own parsed response:
        # Claude's OUTPUT text is not sanitized by the normalize_llm_text() calls
        # above (those only cover the outgoing prompts) — narration/questions
        # text containing curly quotes, em-dashes, etc. was flowing straight into
        # LangGraph's checkpoint and session_service's persisted rows unsanitized
        # ever since Anthropic became primary, which is exactly the class of
        # Unicode punctuation the WIN1252-encoded local dev Postgres instance
        # cannot store (see DECISIONS.md).
        try:
            parsed = response_model.model_validate(json.loads(normalize_llm_text(parsed.model_dump_json())))
        except ValidationError as exc:
            raise StructuredOutputError(f"normalized model output failed validation: {exc}") from exc

        return StructuredResponse(
            parsed=parsed,
            usage=LLMUsage(
                prompt_tokens=response.usage.input_tokens if response.usage else 0,
                completion_tokens=response.usage.output_tokens if response.usage else 0,
            ),
            model=model,
            attempts=1,
        )

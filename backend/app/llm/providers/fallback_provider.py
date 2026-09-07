"""Wraps a primary and an optional secondary LLMProvider so a quota/credits
exhaustion on the primary switches to the secondary — without the gateway,
graph nodes, or any prompt ever knowing a fallback exists (matches the
extensibility boundary LLMProvider's own docstring already commits to).

The configured provider stays primary; this exists only to keep the app usable
when that provider is unavailable or out of credits. The switch
is sticky and permanent for this process's lifetime —
once the primary has proven it can't serve a request, there's no value in
re-trying it on the next call only to pay the same failure again.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from app.llm.base import (
    LLMProvider,
    ModelTier,
    ProviderQuotaExceededError,
    ProviderRequestError,
    StructuredOutputError,
    StructuredResponse,
)

logger = logging.getLogger(__name__)


class FallbackLLMProvider(LLMProvider):
    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._using_fallback = False

    def _active(self) -> LLMProvider:
        return self._fallback if (self._using_fallback and self._fallback is not None) else self._primary

    def model_for_tier(self, tier: ModelTier) -> str:
        return self._active().model_for_tier(tier)

    async def complete_structured[T: BaseModel](
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
    ) -> StructuredResponse[T]:
        if self._using_fallback and self._fallback is not None:
            return await self._fallback.complete_structured(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
            )

        try:
            return await self._primary.complete_structured(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
            )
        except (ProviderQuotaExceededError, ProviderRequestError) as exc:
            if self._fallback is None:
                raise
            logger.error(
                "primary LLM provider quota/credits exhausted (%s) — switching to the fallback provider "
                "for the rest of this process",
                exc,
            )
            self._using_fallback = True
            # Raise a plain StructuredOutputError (not the quota subclass) so
            # LLMGateway's own retry loop treats this exactly like any other
            # failed attempt and retries — on that retry it re-reads
            # model_for_tier(), which now returns the fallback provider's
            # model name, so the next complete_structured() call above
            # routes to `self._fallback` correctly.
            raise StructuredOutputError(f"switched to fallback provider after: {exc}") from exc

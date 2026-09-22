"""Wraps a primary and an optional secondary LLMProvider so a quota/credits
exhaustion, or the primary being genuinely unreachable, switches to the
secondary — without the gateway, graph nodes, or any prompt ever knowing a
fallback exists (matches the extensibility boundary LLMProvider's own
docstring already commits to).

Anthropic stays the primary; this exists only to keep the app usable when its
account runs out of credits or it's down, not to run a multi-provider
strategy. The switch is sticky for quota exhaustion — once the primary has
proven it's out of quota/credits, there's no value retrying it on the next
call only to pay the same failure again. A transient transport failure (a
timeout, a connection drop, a non-quota API error) tries the fallback for
just this call instead, without permanently abandoning a primary that may
well be healthy again by the next request.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from app.llm.base import (
    LLMProvider,
    ModelTier,
    ProviderQuotaExceededError,
    ProviderRequestError,
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
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> StructuredResponse[T]:
        if self._using_fallback and self._fallback is not None:
            return await self._fallback.complete_structured(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                on_delta=on_delta,
            )

        try:
            return await self._primary.complete_structured(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                on_delta=on_delta,
            )
        except (ProviderQuotaExceededError, ProviderRequestError) as exc:
            if self._fallback is None:
                raise
            logger.error(
                "primary LLM provider unavailable (%s) - trying the fallback provider",
                exc,
            )
            self._using_fallback = isinstance(exc, ProviderQuotaExceededError)
            # Fallback is transport recovery, independent of JSON repair attempts.
            tier = ModelTier.STRONG if model == self._primary.model_for_tier(ModelTier.STRONG) else ModelTier.CHEAP
            return await self._fallback.complete_structured(
                model=self._fallback.model_for_tier(tier),
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                on_delta=on_delta,
            )

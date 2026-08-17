"""L1 gateway: retry policy, tier escalation, token budget, tracing. Graph nodes
call this — never a provider directly.

Escalation policy (DECISIONS.md): a CHEAP-tier structured-output failure escalates to
the STRONG tier after `cheap_tier_max_retries` attempts (default 1), rather than
burning the uniform 3 retries the original PRD specified. The cheap tier runs on
every discovery turn and is the node where a bad patch is what PatchValidator has to
catch — spending a strong-model call there is cheaper than three failed cheap ones.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from app.llm.base import (
    LLMProvider,
    ModelTier,
    StructuredOutputError,
    StructuredResponse,
    TokenBudgetExceededError,
)
from app.observability.tracing import trace_llm_call

logger = logging.getLogger(__name__)


class SessionTokenMeter:
    """Per-session token accounting. Enforced before each call so a runaway loop
    can't silently spend past the budget."""

    def __init__(self, budget: int, already_spent: int = 0) -> None:
        self._budget = budget
        self._spent = already_spent

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def remaining(self) -> int:
        return max(0, self._budget - self._spent)

    def check_before_call(self) -> None:
        if self.remaining <= 0:
            raise TokenBudgetExceededError(
                f"session token budget of {self._budget} exhausted ({self._spent} spent)"
            )

    def record(self, tokens: int) -> None:
        self._spent += tokens


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        cheap_tier_max_retries: int = 1,
        strong_tier_max_retries: int = 3,
    ) -> None:
        self._provider = provider
        self._cheap_retries = cheap_tier_max_retries
        self._strong_retries = strong_tier_max_retries

    async def complete[T: BaseModel](
        self,
        *,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        meter: SessionTokenMeter | None = None,
        node_name: str = "unknown",
        temperature: float = 0.0,
    ) -> StructuredResponse[T]:
        """Runs the call with tier-appropriate retries, escalating CHEAP→STRONG on
        exhaustion. Raises StructuredOutputError only if the strong tier also fails —
        callers treat that as 'state untouched'."""

        attempts_used = 0
        max_attempts = self._cheap_retries + 1 if tier == ModelTier.CHEAP else self._strong_retries + 1
        last_error: Exception | None = None
        error_feedback = ""

        for attempt in range(max_attempts):
            if meter:
                meter.check_before_call()
            attempts_used += 1
            model = self._provider.model_for_tier(tier)

            try:
                response = await self._provider.complete_structured(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt + error_feedback,
                    response_model=response_model,
                    temperature=temperature,
                )
            except StructuredOutputError as exc:
                last_error = exc
                error_feedback = (
                    f"\n\nYour previous response could not be parsed against the required schema. "
                    f"Error: {exc}. Return only valid output matching the schema."
                )
                logger.warning("structured output failed (node=%s tier=%s attempt=%d): %s", node_name, tier, attempt + 1, exc)
                continue

            if meter:
                meter.record(response.usage.total_tokens)
            trace_llm_call(
                node_name=node_name,
                model=response.model,
                tier=str(tier),
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                attempts=attempts_used,
            )
            response.attempts = attempts_used
            return response

        if tier == ModelTier.CHEAP:
            logger.warning("cheap tier exhausted for node=%s, escalating to strong tier", node_name)
            escalated = await self.complete(
                tier=ModelTier.STRONG,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                meter=meter,
                node_name=f"{node_name}:escalated",
                temperature=temperature,
            )
            escalated.attempts += attempts_used
            return escalated

        raise StructuredOutputError(
            f"node '{node_name}' failed to produce schema-valid output after {attempts_used} attempts: {last_error}"
        ) from last_error

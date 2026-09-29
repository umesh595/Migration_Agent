"""L1 gateway: retry policy, tier escalation, token budget, tracing. Graph nodes
call this — never a provider directly.

Escalation policy (DECISIONS.md): a CHEAP-tier structured-output failure escalates to
the STRONG tier after `cheap_tier_max_retries` attempts (default 1), rather than
burning the uniform 3 retries the original PRD specified. The cheap tier runs on
every discovery turn and is the node where a bad patch is what PatchValidator has to
catch — spending a strong-model call there is cheaper than three failed cheap ones.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel

from app.llm.base import (
    LLMCallOptions,
    LLMProvider,
    ModelTier,
    ProviderQuotaExceededError,
    ProviderRequestError,
    StructuredOutputError,
    StructuredResponse,
    TokenBudgetExceededError,
    normalize_llm_text,
)
from app.llm.streaming import get_reasoning_sink
from app.observability.tracing import trace_llm_call

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeRoute:
    model: str | None
    thinking: str


def _valid_thinking(value: str | None) -> str:
    lowered = (value or "off").lower()
    return lowered if lowered in {"off", "low", "high", "max"} else "off"


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
        call_timeout_s: float | None = None,
        critic_timeout_s: float | None = None,
        plan_timeout_s: float | None = None,
        node_routes_json: str | None = None,
        plan_thinking_effort: str = "low",
        question_generation_model: str = "flash",
    ) -> None:
        self._provider = provider
        self._cheap_retries = cheap_tier_max_retries
        self._strong_retries = strong_tier_max_retries
        self._call_timeout_s = call_timeout_s
        self._critic_timeout_s = critic_timeout_s
        self._plan_timeout_s = plan_timeout_s or call_timeout_s
        self._plan_thinking_effort = _valid_thinking(plan_thinking_effort)
        self._question_generation_model = question_generation_model
        self._node_route_overrides = self._parse_node_routes(node_routes_json)
        self._provider_accepts_options = "options" in inspect.signature(provider.complete_structured).parameters

    def _parse_node_routes(self, value: str | None) -> dict[str, NodeRoute]:
        if not value:
            return {}
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("invalid LLM_NODE_ROUTES JSON; ignoring")
            return {}
        routes: dict[str, NodeRoute] = {}
        for pattern, config in raw.items():
            if not isinstance(config, dict):
                continue
            routes[str(pattern)] = NodeRoute(
                model=str(config["model"]) if config.get("model") else None,
                thinking=_valid_thinking(config.get("thinking")),
            )
        return routes

    def _model_alias(self, alias: str | None) -> str | None:
        if alias == "flash":
            return self._provider.model_for_tier(ModelTier.CHEAP)
        if alias == "pro":
            return self._provider.model_for_tier(ModelTier.STRONG)
        return alias

    def _default_route(self, node_name: str, tier: ModelTier) -> NodeRoute:
        if node_name in {"discovery.ingest", "discovery.ingest_completeness_critic", "discovery.requirement_coverage_critic"}:
            return NodeRoute(self._provider.model_for_tier(ModelTier.CHEAP), "off")
        if node_name == "discovery.requirement_coverage":
            return NodeRoute(self._provider.model_for_tier(ModelTier.CHEAP), "off")
        if node_name == "discovery.generate_questions":
            model = self._provider.model_for_tier(
                ModelTier.STRONG if self._question_generation_model == "pro" else ModelTier.CHEAP
            )
            return NodeRoute(model, "off")
        if node_name in {"planning.after_gate_intake", "planning.elicit_context"}:
            return NodeRoute(self._provider.model_for_tier(ModelTier.CHEAP), "off")
        if (
            fnmatch.fnmatch(node_name, "planning.component.*")
            or node_name in {"planning.target_architecture", "planning.cutover", "planning.rollback"}
        ):
            return NodeRoute(self._provider.model_for_tier(ModelTier.STRONG), self._plan_thinking_effort)
        if node_name in {"review.discuss_ingest", "review.discuss_answer"}:
            return NodeRoute(self._provider.model_for_tier(ModelTier.CHEAP), "off")
        if node_name == "review.semantic" or node_name == "review.judge" or fnmatch.fnmatch(node_name, "review.refine.*"):
            return NodeRoute(self._provider.model_for_tier(ModelTier.STRONG), "high")
        return NodeRoute(self._provider.model_for_tier(tier), "off")

    def _route_for(self, node_name: str, tier: ModelTier) -> NodeRoute:
        route = self._default_route(node_name, tier)
        for pattern, override in self._node_route_overrides.items():
            if fnmatch.fnmatch(node_name, pattern):
                return NodeRoute(self._model_alias(override.model) or route.model, override.thinking)
        return route

    def _timeout_for(self, node_name: str) -> float | None:
        if fnmatch.fnmatch(node_name, "planning.component.*") or node_name in {
            "planning.target_architecture", "planning.cutover", "planning.rollback"
        }:
            return self._plan_timeout_s
        if node_name in {"discovery.ingest_completeness_critic", "discovery.requirement_coverage_critic"}:
            return self._critic_timeout_s or self._call_timeout_s
        return self._call_timeout_s

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
        timeout = self._timeout_for(node_name)
        route = self._route_for(node_name, tier)
        started = time.perf_counter()
        logger.info(
            "LLM started node=%s tier=%s model=%s thinking=%s timeout_s=%s",
            node_name, tier, route.model, route.thinking, timeout,
        )
        try:
            # One deadline includes format repair, tier escalation, and provider fallback.
            async with asyncio.timeout(timeout):
                return await self._complete_with_retries(
                    tier=tier, system_prompt=system_prompt, user_prompt=user_prompt,
                    response_model=response_model, meter=meter, node_name=node_name, temperature=temperature,
                    route=route,
                )
        except TimeoutError as exc:
            raise ProviderRequestError(f"node '{node_name}' exceeded its {timeout}s response deadline") from exc
        finally:
            logger.info(
                "LLM finished node=%s model=%s thinking=%s elapsed_s=%.2f",
                node_name, route.model, route.thinking, time.perf_counter() - started,
            )

    async def _complete_with_retries[T: BaseModel](
        self,
        *,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        meter: SessionTokenMeter | None = None,
        node_name: str = "unknown",
        temperature: float = 0.0,
        route: NodeRoute | None = None,
    ) -> StructuredResponse[T]:
        """Runs the call with tier-appropriate retries, escalating CHEAP→STRONG on
        exhaustion. Raises StructuredOutputError only if the strong tier also fails —
        callers treat that as 'state untouched'."""

        attempts_used = 0
        max_attempts = self._cheap_retries + 1 if tier == ModelTier.CHEAP else self._strong_retries + 1
        last_error: Exception | None = None
        error_feedback = ""

        # The gateway is the one layer that knows BOTH node_name (for labeling
        # in the UI) and whether a turn wants live reasoning at all (the
        # ContextVar, set by the SSE endpoint — see app/llm/streaming.py). A
        # provider that supports it streams a delta the instant it's
        # generated; one that doesn't just never calls this and nothing
        # changes for it. Never set for a turn with no sink registered
        # (e.g. every existing test, every non-streaming caller).
        raw_sink = get_reasoning_sink()
        on_delta = (lambda text: raw_sink(node_name, text)) if raw_sink is not None else None
        route = route or self._route_for(node_name, tier)

        for attempt in range(max_attempts):
            if meter:
                meter.check_before_call()
            attempts_used += 1
            model = route.model or self._provider.model_for_tier(tier)

            try:
                call_kwargs = {
                    "model": model,
                    "system_prompt": normalize_llm_text(system_prompt),
                    "user_prompt": normalize_llm_text(user_prompt + error_feedback),
                    "response_model": response_model,
                    "temperature": temperature,
                    "on_delta": on_delta,
                    "options": LLMCallOptions(thinking=route.thinking),
                }
                if not self._provider_accepts_options:
                    call_kwargs.pop("options")
                response = await self._provider.complete_structured(**call_kwargs)
            except (ProviderRequestError, ProviderQuotaExceededError):
                # API failures cannot be repaired by asking for different JSON.
                raise
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
                reasoning=response.reasoning,
            )
            response.attempts = attempts_used
            logger.info(
                "LLM result node=%s model=%s thinking=%s attempts=%d prompt_tokens=%d completion_tokens=%d tokens=%d",
                node_name, response.model, route.thinking, attempts_used,
                response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens,
            )
            return response

        if tier == ModelTier.CHEAP:
            logger.warning("cheap tier exhausted for node=%s, escalating to strong tier", node_name)
            escalated = await self._complete_with_retries(
                tier=ModelTier.STRONG,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                meter=meter,
                node_name=f"{node_name}:escalated",
                temperature=temperature,
                route=NodeRoute(self._provider.model_for_tier(ModelTier.STRONG), route.thinking),
            )
            escalated.attempts += attempts_used
            return escalated

        raise StructuredOutputError(
            f"node '{node_name}' failed to produce schema-valid output after {attempts_used} attempts: {last_error}"
        ) from last_error

"""Tests for the tier-escalation retry policy (DECISIONS.md): the cheap tier
escalates to strong after 1 failure rather than burning 3 uniform retries, because
it runs on every discovery turn and is the hallucination-containment-critical node."""

import pytest

from app.llm.base import LLMProvider, LLMUsage, ModelTier, StructuredOutputError, StructuredResponse
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.schemas import TargetArchitectureOutput


class ScriptedProvider(LLMProvider):
    """Fails the first `fail_count` calls per tier, then succeeds."""

    def __init__(self, fail_cheap: int = 0, fail_strong: int = 0) -> None:
        self.fail_cheap = fail_cheap
        self.fail_strong = fail_strong
        self.cheap_calls = 0
        self.strong_calls = 0

    def model_for_tier(self, tier: ModelTier) -> str:
        return "cheap-model" if tier == ModelTier.CHEAP else "strong-model"

    async def complete_structured(self, *, model, system_prompt, user_prompt, response_model, temperature=0.0):
        if model == "cheap-model":
            self.cheap_calls += 1
            if self.cheap_calls <= self.fail_cheap:
                raise StructuredOutputError("cheap tier schema failure")
        else:
            self.strong_calls += 1
            if self.strong_calls <= self.fail_strong:
                raise StructuredOutputError("strong tier schema failure")

        return StructuredResponse(
            parsed=response_model(description="ok") if response_model is TargetArchitectureOutput else response_model(),
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            model=model,
            attempts=1,
        )


@pytest.mark.asyncio
async def test_cheap_tier_success_never_touches_strong_model():
    provider = ScriptedProvider()
    gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)

    await gateway.complete(
        tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput
    )
    assert provider.cheap_calls == 1
    assert provider.strong_calls == 0


@pytest.mark.asyncio
async def test_cheap_tier_escalates_to_strong_after_configured_retries():
    # fail cheap twice: 1 initial + 1 retry exhausts the cheap budget -> escalate
    provider = ScriptedProvider(fail_cheap=2)
    gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)

    response = await gateway.complete(
        tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput
    )
    assert provider.cheap_calls == 2, "cheap tier should stop after 2 attempts, not 3+"
    assert provider.strong_calls == 1, "should have escalated to the strong tier"
    assert response.model == "strong-model"


@pytest.mark.asyncio
async def test_strong_tier_exhaustion_raises_rather_than_persisting_garbage():
    provider = ScriptedProvider(fail_strong=99)
    gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)

    with pytest.raises(StructuredOutputError):
        await gateway.complete(
            tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput
        )
    assert provider.strong_calls == 4  # 1 initial + 3 retries


@pytest.mark.asyncio
async def test_token_meter_records_usage_across_escalation():
    provider = ScriptedProvider(fail_cheap=2)
    gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)
    meter = SessionTokenMeter(budget=10_000)

    await gateway.complete(
        tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u",
        response_model=TargetArchitectureOutput, meter=meter,
    )
    # Only the successful call records usage; failed calls returned no usage data.
    assert meter.spent == 15

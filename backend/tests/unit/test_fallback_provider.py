"""FallbackLLMProvider: Anthropic stays primary; Groq only
activates if the primary's account has genuinely run out of quota/credits —
never for a transient failure. Exercised through the real LLMGateway retry
loop, since that's what actually drives the provider switch (see
FallbackLLMProvider's own docstring for why no gateway change was needed)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.base import ModelTier, ProviderQuotaExceededError, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.fallback_provider import FallbackLLMProvider
from app.llm.providers.openai_provider import MockProvider


class _Verdict(BaseModel):
    ok: bool


@pytest.mark.asyncio
async def test_primary_success_never_touches_the_fallback():
    primary = MockProvider()
    primary.register(_Verdict, _Verdict(ok=True))
    fallback = MockProvider()
    provider = FallbackLLMProvider(primary=primary, fallback=fallback)
    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(budget=100_000)

    response = await gateway.complete(
        tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter
    )

    assert response.parsed.ok is True
    assert len(primary.calls) == 1
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_quota_exhaustion_switches_to_fallback_within_the_same_call():
    primary = MockProvider()
    primary.register(_Verdict, ProviderQuotaExceededError("no credits"))
    fallback = MockProvider()
    fallback.register(_Verdict, _Verdict(ok=True))
    provider = FallbackLLMProvider(primary=primary, fallback=fallback)
    gateway = LLMGateway(provider, strong_tier_max_retries=3)
    meter = SessionTokenMeter(budget=100_000)

    response = await gateway.complete(
        tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter
    )

    assert response.parsed.ok is True
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_switch_is_sticky_across_later_calls_primary_not_retried():
    primary = MockProvider()
    primary.register(_Verdict, ProviderQuotaExceededError("no credits"))
    fallback = MockProvider()
    fallback.register(_Verdict, _Verdict(ok=True), _Verdict(ok=True))
    provider = FallbackLLMProvider(primary=primary, fallback=fallback)
    gateway = LLMGateway(provider, strong_tier_max_retries=3)
    meter = SessionTokenMeter(budget=100_000)

    await gateway.complete(tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter)
    await gateway.complete(tier=ModelTier.STRONG, system_prompt="s", user_prompt="u2", response_model=_Verdict, meter=meter)

    # Only the very first call ever reached the primary — the second call went
    # straight to the fallback since the switch is sticky for the provider's lifetime.
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2


@pytest.mark.asyncio
async def test_quota_exhaustion_with_no_fallback_configured_reraises():
    primary = MockProvider()
    primary.register(_Verdict, ProviderQuotaExceededError("no credits"))
    provider = FallbackLLMProvider(primary=primary, fallback=None)
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
    meter = SessionTokenMeter(budget=100_000)

    with pytest.raises(StructuredOutputError):
        await gateway.complete(
            tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter
        )


@pytest.mark.asyncio
async def test_ordinary_structured_output_failure_does_not_trigger_fallback():
    """A malformed response is a normal StructuredOutputError, not a quota
    issue — this must keep retrying against the primary, never switch."""

    primary = MockProvider()
    primary.register(_Verdict, StructuredOutputError("model returned garbage"), _Verdict(ok=True))
    fallback = MockProvider()
    provider = FallbackLLMProvider(primary=primary, fallback=fallback)
    gateway = LLMGateway(provider, strong_tier_max_retries=3)
    meter = SessionTokenMeter(budget=100_000)

    response = await gateway.complete(
        tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter
    )

    assert response.parsed.ok is True
    assert len(primary.calls) == 2
    assert fallback.calls == []

"""FallbackLLMProvider: Anthropic stays primary; Groq only
activates if the primary's account has genuinely run out of quota/credits —
never for a transient failure. Exercised through the real LLMGateway retry
loop, since that's what actually drives the provider switch (see
FallbackLLMProvider's own docstring for why no gateway change was needed)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.base import ModelTier, ProviderQuotaExceededError, ProviderRequestError, StructuredOutputError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.fallback_provider import FallbackLLMProvider
from app.llm.providers.openai_provider import MockProvider


class _Verdict(BaseModel):
    ok: bool


@pytest.mark.asyncio
async def test_transient_timeout_does_not_permanently_abandon_primary():
    primary = MockProvider()
    primary.register(_Verdict, ProviderRequestError("timeout"), _Verdict(ok=True))
    fallback = MockProvider()
    fallback.register(_Verdict, ProviderRequestError("fallback also unavailable"))
    gateway = LLMGateway(FallbackLLMProvider(primary, fallback))
    with pytest.raises(ProviderRequestError):
        await gateway.complete(
            tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=_Verdict,
        )
    result = await gateway.complete(
        tier=ModelTier.STRONG, system_prompt="s", user_prompt="u2", response_model=_Verdict,
    )
    assert result.parsed.ok
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1


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
    gateway = LLMGateway(provider, strong_tier_max_retries=0)
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


@pytest.mark.asyncio
async def test_provider_request_error_switches_to_fallback_same_as_quota():
    """ProviderRequestError (the primary genuinely unreachable - connection
    refused, timeout, a non-quota API error) triggers the same permanent
    switch as ProviderQuotaExceededError, not just a retry against the
    primary. By the time this reaches FallbackLLMProvider at all,
    LLMGateway's own retry budget has already been exhausted - see
    ProviderRequestError's own docstring for why treating it as sticky here
    is the right call, not a new risk."""

    primary = MockProvider()
    primary.register(_Verdict, ProviderRequestError("connection refused"))
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
async def test_two_deep_fallback_chain_resolves_within_a_single_gateway_attempt():
    """A two-deep chain (primary -> middle -> innermost) now cascades entirely
    within one complete_structured() call — each FallbackLLMProvider calls
    its own fallback inline on failure, rather than raising for the gateway
    to retry into (see fallback_provider.py's own docstring). A bare
    cheap_tier_max_retries=1, with no per-tier compensation, is enough to
    reach the innermost provider without ever escalating to the strong tier —
    unlike the old design, which needed one extra retry per fallback tier."""

    primary = MockProvider()
    primary.register(_Verdict, ProviderQuotaExceededError("no credits"))
    middle = MockProvider()
    middle.register(_Verdict, ProviderQuotaExceededError("no credits"))
    innermost = MockProvider()
    innermost.register(_Verdict, _Verdict(ok=True))

    inner_chain = FallbackLLMProvider(primary=middle, fallback=innermost)
    provider = FallbackLLMProvider(primary=primary, fallback=inner_chain)
    gateway = LLMGateway(provider, cheap_tier_max_retries=1, strong_tier_max_retries=3)
    meter = SessionTokenMeter(budget=100_000)

    response = await gateway.complete(
        tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=_Verdict, meter=meter
    )

    assert response.parsed.ok is True
    assert response.model == "mock-cheap"
    assert len(primary.calls) == 1
    assert len(middle.calls) == 1
    assert len(innermost.calls) == 1

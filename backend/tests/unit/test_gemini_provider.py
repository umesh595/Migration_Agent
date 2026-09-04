"""GeminiProvider: strict round-robin across every configured API key on every
call (not just on failure), and the escalation to ProviderQuotaExceededError
only once every key has individually come back quota-exhausted — a single
key's 429 must not look like total exhaustion to FallbackLLMProvider."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import BaseModel

from app.llm.base import ModelTier, ProviderQuotaExceededError, StructuredOutputError
from app.llm.providers.gemini_provider import GeminiProvider


class _Verdict(BaseModel):
    ok: bool


def _quota_error() -> APIError:
    return APIError(code=429, response_json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota exceeded"}})


def _fake_response(ok: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        parsed=_Verdict(ok=ok),
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
    )


@dataclass
class _Harness:
    provider: GeminiProvider
    calls: list[AsyncMock]


def _make_provider(n: int) -> _Harness:
    provider = GeminiProvider(api_keys=[f"key-{i}" for i in range(n)], cheap_model="gemini-2.5-flash", strong_model="gemini-2.5-pro")
    mocks = [AsyncMock(return_value=_fake_response()) for _ in range(n)]
    for client, mock in zip(provider._clients, mocks, strict=True):
        client.aio.models.generate_content = mock
    return _Harness(provider=provider, calls=mocks)


async def _call(provider: GeminiProvider) -> None:
    await provider.complete_structured(
        model=provider.model_for_tier(ModelTier.CHEAP),
        system_prompt="s",
        user_prompt="u",
        response_model=_Verdict,
    )


@pytest.mark.asyncio
async def test_every_call_advances_to_the_next_key_in_order():
    h = _make_provider(3)

    for _ in range(7):
        await _call(h.provider)

    # 7 calls over 3 keys in strict rotation: 0,1,2,0,1,2,0
    assert [m.call_count for m in h.calls] == [3, 2, 2]


@pytest.mark.asyncio
async def test_one_exhausted_key_is_skipped_not_treated_as_total_exhaustion():
    h = _make_provider(3)
    h.calls[1].side_effect = _quota_error()

    await _call(h.provider)  # key 0 — ok

    with pytest.raises(StructuredOutputError) as excinfo:
        await _call(h.provider)  # key 1 — quota error, but 2 keys remain
    assert not isinstance(excinfo.value, ProviderQuotaExceededError)

    await _call(h.provider)  # key 2 — ok
    await _call(h.provider)  # rotation skips exhausted key 1, lands on key 0

    assert h.calls[0].call_count == 2
    assert h.calls[1].call_count == 1
    assert h.calls[2].call_count == 1


@pytest.mark.asyncio
async def test_provider_quota_exceeded_only_once_every_key_is_exhausted():
    h = _make_provider(2)
    h.calls[0].side_effect = _quota_error()
    h.calls[1].side_effect = _quota_error()

    with pytest.raises(StructuredOutputError) as first:
        await _call(h.provider)
    assert not isinstance(first.value, ProviderQuotaExceededError)

    with pytest.raises(ProviderQuotaExceededError):
        await _call(h.provider)

    # A further call doesn't even need to hit the network — every key is known
    # exhausted already.
    with pytest.raises(ProviderQuotaExceededError):
        await _call(h.provider)
    assert h.calls[0].call_count == 1
    assert h.calls[1].call_count == 1


@pytest.mark.asyncio
async def test_single_key_provider_round_robins_trivially_onto_itself():
    h = _make_provider(1)

    await _call(h.provider)
    await _call(h.provider)

    assert h.calls[0].call_count == 2

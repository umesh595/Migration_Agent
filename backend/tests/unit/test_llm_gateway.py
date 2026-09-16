"""Tests for the tier-escalation retry policy (DECISIONS.md): the cheap tier
escalates to strong after 1 failure rather than burning 3 uniform retries, because
it runs on every discovery turn and is the hallucination-containment-critical node."""

import asyncio

import pytest

from app.llm.base import LLMProvider, LLMUsage, ModelTier, ProviderRequestError, StructuredOutputError, StructuredResponse
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

    async def complete_structured(self, *, model, system_prompt, user_prompt, response_model, temperature=0.0, on_delta=None):
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


@pytest.mark.asyncio
async def test_transport_error_is_not_retried_as_schema_repair():
    class Unavailable(ScriptedProvider):
        async def complete_structured(self, **kwargs):
            self.cheap_calls += 1
            raise ProviderRequestError("gateway unavailable")

    provider = Unavailable()
    with pytest.raises(ProviderRequestError):
        await LLMGateway(provider).complete(
            tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput,
        )
    assert provider.cheap_calls == 1


@pytest.mark.asyncio
async def test_deadline_cancels_inflight_request_without_retry():
    cancelled = asyncio.Event()

    class Slow(ScriptedProvider):
        async def complete_structured(self, **kwargs):
            self.strong_calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    provider = Slow()
    gateway = LLMGateway(provider, call_timeout_s=0.02)
    with pytest.raises(ProviderRequestError, match="deadline"):
        await gateway.complete(
            tier=ModelTier.STRONG, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput,
        )
    assert cancelled.is_set()
    assert provider.strong_calls == 1


@pytest.mark.asyncio
async def test_deadline_includes_tier_escalation():
    class SlowInvalid(ScriptedProvider):
        async def complete_structured(self, **kwargs):
            if kwargs["model"] == "cheap-model":
                self.cheap_calls += 1
            else:
                self.strong_calls += 1
            await asyncio.sleep(0.04)
            raise StructuredOutputError("invalid JSON")

    provider = SlowInvalid()
    gateway = LLMGateway(provider, cheap_tier_max_retries=0, call_timeout_s=0.06)
    with pytest.raises(ProviderRequestError, match="deadline"):
        await gateway.complete(
            tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput,
        )
    assert provider.cheap_calls == 1
    assert provider.strong_calls == 1


@pytest.mark.asyncio
async def test_no_reasoning_sink_registered_means_provider_gets_no_delta_callback():
    """The common case (every existing caller, every test above): nothing sets
    a reasoning sink, so the provider must receive on_delta=None, exactly as
    if the streaming feature didn't exist."""

    received = {}

    class Capturing(ScriptedProvider):
        async def complete_structured(self, **kwargs):
            received["on_delta"] = kwargs.get("on_delta")
            return await super().complete_structured(**kwargs)

    await LLMGateway(Capturing()).complete(
        tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u", response_model=TargetArchitectureOutput,
    )
    assert received["on_delta"] is None


@pytest.mark.asyncio
async def test_reasoning_sink_receives_node_name_and_delta_text_when_registered():
    """When the SSE endpoint has registered a sink for this turn (see
    app/llm/streaming.py), the gateway must build a per-call callback that
    tags each delta with the node_name IT knows (the provider has no idea
    what node it's running as) and forwards the text unchanged."""

    from app.llm.streaming import reasoning_sink_scope

    received_deltas = []

    async def sink(node_name: str, text: str) -> None:
        received_deltas.append((node_name, text))

    class Streaming(ScriptedProvider):
        async def complete_structured(self, **kwargs):
            on_delta = kwargs.get("on_delta")
            assert on_delta is not None
            await on_delta("thinking about the request")
            await on_delta(" ...and a bit more")
            return await super().complete_structured(**{k: v for k, v in kwargs.items() if k != "on_delta"})

    with reasoning_sink_scope(sink):
        await LLMGateway(Streaming()).complete(
            tier=ModelTier.CHEAP, system_prompt="s", user_prompt="u",
            response_model=TargetArchitectureOutput, node_name="discovery.ingest",
        )

    assert received_deltas == [
        ("discovery.ingest", "thinking about the request"),
        ("discovery.ingest", " ...and a bit more"),
    ]

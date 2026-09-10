import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.base import LLMUsage, ProviderRequestError, StructuredResponse
from app.llm.providers.codevector_provider import CodeVectorProvider


class Verdict(BaseModel):
    ok: bool


def completion(content='{"ok":true}', reasoning_content=None):
    message = {"role": "assistant", "content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return httpx.Response(200, json={
        "id": "test", "object": "chat.completion", "created": 0, "model": "kimi-test",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    })


@pytest.mark.asyncio
async def test_no_hidden_sdk_retries_on_timeout():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow upstream", request=request)

    provider = CodeVectorProvider("test", "https://gateway.invalid/v1", "kimi-test", "kimi-test")
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            with pytest.raises(ProviderRequestError):
                await provider.complete_structured(
                    model="kimi-test", system_prompt="s", user_prompt="u", response_model=Verdict,
                )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
async def test_schema_sent_once_and_all_user_context_preserved(mode):
    import json

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return completion()

    provider = CodeVectorProvider(
        "test", "https://gateway.invalid/v1", "kimi-test", "kimi-test", response_format=mode,
    )
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            result = await provider.complete_structured(
                model="kimi-test", system_prompt="All reasoning instructions", user_prompt="Full conversation history",
                response_model=Verdict,
            )
    assert result.parsed.ok
    assert requests[0]["temperature"] == 1.0
    assert requests[0]["response_format"]["type"] == mode
    if mode == "json_schema":
        assert requests[0]["response_format"]["json_schema"]["schema"] == Verdict.model_json_schema()
        assert '"properties"' not in requests[0]["messages"][0]["content"]
    else:
        assert '"properties"' in requests[0]["messages"][0]["content"]
    assert requests[0]["messages"][1]["content"] == "Full conversation history"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['```json\n{"ok":true}\n```', '{"ok":true}'])
async def test_complete_json_fence_does_not_need_another_model_call(content):
    requests = []

    def handle(request):
        requests.append(request)
        return completion(content)

    provider = CodeVectorProvider("test", "https://gateway.invalid/v1", "kimi-test", "kimi-test")
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            result = await provider.complete_structured(
                model="kimi-test", system_prompt="s", user_prompt="u", response_model=Verdict,
            )
    assert result.parsed.ok
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_codevector_retries_the_configured_model_before_provider_fallback(monkeypatch):
    provider = CodeVectorProvider(
        "test", "https://gateway.invalid/v1", "deepseekv4p", "deepseekv4p", fallback_model="kimi-k-2-7-code",
    )
    attempted_models = []

    async def complete_for_model(*, model, **_kwargs):
        attempted_models.append(model)
        if model == "deepseekv4p":
            raise ProviderRequestError("DeepSeek unavailable")
        return StructuredResponse(
            parsed=Verdict(ok=True), usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            model=model, attempts=1,
        )

    monkeypatch.setattr(provider, "_complete_structured_for_model", complete_for_model)

    result = await provider.complete_structured(
        model="deepseekv4p", system_prompt="s", user_prompt="u", response_model=Verdict,
    )

    assert result.model == "kimi-k-2-7-code"
    assert attempted_models == ["deepseekv4p", "kimi-k-2-7-code"]


@pytest.mark.asyncio
async def test_reasoning_content_is_captured_not_discarded():
    """Verified live against the real gateway: a reasoning-tier model (deepseekv4p)
    returns its chain-of-thought as `reasoning_content`, separate from the
    schema-constrained `content` — previously read nowhere and silently
    dropped. It must now surface on the returned StructuredResponse so a
    caller can log it or hand it to a critic call."""

    def handle(request):
        return completion(reasoning_content="Working through the request step by step before answering.")

    provider = CodeVectorProvider("test", "https://gateway.invalid/v1", "kimi-test", "kimi-test")
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            result = await provider.complete_structured(
                model="kimi-test", system_prompt="s", user_prompt="u", response_model=Verdict,
            )

    assert result.reasoning == "Working through the request step by step before answering."


def _sse_stream(*chunks: dict) -> httpx.Response:
    """Builds a text/event-stream response body matching the OpenAI streaming
    chunk format (`ChatCompletionChunk`), the shape the real SDK's AsyncStream
    parses — verified live against this gateway (see probe_stream.py) before
    building on it."""

    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def _chunk(content: str = "", reasoning_content: str | None = None) -> dict:
    delta: dict = {}
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content
    if content:
        delta["content"] = content
    return {
        "id": "test", "object": "chat.completion.chunk", "created": 0, "model": "kimi-test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


@pytest.mark.asyncio
async def test_streaming_forwards_each_reasoning_delta_as_it_arrives():
    """The live-verified path this feature exists for: on_delta must be called
    once per reasoning_content fragment, in order, as the response streams —
    not batched, not delayed until the stream ends — while the final
    StructuredResponse still carries the complete accumulated text and a
    schema-valid parsed result, identical in shape to the non-streaming path."""

    chunks = [
        _chunk(reasoning_content="Thinking about "),
        _chunk(reasoning_content="the request step by step."),
        _chunk(content='{"ok"'),
        _chunk(content=":true}"),
    ]

    def handle(request):
        return _sse_stream(*chunks)

    received: list[str] = []

    async def on_delta(text: str) -> None:
        received.append(text)

    provider = CodeVectorProvider("test", "https://gateway.invalid/v1", "kimi-test", "kimi-test")
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            result = await provider.complete_structured(
                model="kimi-test", system_prompt="s", user_prompt="u", response_model=Verdict, on_delta=on_delta,
            )

    assert received == ["Thinking about ", "the request step by step."]
    assert result.reasoning == "Thinking about the request step by step."
    assert result.parsed.ok is True


@pytest.mark.asyncio
async def test_reasoning_defaults_to_none_when_the_provider_does_not_return_one():
    def handle(request):
        return completion()

    provider = CodeVectorProvider("test", "https://gateway.invalid/v1", "kimi-test", "kimi-test")
    async with provider._client:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider._client._client = client
            result = await provider.complete_structured(
                model="kimi-test", system_prompt="s", user_prompt="u", response_model=Verdict,
            )

    assert result.reasoning is None

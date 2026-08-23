"""Exercises RestCatalogProvider's OAuth2 client-credentials flow, token
caching, and retry policy against an httpx.MockTransport -- no real network,
no third-party sandbox account needed, and no new test dependency (httpx's
own MockTransport, not respx/pytest-httpx)."""

import httpx
import pytest

from app.integrations.catalog_provider import CatalogProviderError
from app.integrations.rest_catalog_provider import RestCatalogProvider


def _provider(handler, timeout_s: float = 5.0) -> RestCatalogProvider:
    return RestCatalogProvider(
        base_url="https://catalog.example.com",
        token_url="https://auth.example.com/oauth/token",
        client_id="test-client",
        client_secret="test-secret",
        timeout_s=timeout_s,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_fetches_token_then_calls_search_with_bearer_header():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 300})
        assert request.headers["authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={"components": [], "dependencies": []})

    provider = _provider(handler)
    result = await provider.fetch_components(query="orders")

    assert result.components == []
    assert [c.url.path for c in calls] == ["/oauth/token", "/api/v1/catalog/search"]


@pytest.mark.asyncio
async def test_second_call_reuses_cached_token_without_a_second_token_request():
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path == "/oauth/token":
            token_requests += 1
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 300})
        return httpx.Response(200, json={"components": [], "dependencies": []})

    provider = _provider(handler)
    await provider.fetch_components(query="orders")
    await provider.fetch_components(query="payments")

    assert token_requests == 1


@pytest.mark.asyncio
async def test_expired_token_is_refetched():
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if request.url.path == "/oauth/token":
            token_requests += 1
            # expires_in shorter than the 30s safety margin -> already
            # considered expired the moment it's cached.
            return httpx.Response(200, json={"access_token": f"tok-{token_requests}", "expires_in": 1})
        return httpx.Response(200, json={"components": [], "dependencies": []})

    provider = _provider(handler)
    await provider.fetch_components(query="orders")
    await provider.fetch_components(query="orders")

    assert token_requests == 2


@pytest.mark.asyncio
async def test_401_on_search_invalidates_cached_token_so_the_next_call_refetches_it():
    """A 401 means the server has already rejected this token -- caching it
    across the failure and reusing it on the next call would just fail the
    same way again forever. Verified behaviorally: after the 401, a
    subsequent call must request a fresh token rather than reuse the
    rejected one, not by inspecting the cache field directly."""

    token_requests = 0
    search_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests, search_calls
        if request.url.path == "/oauth/token":
            token_requests += 1
            return httpx.Response(200, json={"access_token": f"tok-{token_requests}", "expires_in": 300})
        search_calls += 1
        if search_calls == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"components": [], "dependencies": []})

    provider = _provider(handler)

    with pytest.raises(CatalogProviderError, match="rejected"):
        await provider.fetch_components(query="orders")
    assert token_requests == 1

    result = await provider.fetch_components(query="orders")
    assert result.components == []
    assert token_requests == 2  # refetched instead of reusing the rejected token


@pytest.mark.asyncio
async def test_malformed_token_response_raises_without_retrying_pointlessly():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"not_access_token": "x"})

    provider = _provider(handler)

    with pytest.raises(CatalogProviderError, match="did not match expected shape"):
        await provider.fetch_components(query="orders")

    assert calls == 1  # a schema mismatch is not a transport error -- no retry


@pytest.mark.asyncio
async def test_connection_failure_is_retried_then_wrapped_as_catalog_provider_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)

    with pytest.raises(CatalogProviderError, match="unreachable after retries"):
        await provider.fetch_components(query="orders")

    assert attempts == 3  # stop_after_attempt(3)


@pytest.mark.asyncio
async def test_transient_connection_failure_followed_by_success_still_succeeds():
    """The whole point of retrying at all: a request that fails once and then
    succeeds must return the successful result, not the earlier failure."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 300})
        if attempts <= 2:
            raise httpx.ConnectError("transient")
        return httpx.Response(200, json={"components": [], "dependencies": []})

    provider = _provider(handler)
    result = await provider.fetch_components(query="orders")

    assert result.components == []

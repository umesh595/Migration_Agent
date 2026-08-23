"""httpx + OAuth2 client-credentials adapter for CatalogProvider. Hand-rolled
token fetch/cache rather than a heavy OAuth2 framework — consistent with this
codebase's existing convention of small hand-rolled primitives instead of
pulling in a library for something this contained (see
app/security/session_lock.py, app/security/rate_limit.py)."""

from __future__ import annotations

import logging
import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.catalog_provider import (
    CatalogComponentRecord,
    CatalogDependencyRecord,
    CatalogFetchResult,
    CatalogProvider,
    CatalogProviderError,
)

logger = logging.getLogger(__name__)

# Refresh slightly before the token's actual expiry, not exactly at it — a
# request that starts a few seconds before real expiry must not have its
# token die mid-flight.
_TOKEN_EXPIRY_SAFETY_MARGIN_S = 30.0


class _CachedToken:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: str, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at

    def is_valid(self) -> bool:
        return time.monotonic() < self.expires_at


class RestCatalogProvider(CatalogProvider):
    """Adapter for a REST-based enterprise catalog/CMDB exposing OAuth2
    client-credentials auth and a components/dependencies search endpoint.

    Retry policy: only transport-level failures (connection refused, DNS,
    timeout — httpx.TransportError) are retried, up to 3 attempts with
    exponential backoff. A definitive HTTP-status rejection (invalid client
    credentials, 404, malformed response body) is raised immediately as
    CatalogProviderError without wasting retries on something retrying won't
    fix."""

    def __init__(
        self,
        *,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_s = timeout_s
        # None means "real network" (httpx's default). Overridable so tests can
        # inject an httpx.MockTransport instead of hitting a real server —
        # without this seam, the OAuth2 flow and retry/caching logic above would
        # only be exercisable through a live third-party system.
        self._transport = transport
        self._token: _CachedToken | None = None

    async def _request_token(self, client: httpx.AsyncClient) -> tuple[str, float]:
        """Returns (access_token, expires_in_seconds). May raise
        httpx.TransportError (connection-level — retryable by the caller) or
        CatalogProviderError (a definitive rejection, e.g. invalid client
        credentials — not worth retrying)."""

        response = await client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=self._timeout_s,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CatalogProviderError(f"OAuth2 token request rejected: {exc}") from exc

        try:
            payload = response.json()
            access_token = payload["access_token"]
            expires_in = float(payload.get("expires_in", 300))
        except Exception as exc:
            raise CatalogProviderError(f"OAuth2 token response did not match expected shape: {exc}") from exc

        return access_token, expires_in

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and self._token.is_valid():
            return self._token.value

        access_token, expires_in = await self._request_token(client)
        self._token = _CachedToken(
            value=access_token,
            expires_at=time.monotonic() + expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN_S,
        )
        return self._token.value

    async def _search_once(self, client: httpx.AsyncClient, token: str, query: str) -> CatalogFetchResult:
        response = await client.get(
            "/api/v1/catalog/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                # The server may have revoked the token before our cached
                # expiry thought it would; drop it so the *next* call fetches
                # a fresh one instead of retrying with the same rejected token.
                self._token = None
            raise CatalogProviderError(f"catalog search request rejected: {exc}") from exc

        try:
            payload = response.json()
            components = [CatalogComponentRecord.model_validate(c) for c in payload.get("components", [])]
            dependencies = [CatalogDependencyRecord.model_validate(d) for d in payload.get("dependencies", [])]
        except Exception as exc:
            raise CatalogProviderError(f"catalog response did not match expected shape: {exc}") from exc

        return CatalogFetchResult(components=components, dependencies=dependencies)

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4.0),
        reraise=True,
    )
    async def _fetch_with_retry(self, query: str) -> CatalogFetchResult:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_s, transport=self._transport
        ) as client:
            token = await self._get_access_token(client)
            return await self._search_once(client, token, query)

    async def fetch_components(self, *, query: str) -> CatalogFetchResult:
        try:
            return await self._fetch_with_retry(query)
        except httpx.TransportError as exc:
            # Every retry attempt failed at the transport level.
            raise CatalogProviderError(f"catalog system unreachable after retries: {exc}") from exc

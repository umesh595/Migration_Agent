"""Test double for CatalogProvider. Mirrors app/llm/providers/openai_provider.py's
MockProvider: deterministic, no network, callers pre-register a canned result
or an error to raise."""

from __future__ import annotations

from app.integrations.catalog_provider import CatalogFetchResult, CatalogProvider, CatalogProviderError


class MockCatalogProvider(CatalogProvider):
    def __init__(
        self,
        *,
        result: CatalogFetchResult | None = None,
        error: CatalogProviderError | None = None,
    ) -> None:
        self._result = result if result is not None else CatalogFetchResult()
        self._error = error
        self.received_queries: list[str] = []

    async def fetch_components(self, *, query: str) -> CatalogFetchResult:
        self.received_queries.append(query)
        if self._error is not None:
            raise self._error
        return self._result

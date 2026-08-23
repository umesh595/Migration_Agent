"""Enterprise catalog integration boundary. Mirrors app/llm/base.py's
LLMProvider pattern: one ABC here, swappable concrete adapters in this
package (app/integrations/rest_catalog_provider.py is the real one,
app/integrations/mock_catalog_provider.py is the test double), callers
depend only on CatalogProvider — never on a concrete adapter directly."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class CatalogComponentRecord(BaseModel):
    """One component as reported by an external enterprise catalog/CMDB
    (e.g. a Salesforce object, a ServiceNow CMDB CI, or an internal service
    catalog entry). Field names are deliberately generic, not tied to any
    one vendor's schema — app/integrations/mapper.py owns translating vendor
    free-text hints into this project's own WorkloadType/Environment enums."""

    external_id: str
    name: str
    workload_type_hint: str = Field(default="", description="Free-text type from the source system.")
    technology: str | None = None
    owner_team: str | None = None
    environment_hint: str = Field(default="", description="Free-text environment from the source system.")


class CatalogDependencyRecord(BaseModel):
    source_external_id: str
    target_external_id: str
    kind_hint: str = Field(default="", description="Free-text dependency kind from the source system.")


class CatalogFetchResult(BaseModel):
    components: list[CatalogComponentRecord] = Field(default_factory=list)
    dependencies: list[CatalogDependencyRecord] = Field(default_factory=list)


class CatalogProviderError(Exception):
    """Raised whenever the external catalog system cannot be reached or
    returns something outside its documented contract. Callers must treat
    this as 'no data' and never persist a partial result — mirrors
    StructuredOutputError's contract in app/llm/base.py."""


class CatalogProvider(ABC):
    @abstractmethod
    async def fetch_components(self, *, query: str) -> CatalogFetchResult:
        """Fetches components/dependencies matching `query` from the external
        enterprise catalog. Must raise CatalogProviderError on any failure —
        never return an empty result to signal an error silently, since an
        empty result and 'nothing matched the query' must stay distinguishable
        to the caller."""

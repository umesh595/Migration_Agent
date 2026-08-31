"""Deterministic cost estimation — never an LLM call (technique #12: a fabricated
dollar figure is worse than no figure at all).

Pricing sources, by provider:
  - Azure: Azure's public Retail Prices API, called live per request (briefly
    cached — see _TTLCache) and requiring no credentials. Small, directly
    filterable responses; a genuinely viable live lookup.
  - AWS: read from a local JSON snapshot (pricing_data/aws_snapshot.json) built by
    scripts/refresh_aws_pricing.py from AWS's own public bulk Price List API.
    Those per-region-per-service files run into the hundreds of MB (the EC2 one
    alone is ~350MB), so a live per-request fetch isn't viable the way Azure's is
    — this snapshot is real AWS pricing data, just refreshed out-of-band rather
    than on every request. Re-run the script periodically to keep it current.
  - GCP: requires GCP_BILLING_API_KEY. Without one, GCP components are reported
    as unestimated rather than guessed — see _price_gcp.

Every estimate is built from a stated default SIZING ASSUMPTION (see _SIZING) since
no document fed into this tool states real usage/instance-size numbers. That
assumption is always attached to the estimate, never hidden behind a bare number.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx

from app.config import get_settings
from app.schemas.architecture import ArchitectureModel
from app.schemas.cost import CloudProvider, CostEstimate, CostSummary, ServiceCategory
from app.schemas.migration_plan import MigrationPlan, SevenR

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730  # the standard monthly-hour convention AWS/Azure/GCP calculators themselves use
_AWS_SNAPSHOT_PATH = Path(__file__).parent / "pricing_data" / "aws_snapshot.json"


@dataclass(frozen=True)
class _SizingTier:
    text: str
    quantity: float  # meaning depends on unit_kind: instance-count for hourly, GB for storage, message-count for per_request
    unit_kind: Literal["hourly", "per_gb_month", "per_request"]


# Deliberately small/conservative defaults so a plan has SOME honest cost signal
# without requiring the user to have stated real usage numbers. Extending coverage
# to a new category means adding an entry here AND a lookup for it in each
# provider's _price_* function below — a category missing from either is reported
# as unestimated (CostSummary.unestimated_component_ids), never silently priced at 0.
_SIZING: dict[ServiceCategory, _SizingTier] = {
    ServiceCategory.COMPUTE_VM: _SizingTier(
        "1x small general-purpose instance (2 vCPU, 4GB), on-demand, running 24/7", 1, "hourly"
    ),
    ServiceCategory.MANAGED_DATABASE: _SizingTier(
        "1x small burstable instance (2 vCPU, 4GB), single-AZ, running 24/7", 1, "hourly"
    ),
    ServiceCategory.OBJECT_STORAGE: _SizingTier("100 GB stored", 100, "per_gb_month"),
    ServiceCategory.MESSAGE_QUEUE: _SizingTier("1,000,000 messages/month", 1_000_000, "per_request"),
    ServiceCategory.CACHE: _SizingTier("1x smallest cache node, running 24/7", 1, "hourly"),
}


def _monthly_cost(unit_price_usd: float, category: ServiceCategory) -> float:
    """`unit_price_usd` must already be normalized to the category's base unit
    (per hour / per GB-month / per single request) — each pricing source is
    responsible for that normalization at the point it reads its raw source."""

    tier = _SIZING[category]
    if tier.unit_kind == "hourly":
        return unit_price_usd * HOURS_PER_MONTH * tier.quantity
    return unit_price_usd * tier.quantity


class _TTLCache:
    """So a plan with several components in the same category doesn't re-hit a
    live pricing API once per component in one request. Process-local, not shared
    across replicas — acceptable for this tool's scale; the TTL bounds how stale a
    served price can get without needing a shared cache."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, _PriceLookup | None]] = {}

    def get(self, key: str) -> _PriceLookup | None | Literal['MISS']:
        entry = self._store.get(key)
        if entry is None:
            return "MISS"
        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._store[key]
            return "MISS"
        return value

    def set(self, key: str, value: _PriceLookup | None) -> None:
        self._store[key] = (time.monotonic(), value)


@dataclass(frozen=True)
class _PriceLookup:
    monthly_usd: float
    sku_description: str
    pricing_source: str
    priced_at: str


_azure_cache = _TTLCache(ttl_seconds=6 * 3600)
_aws_snapshot: dict | None = None


def reset_caches() -> None:
    """Test-only: the Azure price cache and the loaded AWS snapshot are module-
    level state so a real deployment doesn't re-fetch/re-read on every call within
    the TTL — tests that vary mocked responses across cases must reset both first,
    or a later case would silently see an earlier case's cached result."""

    global _aws_snapshot
    _azure_cache._store.clear()
    _aws_snapshot = None


def _load_aws_snapshot() -> dict:
    global _aws_snapshot
    if _aws_snapshot is None:
        if not _AWS_SNAPSHOT_PATH.exists():
            logger.warning("AWS pricing snapshot not found at %s — run scripts/refresh_aws_pricing.py", _AWS_SNAPSHOT_PATH)
            _aws_snapshot = {"fetched_at": None, "prices": {}}
        else:
            _aws_snapshot = json.loads(_AWS_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return _aws_snapshot


async def _price_aws(category: ServiceCategory) -> _PriceLookup | None:
    snapshot = _load_aws_snapshot()
    entry = snapshot.get("prices", {}).get(category.value)
    if entry is None:
        return None
    return _PriceLookup(
        monthly_usd=_monthly_cost(entry["unit_price_usd"], category),
        sku_description=entry["sku_description"],
        pricing_source="AWS Price List API (snapshot — see scripts/refresh_aws_pricing.py)",
        priced_at=snapshot.get("fetched_at") or "unknown",
    )


# Each entry: how to filter Azure's Retail Prices API down to the one SKU/meter we
# price, which of the (often several) returned rows is the right one, the
# human-readable SKU description, and the divisor to normalize the raw unitPrice
# to this category's base unit (see _SizingTier.unit_kind).
_AZURE_QUERIES: dict[ServiceCategory, dict] = {
    ServiceCategory.COMPUTE_VM: {
        "filter": "armSkuName eq 'Standard_D2s_v3' and armRegionName eq 'eastus' and priceType eq 'Consumption'",
        "match": lambda item: item["skuName"] == "D2s v3" and "Windows" not in item["productName"],
        "sku_description": "Azure Virtual Machines Standard_D2s_v3, Linux, pay-as-you-go, East US",
        "divisor": 1,
    },
    ServiceCategory.MANAGED_DATABASE: {
        "filter": (
            "serviceName eq 'Azure Database for MySQL' and armRegionName eq 'eastus' "
            "and contains(productName, 'Burstable')"
        ),
        "match": lambda item: item["skuName"] == "B2S",
        "sku_description": "Azure Database for MySQL Flexible Server, Burstable B2s, East US",
        "divisor": 1,
    },
    ServiceCategory.OBJECT_STORAGE: {
        "filter": "productName eq 'Blob Storage' and armRegionName eq 'eastus'",
        "match": lambda item: item["skuName"] == "Hot LRS" and item["meterName"] == "Hot LRS Data Stored",
        "sku_description": "Azure Blob Storage, Hot tier, LRS, East US",
        "divisor": 1,
    },
    ServiceCategory.MESSAGE_QUEUE: {
        "filter": "serviceName eq 'Service Bus' and armRegionName eq 'eastus'",
        "match": lambda item: item["skuName"] == "Basic" and item["meterName"] == "Basic Messaging Operations",
        "sku_description": "Azure Service Bus, Basic tier, East US",
        "divisor": 1_000_000,  # raw price is USD per 1,000,000 operations
    },
    ServiceCategory.CACHE: {
        "filter": "serviceName eq 'Redis Cache' and armRegionName eq 'eastus'",
        "match": lambda item: item["productName"] == "Azure Redis Cache Basic" and item["skuName"] == "C1",
        "sku_description": "Azure Cache for Redis, Basic C1, East US",
        "divisor": 1,
    },
}


async def _price_azure(category: ServiceCategory, client: httpx.AsyncClient) -> _PriceLookup | None:
    query = _AZURE_QUERIES.get(category)
    if query is None:
        return None

    cache_key = f"azure:{category.value}"
    cached = _azure_cache.get(cache_key)
    if cached != "MISS":
        return cached

    try:
        resp = await client.get(
            "https://prices.azure.com/api/retail/prices",
            params={"$filter": query["filter"], "$top": 50},
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("Items", [])
    except httpx.HTTPError as exc:
        logger.warning("azure pricing lookup failed for %s: %s", category.value, exc)
        return None

    match = next((item for item in items if query["match"](item)), None)
    if match is None:
        logger.warning("azure pricing lookup found no matching SKU for %s", category.value)
        _azure_cache.set(cache_key, None)
        return None

    result = _PriceLookup(
        monthly_usd=_monthly_cost(match["unitPrice"] / query["divisor"], category),
        sku_description=query["sku_description"],
        pricing_source="Azure Retail Prices API (live)",
        priced_at=datetime.now(UTC).date().isoformat(),
    )
    _azure_cache.set(cache_key, result)
    return result


async def _price_gcp(category: ServiceCategory, client: httpx.AsyncClient) -> _PriceLookup | None:
    settings = get_settings()
    if not settings.gcp_billing_api_key:
        return None
    # Documented extension point, not a placeholder: the GCP Cloud Billing Catalog
    # API needs a per-service SKU search (billing.googleapis.com/v1/services/
    # {service}/skus) that's a materially different shape from Azure's flat
    # filterable list, and no key has been configured to build/test it against.
    # Wire this up the same way _price_azure is wired once a real key exists —
    # returning None here means "unestimated," never a fabricated number.
    logger.info("GCP_BILLING_API_KEY is set but GCP pricing lookup is not yet implemented for %s", category.value)
    return None


_PROVIDER_PRICERS: dict[CloudProvider, Callable[[ServiceCategory, httpx.AsyncClient], Awaitable[_PriceLookup | None]]] = {
    CloudProvider.AWS: lambda category, client: _price_aws(category),
    CloudProvider.AZURE: _price_azure,
    CloudProvider.GCP: _price_gcp,
}


async def estimate_plan_cost(
    model: ArchitectureModel, plan: MigrationPlan, *, http_client: httpx.AsyncClient | None = None
) -> CostSummary:
    """Deliverable 11 — Cost Estimate. Walks every non-retired component mapping,
    prices it if its (provider, category) combination is covered, and reports
    everything else as unestimated rather than silently omitting it.

    `http_client` is injectable (same pattern as RestCatalogProvider's `transport`
    param) so tests can pass an httpx.AsyncClient(transport=httpx.MockTransport(...))
    instead of hitting the real network."""

    estimates: list[CostEstimate] = []
    unestimated: list[str] = []

    async def _run(client: httpx.AsyncClient) -> None:
        for mapping in plan.component_mappings:
            if mapping.disposition == SevenR.RETIRE:
                continue  # a retired component has no target cost to estimate

            pricer = _PROVIDER_PRICERS.get(mapping.target_cloud_provider)
            sizing = _SIZING.get(mapping.target_service_category)
            lookup = await pricer(mapping.target_service_category, client) if pricer and sizing else None

            if lookup is None:
                unestimated.append(mapping.component_id)
                estimates.append(
                    CostEstimate(
                        component_id=mapping.component_id,
                        provider=mapping.target_cloud_provider,
                        service_category=mapping.target_service_category,
                        sku_description=mapping.target_description,
                        sizing_assumption=sizing.text if sizing else "not yet covered by cost estimation",
                        monthly_usd=None,
                        pricing_source="none",
                        priced_at=datetime.now(UTC).date().isoformat(),
                        note=_unestimated_reason(mapping.target_cloud_provider, mapping.target_service_category),
                    )
                )
                continue

            estimates.append(
                CostEstimate(
                    component_id=mapping.component_id,
                    provider=mapping.target_cloud_provider,
                    service_category=mapping.target_service_category,
                    sku_description=lookup.sku_description,
                    sizing_assumption=sizing.text,
                    monthly_usd=round(lookup.monthly_usd, 2),
                    pricing_source=lookup.pricing_source,
                    priced_at=lookup.priced_at,
                )
            )

    if http_client is not None:
        await _run(http_client)
    else:
        async with httpx.AsyncClient() as client:
            await _run(client)

    total = sum(e.monthly_usd for e in estimates if e.monthly_usd is not None)
    return CostSummary(estimates=estimates, total_monthly_usd=round(total, 2), unestimated_component_ids=unestimated)


def _unestimated_reason(provider: CloudProvider, category: ServiceCategory) -> str:
    if provider == CloudProvider.ON_PREM:
        return "on-prem cost depends on this org's own infrastructure/licensing, not a cloud price list"
    if provider == CloudProvider.UNKNOWN:
        return "target cloud provider wasn't determined for this component"
    if provider == CloudProvider.GCP:
        return "GCP pricing requires GCP_BILLING_API_KEY, which isn't configured"
    if category not in _SIZING:
        return f"'{category.value}' isn't covered by cost estimation yet"
    return f"no {provider.value} pricing available for '{category.value}'"

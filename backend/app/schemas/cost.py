"""Cost estimation types. Every number here traces to a real cloud pricing source
— Azure prices are fetched live from Azure's public Retail Prices API on every
request (cached briefly); AWS prices are read from a periodically-refreshed local
snapshot built from AWS's own public bulk pricing API (see
scripts/refresh_aws_pricing.py — AWS's per-region-per-service files run into the
hundreds of MB, so a live per-request fetch isn't viable the way Azure's directly-
queryable API is). GCP requires an API key; without one, GCP components are
reported as unestimated rather than guessed.

Never LLM-generated: technique #12 (never regenerate typed content as fresh prose)
applies to dollar figures more than anywhere else in this system — a fabricated
cost estimate is actively worse than none at all.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CloudProvider(StrEnum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    ON_PREM = "on_prem"
    UNKNOWN = "unknown"


class ServiceCategory(StrEnum):
    COMPUTE_VM = "compute_vm"
    COMPUTE_SERVERLESS = "compute_serverless"
    MANAGED_DATABASE = "managed_database"
    OBJECT_STORAGE = "object_storage"
    BLOCK_STORAGE = "block_storage"
    MESSAGE_QUEUE = "message_queue"
    CACHE = "cache"
    CDN = "cdn"
    LOAD_BALANCER = "load_balancer"
    ML_INFERENCE = "ml_inference"
    OTHER = "other"


class CostEstimate(BaseModel):
    component_id: str
    provider: CloudProvider
    service_category: ServiceCategory
    sku_description: str = Field(description="The specific priced SKU, e.g. 'AWS EC2 t3.medium, Linux, On-Demand'.")
    sizing_assumption: str = Field(description="The usage/size assumption behind this estimate, stated plainly.")
    monthly_usd: float | None = Field(
        default=None, description="None when this provider/category combination isn't priced yet — see `note`."
    )
    pricing_source: str = Field(description="Where the unit price came from, e.g. 'Azure Retail Prices API (live)'.")
    priced_at: str = Field(description="ISO date the underlying price was fetched or the snapshot was built.")
    note: str | None = Field(default=None, description="Set when monthly_usd is None, or to flag a caveat.")


class CostSummary(BaseModel):
    estimates: list[CostEstimate] = Field(default_factory=list)
    total_monthly_usd: float = 0.0
    unestimated_component_ids: list[str] = Field(default_factory=list)
    methodology_note: str = Field(
        default=(
            "Directional estimates from default sizing assumptions (see each component's sizing_assumption) "
            "and real cloud list pricing — not a quote. Actual cost depends on real usage volume, reserved/"
            "savings-plan discounts, and negotiated rates, none of which this tool has visibility into."
        )
    )

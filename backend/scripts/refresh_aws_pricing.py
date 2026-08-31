"""Rebuilds app/core/pricing_data/aws_snapshot.json from AWS's own public bulk
Price List API (https://pricing.us-east-1.amazonaws.com/...) — no AWS account or
credentials needed, these files are static and public.

WHY A SNAPSHOT, NOT A LIVE CALL: AWS's per-region-per-service pricing files run
into the hundreds of MB (the EC2 us-east-1 file alone is ~350MB), with no public,
credential-free endpoint for querying a single SKU's price directly. Re-fetching
that on every cost-estimate request isn't viable — this script does the expensive
part (download + stream-parse the multi-hundred-MB files) once, offline, and
writes out just the handful of specific SKU prices app/core/cost_estimator.py
actually uses. Re-run this periodically (AWS updates prices in-place without
notice) to keep the snapshot current; it's not wired into the request path at all.

Usage:
    pip install ijson httpx   # not app runtime dependencies — see requirements-dev.txt
    python scripts/refresh_aws_pricing.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import ijson

_REGION = "us-east-1"
_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "core" / "pricing_data" / "aws_snapshot.json"


@dataclass(frozen=True)
class _Target:
    category: str
    service_offer_code: str  # AWS's offer code, e.g. "AmazonEC2" — see the index at
    # https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json
    match_attributes: dict[str, str]  # products.{sku}.attributes must match all of these
    product_family: str | None  # products.{sku}.productFamily, when it disambiguates (EC2 needs it)
    sku_description: str


# One SKU per category — matches app.core.cost_estimator._SIZING's default sizing
# tier exactly (1 small general-purpose instance / 1 small burstable DB instance /
# S3 Standard storage / SQS standard-queue requests / smallest Redis cache node).
_TARGETS: list[_Target] = [
    _Target(
        category="compute_vm",
        service_offer_code="AmazonEC2",
        product_family="Compute Instance",
        match_attributes={
            "instanceType": "t3.medium",
            "operatingSystem": "Linux",
            "tenancy": "Shared",
            "preInstalledSw": "NA",
            "capacitystatus": "Used",
            "licenseModel": "No License required",
        },
        sku_description="AWS EC2 t3.medium, Linux, On-Demand, Shared tenancy, us-east-1",
    ),
    _Target(
        category="managed_database",
        service_offer_code="AmazonRDS",
        product_family=None,
        match_attributes={
            "instanceType": "db.t3.medium",
            "databaseEngine": "MySQL",
            "deploymentOption": "Single-AZ",
        },
        sku_description="AWS RDS db.t3.medium, MySQL, Single-AZ, On-Demand, us-east-1",
    ),
    _Target(
        category="object_storage",
        service_offer_code="AmazonS3",
        product_family=None,
        match_attributes={
            "storageClass": "General Purpose",
            "volumeType": "Standard",
            "locationType": "AWS Region",
            "location": "US East (N. Virginia)",
        },
        sku_description="AWS S3 Standard storage, first 50TB/month tier, us-east-1",
    ),
    _Target(
        category="message_queue",
        service_offer_code="AWSQueueService",
        product_family=None,
        match_attributes={"queueType": "Standard", "regionCode": "us-east-1"},
        sku_description="AWS SQS Standard queue requests, Tier1, us-east-1",
    ),
    _Target(
        category="cache",
        service_offer_code="AmazonElastiCache",
        product_family=None,
        match_attributes={
            "instanceType": "cache.t3.micro",
            "cacheEngine": "Redis",
            "locationType": "AWS Region",
        },
        sku_description="AWS ElastiCache cache.t3.micro, Redis, On-Demand, us-east-1",
    ),
]


def _offer_file_url(offer_code: str) -> str:
    return f"https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{offer_code}/current/{_REGION}/index.json"


def _download(url: str, dest: Path) -> None:
    print(f"  downloading {url}", file=sys.stderr)
    with httpx.stream("GET", url, timeout=180.0) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)


def _find_matching_sku(file_path: Path, target: _Target) -> str | None:
    with file_path.open("rb") as f:
        for sku, product in ijson.kvitems(f, "products"):
            if target.product_family is not None and product.get("productFamily") != target.product_family:
                continue
            attrs = product.get("attributes", {})
            if all(attrs.get(k) == v for k, v in target.match_attributes.items()):
                return sku
    return None


def _find_on_demand_unit_price(file_path: Path, sku: str) -> float | None:
    with file_path.open("rb") as f:
        for term_sku, terms in ijson.kvitems(f, "terms.OnDemand"):
            if term_sku != sku:
                continue
            for offer_term in terms.values():
                for dimension in offer_term.get("priceDimensions", {}).values():
                    usd = dimension.get("pricePerUnit", {}).get("USD")
                    if usd is not None:
                        return float(usd)
    return None


def main() -> None:
    prices: dict[str, Any] = {}

    with TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        by_offer_code: dict[str, Path] = {}

        for target in _TARGETS:
            print(f"[{target.category}] service={target.service_offer_code}", file=sys.stderr)
            file_path = by_offer_code.get(target.service_offer_code)
            if file_path is None:
                file_path = tmp_dir / f"{target.service_offer_code}.json"
                _download(_offer_file_url(target.service_offer_code), file_path)
                by_offer_code[target.service_offer_code] = file_path

            sku = _find_matching_sku(file_path, target)
            if sku is None:
                print(f"  WARNING: no matching SKU found for {target.category} — leaving unset", file=sys.stderr)
                continue

            unit_price = _find_on_demand_unit_price(file_path, sku)
            if unit_price is None:
                print(f"  WARNING: no OnDemand price found for sku={sku} ({target.category}) — leaving unset", file=sys.stderr)
                continue

            print(f"  {target.category}: sku={sku} unit_price_usd={unit_price}", file=sys.stderr)
            prices[target.category] = {
                "unit_price_usd": unit_price,
                "sku_description": target.sku_description,
                "aws_sku": sku,
            }

    snapshot = {
        "fetched_at": datetime.now(UTC).date().isoformat(),
        "region": _REGION,
        "prices": prices,
    }
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUTPUT_PATH} with {len(prices)}/{len(_TARGETS)} categories priced", file=sys.stderr)


if __name__ == "__main__":
    main()

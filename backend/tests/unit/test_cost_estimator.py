"""Exercises cost_estimator against httpx.MockTransport (Azure) and a monkeypatched
snapshot path (AWS) — no real network, matching the pattern already established for
RestCatalogProvider (see test_rest_catalog_provider.py)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core import cost_estimator
from app.schemas.architecture import ArchitectureModel
from app.schemas.cost import CloudProvider, ServiceCategory
from app.schemas.migration_plan import ComponentMapping, MigrationPlan, SevenR


@pytest.fixture(autouse=True)
def _reset_module_caches():
    cost_estimator.reset_caches()
    yield
    cost_estimator.reset_caches()


def _model() -> ArchitectureModel:
    return ArchitectureModel()


def _plan(mappings: list[ComponentMapping]) -> MigrationPlan:
    return MigrationPlan(target_architecture_description="x", component_mappings=mappings)


def _mapping(component_id: str, provider: CloudProvider, category: ServiceCategory, disposition=SevenR.REHOST):
    return ComponentMapping(
        component_id=component_id,
        target_description="whatever the plan said",
        disposition=disposition,
        target_cloud_provider=provider,
        target_service_category=category,
    )


def _write_aws_snapshot(tmp_path, monkeypatch, prices: dict) -> None:
    snapshot_path = tmp_path / "aws_snapshot.json"
    snapshot_path.write_text(json.dumps({"fetched_at": "2026-01-01", "region": "us-east-1", "prices": prices}))
    monkeypatch.setattr(cost_estimator, "_AWS_SNAPSHOT_PATH", snapshot_path)


@pytest.mark.asyncio
async def test_aws_component_priced_from_snapshot(tmp_path, monkeypatch):
    _write_aws_snapshot(
        tmp_path,
        monkeypatch,
        {"compute_vm": {"unit_price_usd": 0.0416, "sku_description": "AWS EC2 t3.medium", "aws_sku": "X"}},
    )

    plan = _plan([_mapping("api", CloudProvider.AWS, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan)

    assert summary.unestimated_component_ids == []
    assert len(summary.estimates) == 1
    estimate = summary.estimates[0]
    assert estimate.monthly_usd == pytest.approx(0.0416 * 730, abs=0.01)  # estimates are rounded to cents
    assert estimate.pricing_source.startswith("AWS Price List API")
    assert summary.total_monthly_usd == estimate.monthly_usd


@pytest.mark.asyncio
async def test_aws_snapshot_missing_category_is_unestimated_not_zero(tmp_path, monkeypatch):
    _write_aws_snapshot(tmp_path, monkeypatch, {})  # nothing priced

    plan = _plan([_mapping("api", CloudProvider.AWS, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan)

    assert summary.unestimated_component_ids == ["api"]
    assert summary.estimates[0].monthly_usd is None
    assert summary.estimates[0].note is not None
    assert summary.total_monthly_usd == 0.0


@pytest.mark.asyncio
async def test_azure_component_priced_via_live_lookup():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "prices.azure.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "Items": [
                    {
                        "skuName": "D2s v3",
                        "productName": "Virtual Machines DSv3 Series",
                        "unitPrice": 0.096,
                        "unitOfMeasure": "1 Hour",
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plan = _plan([_mapping("api", CloudProvider.AZURE, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.estimates[0].monthly_usd == pytest.approx(0.096 * 730, rel=1e-6)
    assert summary.estimates[0].pricing_source == "Azure Retail Prices API (live)"
    await client.aclose()


@pytest.mark.asyncio
async def test_azure_message_queue_normalizes_per_million_to_per_message():
    """Service Bus's raw meter is priced per 1,000,000 operations — the default
    sizing tier is 1,000,000 messages/month, so the normalized per-message price
    times that quantity should reproduce the raw per-million price directly."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    {"skuName": "Basic", "meterName": "Basic Messaging Operations", "unitPrice": 0.05, "unitOfMeasure": "1M"}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plan = _plan([_mapping("queue", CloudProvider.AZURE, ServiceCategory.MESSAGE_QUEUE)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.estimates[0].monthly_usd == pytest.approx(0.05, rel=1e-6)
    await client.aclose()


@pytest.mark.asyncio
async def test_azure_lookup_result_is_cached_across_components_in_one_call():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200, json={"Items": [{"skuName": "D2s v3", "productName": "x", "unitPrice": 0.096, "unitOfMeasure": "1 Hour"}]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plan = _plan(
        [
            _mapping("api1", CloudProvider.AZURE, ServiceCategory.COMPUTE_VM),
            _mapping("api2", CloudProvider.AZURE, ServiceCategory.COMPUTE_VM),
        ]
    )
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert call_count == 1  # second component's lookup served from cache
    assert len(summary.estimates) == 2
    assert all(e.monthly_usd == summary.estimates[0].monthly_usd for e in summary.estimates)
    await client.aclose()


@pytest.mark.asyncio
async def test_azure_lookup_with_no_matching_sku_is_unestimated():
    def handler(request: httpx.Request) -> httpx.Response:
        item = {"skuName": "totally-different", "unitPrice": 1.0, "unitOfMeasure": "1 Hour"}
        return httpx.Response(200, json={"Items": [item]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plan = _plan([_mapping("api", CloudProvider.AZURE, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.unestimated_component_ids == ["api"]
    await client.aclose()


@pytest.mark.asyncio
async def test_azure_http_error_is_unestimated_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plan = _plan([_mapping("api", CloudProvider.AZURE, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.unestimated_component_ids == ["api"]
    await client.aclose()


@pytest.mark.asyncio
async def test_gcp_without_api_key_is_unestimated_with_clear_note(monkeypatch):
    from app import config

    config.get_settings.cache_clear()
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    plan = _plan([_mapping("svc", CloudProvider.GCP, ServiceCategory.COMPUTE_VM)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.unestimated_component_ids == ["svc"]
    assert "GCP_BILLING_API_KEY" in summary.estimates[0].note
    await client.aclose()
    config.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_retired_component_is_excluded_entirely_not_priced_at_zero():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Items": []})))
    plan = _plan([_mapping("legacy", CloudProvider.AWS, ServiceCategory.COMPUTE_VM, disposition=SevenR.RETIRE)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.estimates == []
    assert summary.unestimated_component_ids == []
    assert summary.total_monthly_usd == 0.0
    await client.aclose()


@pytest.mark.asyncio
async def test_on_prem_provider_is_unestimated_with_on_prem_note():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Items": []})))
    plan = _plan([_mapping("mainframe", CloudProvider.ON_PREM, ServiceCategory.COMPUTE_VM, disposition=SevenR.RETAIN)])
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.unestimated_component_ids == ["mainframe"]
    assert "on-prem" in summary.estimates[0].note
    await client.aclose()


def test_monthly_cost_hourly_unit_kind():
    assert cost_estimator._monthly_cost(0.10, ServiceCategory.COMPUTE_VM) == pytest.approx(0.10 * 730)


def test_monthly_cost_per_gb_month_unit_kind():
    assert cost_estimator._monthly_cost(0.023, ServiceCategory.OBJECT_STORAGE) == pytest.approx(0.023 * 100)


def test_monthly_cost_per_request_unit_kind():
    assert cost_estimator._monthly_cost(0.0000004, ServiceCategory.MESSAGE_QUEUE) == pytest.approx(0.0000004 * 1_000_000)


@pytest.mark.asyncio
async def test_total_monthly_usd_sums_only_priced_components(tmp_path, monkeypatch):
    _write_aws_snapshot(
        tmp_path, monkeypatch, {"compute_vm": {"unit_price_usd": 0.05, "sku_description": "x", "aws_sku": "Y"}}
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"Items": []})))
    plan = _plan(
        [
            _mapping("priced", CloudProvider.AWS, ServiceCategory.COMPUTE_VM),
            _mapping("unpriced", CloudProvider.GCP, ServiceCategory.COMPUTE_VM),
        ]
    )
    summary = await cost_estimator.estimate_plan_cost(_model(), plan, http_client=client)

    assert summary.total_monthly_usd == pytest.approx(0.05 * 730)
    assert summary.unestimated_component_ids == ["unpriced"]
    await client.aclose()

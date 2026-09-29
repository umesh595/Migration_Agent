import asyncio

import pytest

from app.config import get_settings
from app.llm.base import ModelTier, ProviderRequestError, StructuredResponse
from app.llm.gateway import SessionTokenMeter
from app.llm.schemas import ComponentPlanLLMOutput
from app.orchestration.nodes.planning import per_component_planning_node
from app.schemas.architecture import ArchitectureModel, Component, Environment, WorkloadType
from app.schemas.cost import CloudProvider, ServiceCategory
from app.schemas.migration_context import DowntimeTolerance, MigrationContext
from app.schemas.migration_plan import SevenR, ValidationCheck, Wave


def _output(component_id: str) -> ComponentPlanLLMOutput:
    return ComponentPlanLLMOutput(
        component_id=component_id,
        target_description=f"{component_id} on Cloud Run",
        disposition=SevenR.REPLATFORM,
        target_cloud_provider=CloudProvider.GCP,
        target_service_category=ServiceCategory.COMPUTE_SERVERLESS,
        steps=[f"Step {i} for {component_id} using Cloud Run" for i in range(1, 6)],
        validation_checks=[
            ValidationCheck(description="HTTP 200 smoke test must pass", check_type="smoke_test"),
            ValidationCheck(description="Error rate remains below 1%", check_type="load_test"),
        ],
        rollback_notes="Switch traffic back to source service first; keep source online for 24 hours.",
    )


class FakeGateway:
    def __init__(self, failures: dict[str, int] | None = None, delay_s: float = 0.0) -> None:
        self.failures = failures or {}
        self.delay_s = delay_s
        self.calls: list[str] = []
        self.inflight = 0
        self.max_inflight = 0

    async def complete(self, *, tier, system_prompt, user_prompt, response_model, meter, node_name, temperature=0.0):
        assert tier == ModelTier.STRONG
        component_id = node_name.removeprefix("planning.component.")
        self.calls.append(component_id)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            remaining_failures = self.failures.get(component_id, 0)
            if remaining_failures:
                self.failures[component_id] = remaining_failures - 1
                raise ProviderRequestError("timed out after 300s")
            return StructuredResponse(
                parsed=_output(component_id),
                usage=type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})(),
                model="fake",
                attempts=1,
            )
        finally:
            self.inflight -= 1


def _state(component_ids: list[str]):
    model = ArchitectureModel(
        components=[
            Component(id=component_id, name=component_id, workload_type=WorkloadType.API_SERVICE, environment=Environment.CLOUD)
            for component_id in component_ids
        ]
    )
    return {
        "model": model,
        "_waves": [Wave(index=0, component_ids=list(reversed(component_ids)), rationale="same wave")],
        "migration_context": MigrationContext(
            source_environment=Environment.CLOUD,
            target_environment=Environment.CLOUD,
            target_platform_description="GCP",
            downtime_tolerance=DowntimeTolerance.ZERO_DOWNTIME,
        ),
    }


@pytest.mark.asyncio
async def test_component_planning_runs_concurrently_but_returns_model_order(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("PLAN_CONCURRENCY", "2")
    gateway = FakeGateway(delay_s=0.01)

    result = await per_component_planning_node(_state(["a", "b", "c"]), gateway, SessionTokenMeter(100_000))

    assert [output.component_id for output in result["_component_outputs"]] == ["a", "b", "c"]
    assert gateway.max_inflight == 2
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_component_planning_retries_only_failed_components_once():
    gateway = FakeGateway(failures={"b": 1})

    result = await per_component_planning_node(_state(["a", "b", "c"]), gateway, SessionTokenMeter(100_000))

    assert result["error"] is None
    assert gateway.calls.count("a") == 1
    assert gateway.calls.count("b") == 2
    assert gateway.calls.count("c") == 1


@pytest.mark.asyncio
async def test_component_planning_reports_failed_component_reason_after_retry():
    gateway = FakeGateway(failures={"b": 2})

    result = await per_component_planning_node(_state(["a", "b"]), gateway, SessionTokenMeter(100_000))

    assert "b: provider error: timed out after 300s" in result["error"]

"""End-to-end lifecycle through the real HTTP API: discovery turns over SSE, gate 1,
planning + review, gate 2, and export of the 10-deliverable package.

The LLM boundary is a MockProvider (DECISIONS.md), so this asserts OUR pipeline —
persistence, versioning, gate enforcement, audit trail, export — rather than model
behavior. Requires Postgres + Redis; skips cleanly without them.
"""

from __future__ import annotations

import json

import pytest

from app.llm.schemas import (
    ComponentPlanLLMOutput,
    CutoverReviewOutput,
    GeneratedQuestion,
    MigrationContextElicitationOutput,
    QuestionGenerationOutput,
    RollbackPlanOutput,
    SemanticReviewJudgeOutput,
    SemanticReviewOutput,
    TargetArchitectureOutput,
)
from app.schemas.migration_plan import ValidationCheck
from app.schemas.patches import (
    AddComponentPatch,
    AddDependencyPatch,
    PatchSet,
    RemoveDependencyPatch,
)
from tests.integration.conftest import requires_infra

pytestmark = [requires_infra]


async def _read_sse(client, url: str, headers: dict, payload: dict) -> list[dict]:
    """Collects SSE events from a streaming POST."""

    events: list[dict] = []
    async with client.stream("POST", url, headers=headers, json=payload, timeout=60.0) as response:
        assert response.status_code == 200, await response.aread()
        current: dict = {}
        async for line in response.aiter_lines():
            if not line.strip():
                if current:
                    events.append(current)
                    current = {}
                continue
            if line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line.split(":", 1)[1].strip())
    if current:
        events.append(current)
    return events


def _register_discovery(provider) -> None:
    provider.register(
        PatchSet,
        PatchSet(
            patches=[
                AddComponentPatch(id="storefront", name="Storefront", workload_type="web_service", technology="React"),
                AddComponentPatch(id="orders_api", name="Orders API", workload_type="api_service", technology="Django"),
                AddComponentPatch(id="postgres", name="Postgres", workload_type="database", technology="PostgreSQL 14"),
                AddDependencyPatch(source_id="storefront", target_id="postgres", kind="data_read"),
                AddDependencyPatch(source_id="orders_api", target_id="postgres", kind="data_write"),
            ],
            narration="Captured storefront, orders API, and Postgres.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(
            questions=[GeneratedQuestion(text="Which environment does the storefront run in?", related_gap_description="env")],
            narration="A detail or two would help.",
        ),
    )


def _register_correction(provider) -> None:
    provider.register(
        PatchSet,
        PatchSet(
            patches=[
                RemoveDependencyPatch(source_id="storefront", target_id="postgres"),
                AddDependencyPatch(source_id="storefront", target_id="orders_api", kind="sync_call"),
            ],
            narration="Corrected: storefront calls the orders API, not Postgres directly.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(
            questions=[GeneratedQuestion(text="How critical is the orders API?", related_gap_description="criticality")],
            narration="One more.",
        ),
    )


def _register_planning(provider) -> None:
    provider.register(
        MigrationContextElicitationOutput,
        MigrationContextElicitationOutput(
            source_environment="on_prem",
            target_environment="cloud",
            target_platform_description="AWS EKS with managed RDS",
            downtime_tolerance="maintenance_window",
            constraints=["PCI compliance must be maintained"],
        ),
    )
    for cid in ["postgres", "orders_api", "storefront"]:
        provider.register(
            ComponentPlanLLMOutput,
            ComponentPlanLLMOutput(
                component_id=cid,
                target_description=f"{cid} on managed AWS infrastructure",
                disposition="replatform",
                steps=[f"provision {cid} target", f"cut {cid} traffic over"],
                validation_checks=[ValidationCheck(description=f"{cid} smoke test", check_type="smoke_test")],
                rollback_notes=f"revert {cid} DNS and restore source",
                estimated_effort="3-5 days",
            ),
        )
    provider.register(TargetArchitectureOutput, TargetArchitectureOutput(description="Containerized on EKS with RDS."))
    provider.register(
        CutoverReviewOutput,
        CutoverReviewOutput(
            approach="phased-by-wave",
            steps=["cut wave 0", "validate", "continue"],
            go_no_go_criteria=["smoke tests green", "error rate < 0.1% for 30 min"],
            communication_plan="status page per wave",
        ),
    )
    provider.register(
        RollbackPlanOutput,
        RollbackPlanOutput(
            approach="per-wave revert",
            triggers=["error rate > 1%"],
            steps=["revert DNS", "resync data"],
            data_reconciliation_notes="replay CDC log",
        ),
    )
    # Semantic critic finds nothing — keeps the refine loop from running so this test
    # asserts the happy path deterministically. Refine is covered by unit tests.
    provider.register(SemanticReviewOutput, SemanticReviewOutput(findings=[]))
    provider.register(
        SemanticReviewJudgeOutput,
        SemanticReviewJudgeOutput(
            relevance_score=90,
            specificity_score=85,
            actionability_score=88,
            context_awareness_score=92,
            overall_score=90,
            rationale="Correctly found nothing on a clean plan with no genuine judgment-level issues.",
            flagged_issues=[],
        ),
    )


@pytest.mark.asyncio
async def test_full_lifecycle_discovery_to_export(app_client, auth_headers):
    client, provider = app_client

    # --- create session ---
    response = await client.post("/sessions", headers=auth_headers, json={"name": "lifecycle test"})
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]

    # --- discovery turn 1 ---
    _register_discovery(provider)
    events = await _read_sse(client, f"/sessions/{session_id}/messages", auth_headers,
                             {"message": "We have a storefront, an orders API, and Postgres."})
    assert any(e.get("event") == "turn_complete" for e in events)

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert {c["id"] for c in state["model"]["components"]} == {"storefront", "orders_api", "postgres"}

    # --- discovery turn 2: a correction must actually remove the wrong edge ---
    _register_correction(provider)
    await _read_sse(client, f"/sessions/{session_id}/messages", auth_headers,
                    {"message": "The storefront goes through the orders API, not Postgres directly."})

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    edges = {(d["source_id"], d["target_id"]) for d in state["model"]["dependencies"]}
    assert ("storefront", "postgres") not in edges
    assert ("storefront", "orders_api") in edges

    # --- audit trail records every patch, applied or rejected ---
    audit = (await client.get(f"/sessions/{session_id}/audit", headers=auth_headers)).json()
    assert len(audit["records"]) >= 7  # 5 from turn 1, 2 from turn 2
    assert all(r["outcome"] in ("applied", "rejected") for r in audit["records"])

    # --- GATE 1 ---
    response = await client.post(f"/sessions/{session_id}/model/accept", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["session_status"] == "planning"

    # accepting twice must fail — the gate is not re-enterable
    assert (await client.post(f"/sessions/{session_id}/model/accept", headers=auth_headers)).status_code == 409

    # --- planning + review in one run ---
    _register_planning(provider)
    planning_events = await _read_sse(client, f"/sessions/{session_id}/messages", auth_headers,
                    {"message": "Move everything to AWS, we can take a maintenance window."})

    # The turn_complete narration must describe the plan that was just generated,
    # never stale discovery-stage text left over in the shared checkpointed thread
    # from before Gate 1 (a real bug: discovery and planning share one LangGraph
    # thread_id, so `narration`/`pending_questions` persist across the gate unless
    # explicitly reset and re-synthesized for the planning turn).
    turn_complete = next(e for e in planning_events if e.get("event") == "turn_complete")
    planning_narration = turn_complete["data"]["narration"]
    assert planning_narration is not None
    assert "Migration plan generated" in planning_narration
    assert "storefront" not in planning_narration.lower()  # not the discovery-turn narration bleeding through
    assert turn_complete["data"]["questions"] == []

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    plan = state["plan"]
    assert plan is not None, "planning run produced no plan"

    # Sequencing came from the graph: postgres has no outgoing deps so it moves first.
    wave_of = {cid: w["index"] for w in plan["waves"] for cid in w["component_ids"]}
    assert wave_of["postgres"] < wave_of["orders_api"] < wave_of["storefront"]

    # All 10 deliverables have typed content, including the two that had no schema
    # home in the original design (validation_summary, roadmap_items).
    assert plan["target_architecture_description"]
    assert len(plan["component_mappings"]) == 3
    assert len(plan["component_plans"]) == 3
    assert plan["cutover_strategy"]["go_no_go_criteria"]
    assert plan["rollback_strategy"]["steps"]
    assert plan["validation_summary"]["overall_strategy"]
    assert len(plan["roadmap_items"]) == 3

    # --- review quality: the judge scored the (empty, correctly-empty) critique ---
    review_quality = (await client.get(f"/sessions/{session_id}/review-quality", headers=auth_headers)).json()
    assert len(review_quality["scores"]) == 1
    assert review_quality["scores"][0]["overall_score"] == 90
    assert review_quality["scores"][0]["evaluated_finding_count"] == 0

    # --- GATE 2 ---
    response = await client.post(f"/sessions/{session_id}/plan/approve", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["session_status"] == "exported"

    # --- export both formats ---
    md = await client.get(f"/sessions/{session_id}/export?format=markdown", headers=auth_headers)
    assert md.status_code == 200
    body = md.text
    for heading in ["Current Architecture", "Target Architecture", "Component Mapping",
                    "Component Migration Approach", "Migration Sequence", "Risks & Assumptions",
                    "Validation Approach", "Cutover Strategy", "Rollback Strategy", "Migration Roadmap"]:
        assert heading in body, f"export missing deliverable section: {heading}"
    assert "```mermaid" in body

    docx = await client.get(f"/sessions/{session_id}/export?format=docx", headers=auth_headers)
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_rejected_patch_is_audited_and_narrated_not_silently_dropped(app_client, auth_headers):
    """A patch referencing a nonexistent component must be rejected, recorded, and
    leave the model untouched (Doc 3 §3.2 failure branch)."""

    client, provider = app_client

    session_id = (await client.post("/sessions", headers=auth_headers, json={"name": "reject test"})).json()["id"]

    provider.register(
        PatchSet,
        PatchSet(
            patches=[
                AddComponentPatch(id="real_service", name="Real", workload_type="api_service"),
                AddDependencyPatch(source_id="real_service", target_id="ghost_db", kind="data_read"),
            ],
            narration="Added the service and its database link.",
        ),
    )
    provider.register(
        QuestionGenerationOutput,
        QuestionGenerationOutput(
            questions=[GeneratedQuestion(text="Anything else?", related_gap_description="g")], narration="n"
        ),
    )

    await _read_sse(client, f"/sessions/{session_id}/messages", auth_headers,
                    {"message": "There's a real service that reads from a database."})

    state = (await client.get(f"/sessions/{session_id}/state", headers=auth_headers)).json()
    assert {c["id"] for c in state["model"]["components"]} == {"real_service"}
    assert state["model"]["dependencies"] == []

    audit = (await client.get(f"/sessions/{session_id}/audit", headers=auth_headers)).json()
    rejected = [r for r in audit["records"] if r["outcome"] == "rejected"]
    assert len(rejected) == 1
    assert "ghost_db" in rejected[0]["reason"]

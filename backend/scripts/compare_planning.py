"""Compare model routes for slow/failing planning and discovery nodes.

Usage:
  python scripts/compare_planning.py --session-id <uuid>

Requires the normal backend .env. The script reads the accepted model and
migration context from the saved session, runs selected component-planning calls
under three routing configs, then replays small discovery transcripts through
ingest + question generation on flash vs pro. It prints elapsed seconds, token
usage, and a simple pass/fail rubric. It does not persist anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
import time
import uuid
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.codevector_provider import CodeVectorProvider
from app.llm.providers.fallback_provider import FallbackLLMProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.schemas import ComponentPlanLLMOutput
from app.orchestration.nodes.discovery import apply_patches_node, gap_analysis_node, generate_questions_node, ingest_node
from app.orchestration.nodes.planning import per_component_planning_node
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel
from app.schemas.migration_plan import Wave
from app.services import session_service


COMPONENT_IDS = ["payment_gateway", "user_inventory"]

DISCOVERY_TRANSCRIPTS = {
    "movie_booking": "We run a movie booking platform with a web app, booking API, seat inventory, payments, and email confirmations.",
    "order_management": "Our order management system has order APIs, inventory checks, warehouse fulfillment, billing, and customer notifications.",
    "retail_azure_to_gcp": (
        "Our online retail ecosystem is on Azure across Product Catalog, User Inventory, Order Processing, "
        "Logistics & Shipping, Loyalty Rewards, and Payment Gateway. Event Hubs and Service Bus carry async "
        "messages. Order Processing needs real-time Inventory and Payment checks. Move to GCP, but Payment "
        "Gateway remains on Azure for 6 months."
    ),
}


def build_gateway(*, plan_thinking: str, node_routes: dict[str, dict[str, str]] | None = None) -> LLMGateway:
    settings = get_settings()
    primary = CodeVectorProvider(
        api_key=settings.active_codevector_api_key.get_secret_value(),
        base_url=settings.active_codevector_base_url,
        cheap_model=settings.active_codevector_cheap_model,
        strong_model=settings.active_codevector_strong_model,
        timeout_s=settings.llm_request_timeout_s,
        response_format=settings.codevector_response_format,
        fallback_model=settings.codevector_fallback_model,
    )
    fallback = (
        GeminiProvider(
            api_key=settings.google_ai_studio_api_key.get_secret_value(),
            cheap_model=settings.google_ai_studio_cheap_model,
            strong_model=settings.google_ai_studio_strong_model,
            timeout_s=settings.llm_request_timeout_s,
        )
        if settings.google_ai_studio_api_key
        else None
    )
    return LLMGateway(
        FallbackLLMProvider(primary, fallback),
        cheap_tier_max_retries=settings.llm_cheap_tier_max_retries,
        strong_tier_max_retries=settings.llm_strong_tier_max_retries,
        call_timeout_s=settings.llm_call_timeout_s,
        critic_timeout_s=settings.llm_critic_timeout_s,
        plan_timeout_s=settings.llm_plan_timeout_s,
        plan_thinking_effort=plan_thinking,
        node_routes_json=json.dumps(node_routes or {}),
    )


def rubric(plan: ComponentPlanLLMOutput) -> dict[str, bool]:
    step_text = " ".join(plan.steps).lower()
    validations = [v.description.lower() for v in plan.validation_checks]
    rollback = plan.rollback_notes.lower()
    return {
        "concrete_target_service": bool(plan.target_description and plan.target_cloud_provider != "unknown"),
        "five_ordered_technology_steps": len(plan.steps) >= 5 and any(word in step_text for word in ("cloud", "gcp", "azure", "run", "sql", "pub/sub", "pubsub")),
        "two_validation_checks_with_criteria": len(validations) >= 2 and any(word in " ".join(validations) for word in ("must", "below", "within", "%", "pass", "fail")),
        "rollback_order_and_duration": any(word in rollback for word in ("first", "then", "order", "before")) and any(word in rollback for word in ("hour", "day", "window", "duration")),
    }


async def run_component_variant(label: str, gateway: LLMGateway, model: ArchitectureModel, migration_context) -> None:
    selected = [c for c in model.components if c.id in COMPONENT_IDS]
    selected_model = model.model_copy(update={"components": selected})
    state = {
        "model": selected_model,
        "migration_context": migration_context,
        "_waves": [Wave(index=0, component_ids=[c.id for c in selected], rationale="comparison run")],
    }
    meter = SessionTokenMeter(1_000_000)
    started = time.perf_counter()
    result = await per_component_planning_node(state, gateway, meter)
    elapsed = time.perf_counter() - started
    print(f"\n[{label}] component planning elapsed={elapsed:.2f}s tokens={meter.spent} error={result.get('error')}")
    for output in result.get("_component_outputs", []):
        checks = rubric(output)
        print(f"  {output.component_id}: {'PASS' if all(checks.values()) else 'FAIL'} {checks}")


async def run_discovery_variant(label: str, route_model: str) -> None:
    route = {"discovery.generate_questions": {"model": route_model, "thinking": "off"}}
    gateway = build_gateway(plan_thinking="off", node_routes=route)
    for name, message in DISCOVERY_TRANSCRIPTS.items():
        meter = SessionTokenMeter(1_000_000)
        state = {
            "session_id": f"compare-{name}-{label}",
            "stage": Stage.DISCOVERY,
            "model": ArchitectureModel(),
            "user_message": message,
            "conversation_context": message,
            "previous_agent_message": None,
        }
        started = time.perf_counter()
        ingest = await ingest_node(state, gateway, meter)
        state.update(ingest)
        applied = apply_patches_node(state)
        state.update(applied)
        gaps = await gap_analysis_node(state, gateway, meter)
        state.update(gaps)
        questions = await generate_questions_node(state, gateway, meter)
        elapsed = time.perf_counter() - started
        model = state["model"]
        print(
            f"[{label}] {name}: elapsed={elapsed:.2f}s tokens={meter.spent} "
            f"components={len(model.components)} dependencies={len(model.dependencies)} "
            f"questions={len(questions.get('pending_questions', []))}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    session_id = uuid.UUID(args.session_id)

    async with AsyncSessionLocal() as db:
        model = await session_service.accepted_model(db, session_id)
        migration_context = await session_service.get_migration_context(db, session_id)
    if migration_context is None:
        raise SystemExit("session has no migration context yet; run planning context intake first")

    await run_component_variant("current/pro-default", build_gateway(plan_thinking="high"), model, migration_context)
    await run_component_variant("pro-low", build_gateway(plan_thinking="low"), model, migration_context)
    await run_component_variant(
        "flash-off",
        build_gateway(plan_thinking="off", node_routes={"planning.component.*": {"model": "flash", "thinking": "off"}}),
        model,
        migration_context,
    )

    print("\nDiscovery variants:")
    await run_discovery_variant("flash-off", "flash")
    await run_discovery_variant("pro-off", "pro")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())

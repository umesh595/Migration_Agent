"""Golden-fixture eval: a scripted discovery transcript must reproduce an exact
model, and that model must produce an exact wave sequence and a clean rules pass.

Red blocks merge (technique #16). This suite is deterministic by construction — the
LLM boundary is a MockProvider, so a failure here always means our logic changed,
never that a model phrased something differently (DECISIONS.md)."""

import pytest

from app.core.graph_engine import compute_sequence
from app.core.plan_assembler import assemble_plan
from app.core.review_rules_engine import run_rules
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.llm.schemas import QuestionGenerationOutput
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel
from app.schemas.findings import FindingSeverity
from app.schemas.patches import PatchSet
from tests.eval.fixtures import ecommerce_platform as fx


async def _run_scripted_discovery() -> ArchitectureModel:
    provider = MockProvider()
    for patch_set in fx.SCRIPTED_PATCH_SETS:
        provider.register(PatchSet, patch_set)
    for questions in fx.SCRIPTED_QUESTIONS:
        provider.register(QuestionGenerationOutput, questions)

    gateway = LLMGateway(provider)
    meter = SessionTokenMeter(1_000_000)
    graph = build_discovery_graph(gateway, meter).compile()

    model = ArchitectureModel()
    for turn in fx.TURNS:
        result = await graph.ainvoke(
            {"session_id": "golden", "stage": Stage.DISCOVERY, "model": model, "user_message": turn}
        )
        model = result["model"]
    return model


@pytest.mark.asyncio
async def test_scripted_transcript_reproduces_golden_model():
    model = await _run_scripted_discovery()

    assert model.component_ids() == fx.EXPECTED_COMPONENT_IDS
    actual_deps = {(d.source_id, d.target_id) for d in model.dependencies}
    assert actual_deps == fx.EXPECTED_DEPENDENCIES


@pytest.mark.asyncio
async def test_golden_model_produces_expected_wave_order():
    model = await _run_scripted_discovery()
    waves = compute_sequence(model)

    assert [w.component_ids for w in waves] == fx.EXPECTED_WAVE_ORDER


@pytest.mark.asyncio
async def test_assembled_plan_passes_all_rules_cleanly():
    model = await _run_scripted_discovery()
    waves = compute_sequence(model)
    plan = assemble_plan(
        model,
        waves,
        fx.component_plan_outputs(),
        target_architecture_description=fx.TARGET_ARCHITECTURE.description,
        cutover=fx.CUTOVER,
        rollback=fx.ROLLBACK,
    )

    findings = run_rules(model, plan)
    errors = [f for f in findings if f.severity == FindingSeverity.ERROR]
    assert errors == [], f"golden plan should have zero rule errors, got: {[f.message for f in errors]}"


@pytest.mark.asyncio
async def test_correction_turn_actually_removed_the_wrong_dependency():
    """Turn 2 corrects turn 1. The wrong edge must be GONE, not merely superseded —
    this is the anti-hallucination path from Doc 3 §3.2."""

    model = await _run_scripted_discovery()
    assert ("storefront", "postgres") not in {(d.source_id, d.target_id) for d in model.dependencies}

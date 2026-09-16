import pytest

from app.llm.base import ProviderRequestError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.providers.openai_provider import MockProvider
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.schemas.architecture import ArchitectureModel
from app.schemas.patches import PatchSet


@pytest.mark.asyncio
async def test_failed_extraction_stops_before_more_model_calls_and_preserves_model():
    provider = MockProvider()
    provider.register(PatchSet, ProviderRequestError("upstream timeout"))
    graph = build_discovery_graph(LLMGateway(provider), SessionTokenMeter(100_000)).compile()
    model = ArchitectureModel()
    result = await graph.ainvoke({
        "session_id": "timeout-test", "stage": Stage.DISCOVERY, "model": model,
        "user_message": "The database is PostgreSQL and the current hosting is on premises.",
        "pending_questions": ["Where is it hosted?"],
    })
    assert result["error"]
    assert result["model"] == model
    assert result["pending_questions"] == []
    assert result["last_patch_results"] == []
    assert len(provider.calls) == 1

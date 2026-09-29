import asyncio

import pytest

from app.api.routers import sessions


async def _quiet_then_node():
    await asyncio.sleep(0.05)
    yield {"slow_node": {"narration": None}}


@pytest.mark.asyncio
async def test_astream_with_reasoning_emits_heartbeat_while_graph_is_quiet(monkeypatch):
    monkeypatch.setattr(sessions, "STREAM_HEARTBEAT_S", 0.01)

    stream = sessions._astream_with_reasoning(_quiet_then_node(), asyncio.Queue())

    assert await anext(stream) == ("heartbeat", None, None)
    for _ in range(10):
        event = await anext(stream)
        if event[0] == "node":
            assert event == ("node", {"slow_node": {"narration": None}}, None)
            break
    else:
        pytest.fail("stream never yielded the completed graph node")

    await stream.aclose()

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers import sessions
from app.db.models import SessionStatus
from app.llm.base import TokenBudgetExceededError


class RecordingLock:
    def __init__(self) -> None:
        self.released: list[tuple[str, str]] = []

    async def acquire(self, session_id: str) -> str:
        return "lock-token"

    async def release(self, session_id: str, token: str) -> None:
        self.released.append((session_id, token))


class FakeDb:
    async def rollback(self) -> None:
        pass


async def _consume_sse(response) -> list[dict]:
    events = []
    async for event in response.body_iterator:
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_post_message_prechecks_token_budget_and_releases_lock(monkeypatch):
    session_id = uuid.uuid4()
    fake_session = SimpleNamespace(
        id=session_id,
        status=SessionStatus.DISCOVERY,
        token_usage=5,
        langgraph_thread_id="thread-1",
    )
    lock = RecordingLock()
    released_claims = []

    async def get_session_for_user(db, requested_session_id, user_id):
        assert requested_session_id == session_id
        return fake_session

    async def claim_message(db, requested_session_id, message_id):
        return True

    async def save_conversation_turn(*args):
        return None

    async def release_message_claim(db, requested_session_id, message_id):
        released_claims.append((requested_session_id, message_id))

    monkeypatch.setattr(sessions.session_service, "get_session_for_user", get_session_for_user)
    monkeypatch.setattr(sessions.session_service, "claim_message", claim_message)
    monkeypatch.setattr(sessions.session_service, "save_conversation_turn", save_conversation_turn)
    monkeypatch.setattr(sessions.session_service, "release_message_claim", release_message_claim)
    monkeypatch.setattr(sessions, "get_settings", lambda: SimpleNamespace(session_token_budget=5))

    with pytest.raises(HTTPException) as exc_info:
        await sessions.post_message(
            session_id,
            sessions.MessageRequest(message="hello", message_id="budget-test"),
            SimpleNamespace(id=uuid.uuid4()),
            FakeDb(),
            SimpleNamespace(),
            lock,
        )

    assert exc_info.value.status_code == 409
    assert "session token budget of 5 exhausted" in exc_info.value.detail
    assert lock.released == [(str(session_id), "lock-token")]
    assert released_claims == [(session_id, "budget-test")]


@pytest.mark.asyncio
async def test_post_message_releases_lock_after_graph_token_budget_exception(monkeypatch):
    session_id = uuid.uuid4()
    fake_session = SimpleNamespace(
        id=session_id,
        status=SessionStatus.DISCOVERY,
        token_usage=0,
        langgraph_thread_id="thread-2",
    )
    lock = RecordingLock()
    saved_turns = []
    released_claims = []

    async def get_session_for_user(db, requested_session_id, user_id):
        return fake_session

    async def claim_message(db, requested_session_id, message_id):
        return True

    async def save_conversation_turn(db, requested_session_id, role, text):
        saved_turns.append((requested_session_id, role, text))

    async def release_message_claim(db, requested_session_id, message_id):
        released_claims.append((requested_session_id, message_id))

    async def latest_model(db, requested_session_id):
        return SimpleNamespace(version=1)

    async def list_conversation_turns(db, requested_session_id):
        return []

    async def latest_conversation_turn(db, requested_session_id, role):
        return None

    class FailingCompiledGraph:
        async def astream(self, initial, config, stream_mode):
            raise TokenBudgetExceededError("friendly budget message")
            yield {}

    class FailingGraphBuilder:
        def compile(self, checkpointer=None):
            return FailingCompiledGraph()

    monkeypatch.setattr(sessions.session_service, "get_session_for_user", get_session_for_user)
    monkeypatch.setattr(sessions.session_service, "claim_message", claim_message)
    monkeypatch.setattr(sessions.session_service, "save_conversation_turn", save_conversation_turn)
    monkeypatch.setattr(sessions.session_service, "release_message_claim", release_message_claim)
    monkeypatch.setattr(sessions.session_service, "latest_model", latest_model)
    monkeypatch.setattr(sessions.session_service, "list_conversation_turns", list_conversation_turns)
    monkeypatch.setattr(sessions.session_service, "latest_conversation_turn", latest_conversation_turn)
    monkeypatch.setattr(sessions, "get_settings", lambda: SimpleNamespace(session_token_budget=100))
    monkeypatch.setattr(sessions, "get_checkpointer", lambda: SimpleNamespace())
    monkeypatch.setattr(sessions, "build_discovery_graph", lambda gateway, meter: FailingGraphBuilder())

    response = await sessions.post_message(
        session_id,
        sessions.MessageRequest(message="hello", message_id="graph-budget-test"),
        SimpleNamespace(id=uuid.uuid4()),
        FakeDb(),
        SimpleNamespace(),
        lock,
    )

    events = await _consume_sse(response)

    assert any(event["event"] == "error" and "friendly budget message" in event["data"] for event in events)
    assert lock.released == [(str(session_id), "lock-token")]
    assert released_claims == [(session_id, "graph-budget-test")]
    assert (session_id, "error", "friendly budget message") in saved_turns

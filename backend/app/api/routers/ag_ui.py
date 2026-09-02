"""AG-UI protocol endpoint for the discovery loop — additive alongside
POST /sessions/{id}/messages (app/api/routers/sessions.py), not a
replacement. The two serve the same discovery graph and the same session/
model/conversation persistence; they differ only in transport:

- POST /sessions/{id}/messages: the original hand-rolled SSE format
  (node_complete/turn_complete), and a structural/high-impact patch that
  needs human confirmation is auto-declined transparently (see that
  endpoint's `interrupted` handling) because that UI has no approve/reject
  control to show.
- POST /sessions/ag-ui (this file): the standard AG-UI event schema a
  CopilotKit frontend understands, INCLUDING real LangGraph interrupt()
  pauses — a structural/high-impact patch pauses this run for a genuine
  approve/edit/reject decision instead of being silently declined.

Scope: DISCOVERY stage only for now. PLANNING/REVIEW support is a
same-pattern follow-up, not built here (see the AG-UI adoption plan) —
this endpoint 409s for a session in any other stage, pointing at the
existing endpoint.

Per-session agent caching: `add_langgraph_fastapi_endpoint` (the standard
ag-ui-langgraph helper) registers ONE agent for the app's whole lifetime,
sharing one compiled graph — and therefore one SessionTokenMeter — across
every session. That's wrong here: build_discovery_graph binds gateway/meter
into its nodes via functools.partial at graph-construction time (see
app/orchestration/graph.py), so each SESSION needs its own meter for the
per-session token budget to mean anything. This module builds and caches
one LangGraphAGUIAgent per session id instead, each wrapping its own
freshly-built graph + meter, sharing only the process-wide gateway and
Postgres checkpointer. The cache is in-process and unbounded for now — a
known, acceptable limitation for this phase, not a correctness bug (a
restart clears it; the checkpointer itself is where real state lives)."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from ag_ui.core import RunAgentInput, UserMessage
from ag_ui_langgraph.endpoint import EventEncoder
from copilotkit import LangGraphAGUIAgent
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, Db, SessionLock, enforce_message_rate_limit, get_gateway

# Reused rather than re-derived — see their own docstrings/comments in
# sessions.py for why each exists.
from app.api.routers.sessions import _persist_turn, _render_user_message_history, _thread_config
from app.config import get_settings
from app.core.request_intelligence import classify_user_request
from app.db.models import SessionStatus
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.orchestration.checkpointer import get_checkpointer
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.security.session_lock import SessionBusyError
from app.services import session_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ag-ui"])

# session_id (str) -> (cached agent, its meter). The meter is kept alongside
# rather than pulled back out of the compiled graph's node closures afterward
# (fragile — depends on functools.partial's internal shape) since we already
# have the object right here when the agent is built.
_agent_cache: dict[str, tuple[LangGraphAGUIAgent, SessionTokenMeter]] = {}


def _get_or_build_agent(
    session_id: str, gateway: LLMGateway, budget: int, already_spent: int
) -> tuple[LangGraphAGUIAgent, SessionTokenMeter]:
    cached = _agent_cache.get(session_id)
    if cached is not None:
        return cached

    meter = SessionTokenMeter(budget, already_spent=already_spent)
    graph = build_discovery_graph(gateway, meter).compile(checkpointer=get_checkpointer())
    agent = LangGraphAGUIAgent(name="discovery", graph=graph, description="Architecture discovery agent")
    _agent_cache[session_id] = (agent, meter)
    return agent, meter


@router.post("/sessions/ag-ui")
async def ag_ui_discovery_endpoint(
    input_data: RunAgentInput,
    request: Request,
    user: CurrentUser,
    db: Db,
    session_lock: SessionLock,
    gateway: Annotated[LLMGateway, Depends(get_gateway)],
    _rate_limit: Annotated[None, Depends(enforce_message_rate_limit)],
) -> StreamingResponse:
    try:
        session_id = uuid.UUID(input_data.thread_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="thread_id must be a session UUID") from None

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"session is in '{session.status}' — the AG-UI endpoint currently only serves discovery; "
                "use POST /sessions/{id}/messages for planning/review turns"
            ),
        )

    session_id_str = str(session.id)
    is_resume = bool(input_data.resume)

    if not is_resume:
        # A fresh turn: the latest UserMessage in the payload is what the user
        # just typed. A resume carries no new user text — input_data.resume is
        # the approve/edit/reject decision, not a message.
        user_text = next(
            (m.content for m in reversed(input_data.messages) if isinstance(m, UserMessage) and m.content),
            None,
        )
        if not user_text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no user message in this run")

        try:
            lock_token = await session_lock.acquire(session_id_str)
        except SessionBusyError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="another turn is already in progress for this session — wait for it to complete",
            ) from None

        await session_service.save_conversation_turn(db, session.id, "user", user_text)
    else:
        # Resuming a paused run: the lock was released when the first request's
        # stream ended at the interrupt (see below) — re-acquire it now so a
        # second concurrent decision can't race this one.
        try:
            lock_token = await session_lock.acquire(session_id_str)
        except SessionBusyError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="another turn is already in progress for this session — wait for it to complete",
            ) from None

    model_before = await session_service.latest_model(db, session.id)
    settings = get_settings()
    agent, meter = _get_or_build_agent(
        session_id_str, gateway, settings.session_token_budget, session.token_usage or 0
    )
    # The LangGraph checkpoint key is session.langgraph_thread_id, NOT session.id
    # (see sessions.py's identical convention and reset_langgraph_thread's
    # docstring): a separate, rotatable id specifically so a corrupted/
    # unreadable checkpoint row can be abandoned via reset_langgraph_thread
    # without touching the session's own identity or its real persisted state
    # (ModelVersion/PatchAuditRecord etc.). AG-UI's own thread_id concept maps
    # to "this conversation" from the frontend's point of view (naturally the
    # session id) — overridden here to the internal rotatable id so
    # ag-ui-langgraph's own checkpoint access (inside request_agent.run) and
    # this endpoint's own aget_state() call below agree on the same thread.
    input_data.thread_id = session.langgraph_thread_id
    thread_config = _thread_config(session.langgraph_thread_id)

    if not is_resume:
        # RunAgentInput.state is ag-ui-langgraph's actual seed for the wrapped
        # graph's own state dict (see prepare_stream's `state_input = input.state
        # or {}`) — confirmed directly against our custom GraphState schema
        # before wiring this in; forwarded_props is a different, unrelated
        # channel (extra out-of-band context, not graph state).
        conversation_turns = await session_service.list_conversation_turns(db, session.id)
        previous_agent_turn = await session_service.latest_conversation_turn(db, session.id, role="agent")
        input_data.state = {
            "session_id": session_id_str,
            "stage": Stage.DISCOVERY,
            "model": model_before,
            "user_message": user_text,
            "conversation_context": _render_user_message_history(conversation_turns),
            "previous_agent_message": previous_agent_turn.text if previous_agent_turn is not None else None,
            "request_impact": classify_user_request(user_text),
        }

    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_stream():
        request_agent = agent.clone()
        try:
            async for event in request_agent.run(input_data):
                yield encoder.encode(event)
        finally:
            try:
                snapshot = await agent.graph.aget_state(thread_config)
                if bool(snapshot.next):
                    # A structural/high-impact patch interrupted this run —
                    # nothing to persist yet (apply_patches_node calls
                    # interrupt() before mutating anything, see its
                    # docstring); the lock is released so the eventual
                    # approve/edit/reject request can acquire it again.
                    pass
                else:
                    await _persist_turn(db, session, meter, model_before, snapshot.values)
            except (UnicodeDecodeError, UnicodeError):
                # Pre-existing structural issue, not specific to this endpoint:
                # the local dev Postgres instance was created with WIN1252
                # encoding rather than UTF-8 (see DECISIONS.md), so a checkpoint
                # row containing certain Unicode punctuation from LLM output can
                # fail to decode on readback. sessions.py's endpoint has a
                # retry-with-reset recovery for the STALE-row case (corruption
                # discovered before this turn's own output exists); this is the
                # harder case — corruption in the row THIS turn just wrote — so
                # there is nothing safe to retry. Degrade instead of crashing
                # the stream: log clearly, still release the lock so the
                # session isn't left stuck, and let the checkpoint (not the
                # durable model/audit tables) be what's stale for next turn.
                logger.error(
                    "ag-ui: checkpoint read failed with a Unicode decode error for session %s — likely the "
                    "known WIN1252 Postgres encoding issue (see DECISIONS.md); this turn's conversational "
                    "state may not have persisted, but no data was corrupted",
                    session_id_str,
                )
            finally:
                await session_lock.release(session_id_str, lock_token)

    return StreamingResponse(event_stream(), media_type=encoder.get_content_type())

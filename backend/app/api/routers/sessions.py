"""Session lifecycle API.

Gate enforcement note: both gates check the PERSISTED session.status, not graph
state. A replayed or stale checkpoint therefore cannot skip a gate — the database row
is the authority (Doc 3: 'stages can't be skipped' has to hold under resume, not just
under happy-path traversal).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import (
    CurrentUser,
    Db,
    SessionLock,
    enforce_message_rate_limit,
    enforce_rate_limit,
    get_gateway,
)
from app.config import get_settings
from app.core.discovery_confidence import compute_discovery_confidence
from app.core.exporter import render_docx, render_pdf
from app.core.graph_engine import compute_impact
from app.db.models import SessionStatus
from app.llm.base import TokenBudgetExceededError
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.llm.streaming import reasoning_sink_scope
from app.orchestration.checkpointer import get_checkpointer
from app.orchestration.graph import (
    STRUCTURAL_PATCH_OPS,
    build_discovery_graph,
    build_planning_graph,
    build_review_discuss_graph,
)
from app.orchestration.state import Stage
from app.security.session_lock import SessionBusyError
from app.services import session_service
from app.services.session_service import GateError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])
STREAM_HEARTBEAT_S = 15.0


class CreateSessionRequest(BaseModel):
    name: str = Field(default="Untitled migration", max_length=255)


class SessionResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    token_usage: int


class MessageRequest(BaseModel):
    # Raised from 10k to accommodate pasting a config file (docker-compose.yml, a
    # Terraform plan summary, a README architecture section) directly into
    # discovery — see DECISIONS.md. This is still conversational ingestion (the
    # LLM reads it as freeform text, same ingest prompt, same validation),
    # not an automated IaC parser — that remains out of scope per the PRD's own
    # Non-Goals. The cap exists so one message can't blow past a sane prompt size
    # for the cheap-tier ingestion model.
    message: str = Field(min_length=1, max_length=50_000)
    message_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Client-generated idempotency key (e.g. a uuid) for this turn. "
            "A retried/double-submitted request with the same id is not re-processed "
            "(FR-E6)."
        ),
    )


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def _astream_with_reasoning(agen, queue: asyncio.Queue):
    """Merges a LangGraph node-update stream with a concurrently-fed queue of
    (node_name, reasoning_delta_text) events (see app/llm/streaming.py — a
    contextvar sink registered for the duration of this turn, fed by LLMGateway as
    a reasoning-model call streams), yielding whichever is ready first. This is
    purely an interleaving of TWO existing streams — LangGraph itself never learns
    this exists, so a graph-internal retry, checkpoint replay, or interrupt cycle
    behaves exactly as before; this wrapper only changes what the caller sees in
    between node completions.

    Always drains and yields every already-queued reasoning event before waiting
    on more graph output, so a node's own thinking text is never reordered to arrive
    after that node's node_complete event.
    """

    it = agen.__aiter__()
    node_task = asyncio.ensure_future(it.__anext__())
    queue_task = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait(
                {node_task, queue_task},
                timeout=STREAM_HEARTBEAT_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                yield ("heartbeat", None, None)
                continue

            if queue_task in done:
                node_name, text = queue_task.result()
                yield ("thinking", node_name, text)
                queue_task = asyncio.ensure_future(queue.get())

            if node_task in done:
                try:
                    chunk = node_task.result()
                except StopAsyncIteration:
                    return
                yield ("node", chunk, None)
                node_task = asyncio.ensure_future(it.__anext__())
    finally:
        node_task.cancel()
        queue_task.cancel()


def _render_user_message_history(turns: list, *, max_chars: int = 8_000) -> str:
    """Prompt context from the user's own prior messages.

    The canonical model is still the source of truth, but this keeps question
    generation aware of facts the user just gave while a patch/assumption is
    still being written. The cap prevents long pasted documents from breaking
    fallback providers with small context limits.
    """

    messages = [
        turn.text.strip()
        for turn in turns
        if turn.role == "user" and turn.text.strip()
    ]
    if not messages:
        return "(none)"

    rendered = "\n".join(
        f"{index}. {message}" for index, message in enumerate(messages, start=1)
    )
    if len(rendered) <= max_chars:
        return rendered

    tail = rendered[-max_chars:]
    first_newline = tail.find("\n")
    if first_newline != -1:
        tail = tail[first_newline + 1 :]
    return (
        "[Earlier user messages omitted because the prompt context was too large]\n"
        + tail
    )


def _patch_justification(patch: dict, outcome: str, reason: str | None) -> str:
    op = patch.get("op", "unknown")
    if outcome == "rejected":
        return reason or "The patch was rejected because it did not pass deterministic validation."

    match op:
        case "add_component":
            return (
                "Recommended because the user's architecture description names this as a "
                "distinct deployable or managed component."
            )
        case "update_component":
            return "Recommended because the user's latest message corrected or refined a known component attribute."
        case "remove_component":
            return "Recommended because the user's latest message removed this component from the architecture scope."
        case "add_dependency":
            source = patch.get("source_id", "source")
            target = patch.get("target_id", "target")
            kind = patch.get("kind", "dependency")
            return (
                f"Recommended because the described workflow implies "
                f"{source} depends on {target} via {kind}."
            )
        case "remove_dependency":
            return "Recommended because the user's latest message corrected the relationship between these components."
        case "add_assumption":
            return "Recorded as an assumption because the statement is a reasonable inference, but not yet a confirmed fact."
        case "confirm_assumption":
            return "Recommended because the user's latest message confirmed or corrected an existing assumption."
        case "resolve_open_question":
            return "Recommended because the user's latest message answered an open question."
        case "add_open_question":
            return "Recommended because the proposed change needs clarification before the architecture model should be changed."
        case _:
            return "Recommended based on the user's latest message and validated against the current architecture model."


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_rate_limit)],
)
async def create_session(
    payload: CreateSessionRequest,
    user: CurrentUser,
    db: Db,
) -> SessionResponse:
    session = await session_service.create_session(db, user.id, payload.name)
    return SessionResponse(
        id=session.id,
        name=session.name,
        status=session.status,
        token_usage=session.token_usage,
    )


@router.get(
    "",
    response_model=list[SessionResponse],
    dependencies=[Depends(enforce_rate_limit)],
)
async def list_sessions(user: CurrentUser, db: Db) -> list[SessionResponse]:
    """Every session this user owns, most recently active first — the dashboard a
    'resume days later' story actually needs."""

    sessions = await session_service.list_sessions_for_user(db, user.id)
    return [
        SessionResponse(
            id=s.id,
            name=s.name,
            status=s.status,
            token_usage=s.token_usage,
        )
        for s in sessions
    ]


@router.get("/{session_id}/state", dependencies=[Depends(enforce_rate_limit)])
async def get_state(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    model = await session_service.latest_model(db, session.id)
    plan = await session_service.latest_plan(db, session.id)
    context = await session_service.get_migration_context(db, session.id)

    return {
        "session": {
            "id": str(session.id),
            "name": session.name,
            "status": session.status,
            "token_usage": session.token_usage,
        },
        "model": model.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if plan else None,
        "migration_context": context.model_dump(mode="json") if context else None,
        # Discovery Confidence Score: always computed fresh from the current
        # model, never cached — see app.core.discovery_confidence.
        "discovery_confidence": compute_discovery_confidence(model).model_dump(
            mode="json"
        ),
    }


@router.get("/{session_id}/messages", dependencies=[Depends(enforce_rate_limit)])
async def get_conversation(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Full conversation history so ChatPanel can rehydrate after a refresh —
    previously there was no persisted record of turn text at all."""

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    turns = await session_service.list_conversation_turns(db, session_id)
    return {
        "turns": [
            {
                "role": t.role,
                "text": t.text,
                "created_at": t.created_at.isoformat(),
            }
            for t in turns
        ]
    }


@router.post(
    "/{session_id}/messages",
    dependencies=[Depends(enforce_message_rate_limit)],
)
async def post_message(
    session_id: uuid.UUID,
    payload: MessageRequest,
    user: CurrentUser,
    db: Db,
    gateway: Annotated[LLMGateway, Depends(get_gateway)],
    session_lock: SessionLock,
) -> EventSourceResponse:
    """Streams a discovery (or context-elicitation) turn over SSE.

    Resume semantics (DECISIONS.md): events carry an id equal to the completed graph
    node. A client reconnecting with Last-Event-ID resumes from the last COMPLETED
    node's persisted output — stage granularity, not token granularity. We never
    re-run an in-flight LLM call to synthesize partial text the client already saw.

    Concurrency (FR-E6): message_id claiming only rejects a replay of the same
    message. Two genuinely different messages sent concurrently for the same session
    would otherwise both read the same latest model, both advance the same LangGraph
    checkpoint thread, and race on the next ModelVersion insert. session_lock
    serializes the whole turn — read model through persist — per session, across
    replicas (Redis-backed).
    """

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    session_uuid = session.id
    session_id_str = str(session_uuid)

    if session.status not in (
        SessionStatus.DISCOVERY,
        SessionStatus.PLANNING,
        SessionStatus.REVIEW,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"session is in '{session.status}' — no further messages accepted",
        )

    try:
        lock_token = await session_lock.acquire(session_id_str)
    except SessionBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another turn is already in progress for this session — wait for it to complete",
        ) from None

    is_new_message = await session_service.claim_message(
        db,
        session_uuid,
        payload.message_id,
    )
    if not is_new_message:
        await session_lock.release(session_id_str, lock_token)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this message_id was already processed for this session — not re-running the turn",
        )

    await session_service.save_conversation_turn(
        db,
        session_uuid,
        "user",
        payload.message,
    )

    settings = get_settings()
    meter = SessionTokenMeter(
        settings.session_token_budget,
        already_spent=session.token_usage or 0,
    )
    try:
        meter.check_before_call()
    except TokenBudgetExceededError as exc:
        await session_lock.release(session_id_str, lock_token)
        await session_service.release_message_claim(db, session_uuid, payload.message_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    model_before = await session_service.latest_model(db, session_uuid)
    conversation_turns = await session_service.list_conversation_turns(
        db,
        session_uuid,
    )
    conversation_context = _render_user_message_history(conversation_turns)
    previous_agent_turn = await session_service.latest_conversation_turn(
        db,
        session_uuid,
        role="agent",
    )
    previous_agent_message = (
        previous_agent_turn.text if previous_agent_turn is not None else None
    )

    async def event_stream():
        checkpointer = get_checkpointer()
        langgraph_thread_id = session.langgraph_thread_id

        original_status = session.status

        if original_status == SessionStatus.DISCOVERY:
            graph = build_discovery_graph(
                gateway,
                meter,
            ).compile(checkpointer=checkpointer)

            initial = {
                "session_id": session_id_str,
                "stage": Stage.DISCOVERY,
                "model": model_before,
                "user_message": payload.message,
                "conversation_context": conversation_context,
                "previous_agent_message": previous_agent_message,
            }

        elif original_status == SessionStatus.PLANNING:
            graph = build_planning_graph(
                gateway,
                meter,
            ).compile(checkpointer=checkpointer)

            accepted = await session_service.accepted_model(
                db,
                session_uuid,
            )

            initial = {
                "session_id": session_id_str,
                "stage": Stage.PLANNING,
                "model": accepted,
                "user_message": payload.message,
                "conversation_context": conversation_context,
                "previous_agent_message": previous_agent_message,
                "migration_context": await session_service.get_migration_context(
                    db,
                    session_uuid,
                ),
                "narration": "",
                "pending_questions": [],
                "context_clarifying_questions": [],
                "error": None,
            }

        else:
            graph = build_review_discuss_graph(
                gateway,
                meter,
            ).compile(checkpointer=checkpointer)

            accepted = await session_service.accepted_model(
                db,
                session_uuid,
            )

            initial = {
                "session_id": session_id_str,
                "stage": Stage.REVIEW,
                "model": accepted,
                "user_message": payload.message,
                "conversation_context": conversation_context,
                "previous_agent_message": previous_agent_message,
                "plan": await session_service.latest_plan(
                    db,
                    session_uuid,
                ),
                "migration_context": await session_service.get_migration_context(
                    db,
                    session_uuid,
                ),
                "narration": "",
                "pending_questions": [],
                "findings": [],
                "refine_iteration": 0,
                "review_quality_history": [],
                "error": None,
            }

        final_state = None
        accumulated_values = dict(initial)

        reasoning_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        async def _emit_reasoning(node_name: str, text: str) -> None:
            reasoning_queue.put_nowait((node_name, text))

        try:
            with reasoning_sink_scope(_emit_reasoning):
                try:
                    graph_started = False

                    for attempt in range(3):
                        try:
                            interrupted = False
                            thread_config = _thread_config(langgraph_thread_id)

                            graph_stream = graph.astream(
                                initial,
                                config=thread_config,
                                stream_mode="updates",
                            )

                            async for kind, stream_payload, extra in _astream_with_reasoning(
                                graph_stream,
                                reasoning_queue,
                            ):
                                if kind == "heartbeat":
                                    yield {
                                        "event": "heartbeat",
                                        "data": json.dumps({}),
                                    }
                                    continue

                                if kind == "thinking":
                                    yield {
                                        "event": "thinking",
                                        "data": json.dumps(
                                            {
                                                "node": stream_payload,
                                                "text": extra,
                                            }
                                        ),
                                    }
                                    continue

                                chunk = stream_payload
                                graph_started = True

                                for node_name, node_output in chunk.items():
                                    if node_name == "__interrupt__":
                                        interrupted = True
                                        continue

                                    final_state = node_output

                                    if isinstance(node_output, dict):
                                        accumulated_values.update(node_output)

                                    yield {
                                        "id": node_name,
                                        "event": "node_complete",
                                        "data": json.dumps(
                                            {
                                                "node": node_name,
                                                "narration": (
                                                    node_output or {}
                                                ).get("narration")
                                            }
                                        ),
                                    }

                            if interrupted:
                                resume_stream = graph.astream(
                                    Command(resume={"approved": False}),
                                    config=thread_config,
                                    stream_mode="updates",
                                )

                                async for kind, stream_payload, extra in _astream_with_reasoning(
                                    resume_stream,
                                    reasoning_queue,
                                ):
                                    if kind == "heartbeat":
                                        yield {
                                            "event": "heartbeat",
                                            "data": json.dumps({}),
                                        }
                                        continue

                                    if kind == "thinking":
                                        yield {
                                            "event": "thinking",
                                            "data": json.dumps(
                                                {
                                                    "node": stream_payload,
                                                    "text": extra,
                                                }
                                            ),
                                        }
                                        continue

                                    chunk = stream_payload

                                    for node_name, node_output in chunk.items():
                                        if node_name == "__interrupt__":
                                            continue

                                        final_state = node_output

                                        if isinstance(node_output, dict):
                                            accumulated_values.update(node_output)

                                        yield {
                                            "id": node_name,
                                            "event": "node_complete",
                                            "data": json.dumps(
                                                {
                                                    "node": node_name,
                                                    "narration": (
                                                        node_output or {}
                                                    ).get("narration")
                                                }
                                            ),
                                        }

                            break

                        except (UnicodeDecodeError, UnicodeError):
                            if graph_started or attempt == 2:
                                raise

                            logger.warning(
                                "checkpoint decode failed for session %s "
                                "(attempt %d); resetting LangGraph thread and retrying",
                                session_uuid,
                                attempt,
                            )

                            await session_service.reset_langgraph_thread(
                                db,
                                session,
                            )

                            final_state = None
                            accumulated_values = dict(initial)

                except TokenBudgetExceededError as exc:
                    logger.warning(
                        "token budget exhausted for session %s: %s",
                        session_uuid,
                        exc,
                    )
                    await db.rollback()
                    await session_service.release_message_claim(
                        db,
                        session_uuid,
                        payload.message_id,
                    )
                    await session_service.save_conversation_turn(
                        db,
                        session_uuid,
                        "error",
                        str(exc),
                    )
                    yield {
                        "event": "error",
                        "data": json.dumps({"detail": str(exc)}),
                    }
                    return

                except Exception as exc:
                    logger.exception(
                        "graph run failed for session %s",
                        session_uuid,
                    )
                    await db.rollback()
                    await session_service.release_message_claim(
                        db,
                        session_uuid,
                        payload.message_id,
                    )
                    await session_service.save_conversation_turn(
                        db,
                        session_uuid,
                        "error",
                        "planning run failed",
                    )
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {
                                "detail": "planning run failed",
                                "error": str(exc),
                            }
                        ),
                    }
                    return

            values = accumulated_values or (final_state or {})


            await _persist_turn(
                db,
                session,
                meter,
                model_before,
                values,
            )

            question_details = []

            if original_status == SessionStatus.DISCOVERY:
                narration = values.get("narration")
                questions = values.get("pending_questions", [])

                question_details = [
                    q.model_dump(mode="json")
                    for q in values.get("question_details", [])
                ]

            elif original_status == SessionStatus.PLANNING:
                plan = values.get("plan")
                clarifying = values.get("context_clarifying_questions") or []

                intake_questions = [
                    str(r.patch.text)
                    for r in (values.get("last_patch_results") or [])
                    if str(getattr(r, "outcome", "")) == "applied"
                    and str(getattr(r.patch, "op", "")) == "add_open_question"
                    and getattr(r.patch, "text", None)
                ]

                intake_results = values.get("last_patch_results") or []

                if values.get("error"):
                    narration = None
                elif intake_questions:
                    narration = values.get("narration")
                elif clarifying:
                    narration = None
                elif plan is not None:
                    narration = (
                        f"Migration plan generated: {len(plan.waves)} wave(s) covering "
                        f"{len(plan.component_plans)} component(s), {len(plan.risks)} risk(s) flagged. "
                        "Review the target architecture and full migration plan below."
                    )
                elif intake_results:
                    narration = values.get("narration") or (
                        "Updated the accepted source model. Send the migration context when ready."
                    )
                else:
                    narration = "Migration context captured."

                questions = intake_questions

            else:
                replanned = any(
                    str(r.outcome) == "applied"
                    and str(r.patch.op) in STRUCTURAL_PATCH_OPS
                    for r in (values.get("last_patch_results") or [])
                )

                plan = values.get("plan")

                if values.get("error"):
                    narration = None
                elif replanned and plan is not None:
                    narration = (
                        f"Updated the plan to reflect this change: {len(plan.waves)} wave(s) covering "
                        f"{len(plan.component_plans)} component(s), {len(plan.risks)} risk(s) flagged. "
                        "Review the updated plan below before approving."
                    )
                else:
                    narration = values.get("narration")

                questions = []

            turn_error = values.get("error")

            if turn_error:
                await session_service.save_conversation_turn(
                    db,
                    session_uuid,
                    "error",
                    str(turn_error),
                )
            else:
                clarifying_questions = (
                    values.get("context_clarifying_questions") or []
                )

                display_parts = []

                if narration:
                    display_parts.append(narration)

                if clarifying_questions:
                    display_parts.append(
                        "I need to clarify a few things before continuing:\n"
                        + "\n".join(
                            f"• {q}" for q in clarifying_questions
                        )
                    )
                elif questions:
                    display_parts.append(
                        "\n".join(f"• {q}" for q in questions)
                    )

                await session_service.save_conversation_turn(
                    db,
                    session_uuid,
                    "agent",
                    "\n\n".join(display_parts) or "Understood.",
                )

            yield {
                "event": "turn_complete",
                "data": json.dumps(
                    {
                        "narration": narration,
                        "questions": questions,
                        "question_details": question_details,
                        "clarifying_questions": values.get(
                            "context_clarifying_questions",
                            [],
                        ),
                        "error": values.get("error"),
                        "model_version": getattr(
                            values.get("model"),
                            "version",
                            None,
                        ),
                        "tokens_used": meter.spent,
                        "request_impact": (
                            values.get("request_impact").model_dump(mode="json")
                            if values.get("request_impact") is not None
                            else None
                        ),
                    }
                ),
            }

        finally:
            await session_lock.release(
                session_id_str,
                lock_token,
            )

    return EventSourceResponse(event_stream())


async def _persist_turn(
    db,
    session,
    meter: SessionTokenMeter,
    model_before,
    values: dict,
) -> None:
    """Writes the turn's artifacts. Runs after the graph completes so a mid-turn
    disconnect leaves the checkpoint (resumable) without half-written audit rows."""

    model = values.get("model")

    if model is not None and getattr(model, "version", 0) > model_before.version:
        await session_service.save_model_version(
            db,
            session.id,
            model,
        )

        results = values.get("last_patch_results") or []

        await session_service.save_patch_audit(
            db,
            session.id,
            results,
            model_before.version,
        )

    context = values.get("migration_context")

    if context is not None:
        await session_service.save_migration_context(
            db,
            session.id,
            context,
        )

    plan = values.get("plan")

    if plan is not None:
        plan_row = await session_service.save_plan_version(
            db,
            session.id,
            plan,
        )

        findings = values.get("findings") or []

        await session_service.save_findings(
            db,
            session.id,
            plan_row.id,
            findings,
        )

        review_quality = values.get("review_quality_history") or []

        await session_service.save_review_quality(
            db,
            session.id,
            review_quality,
        )

        if session.status == SessionStatus.PLANNING:
            session.status = SessionStatus.REVIEW

    session.token_usage = meter.spent

    await db.commit()


@router.post(
    "/{session_id}/model/accept",
    dependencies=[Depends(enforce_rate_limit)],
)
async def accept_model(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Gate 1 — freezes the architecture model. Planning cannot start before this."""

    try:
        session = await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
        model = await session_service.accept_model(
            db,
            session,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None
    except GateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return {
        "status": "accepted",
        "model_version": model.version,
        "session_status": session.status,
    }


@router.post(
    "/{session_id}/plan/approve",
    dependencies=[Depends(enforce_rate_limit)],
)
async def approve_plan(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Gate 2 — marks the plan final and unlocks export."""

    try:
        session = await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
        plan = await session_service.approve_plan(
            db,
            session,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None
    except GateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return {
        "status": "final",
        "plan_version": plan.version,
        "session_status": session.status,
    }


@router.get(
    "/{session_id}/impact/{component_id}",
    dependencies=[Depends(enforce_rate_limit)],
)
async def get_component_impact(
    session_id: uuid.UUID,
    component_id: str,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Reachability-based impact analysis over the current (latest) model: what
    depends on this component (upstream — affected if it changes or moves) and
    what it depends on (downstream). Backs 'what breaks if I touch this' — the
    same question the Engineering Director and Migration Architect personas ask
    before committing to a disposition, computed by GraphEngine rather than
    guessed at."""

    try:
        session = await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    model = await session_service.latest_model(db, session.id)

    if component_id not in model.component_ids():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no component with id '{component_id}'",
        )

    return compute_impact(model, component_id)


@router.get(
    "/{session_id}/findings",
    dependencies=[Depends(enforce_rate_limit)],
)
async def get_findings(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    from sqlalchemy import select

    from app.db.models import FindingRecord

    try:
        await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    result = await db.execute(
        select(FindingRecord)
        .where(FindingRecord.session_id == session_id)
        .order_by(FindingRecord.created_at.desc())
    )

    return {
        "findings": [
            {
                "id": str(f.id),
                "source": f.source,
                "rule_id": f.rule_id,
                "severity": f.severity,
                "message": f.message,
                "related_component_ids": f.related_component_ids,
                "resolution_status": f.resolution_status,
            }
            for f in result.scalars().all()
        ]
    }


class ResolveFindingRequest(BaseModel):
    resolution_status: str = Field(
        description="One of: 'resolved', 'accepted_as_risk', 'open'."
    )


@router.patch(
    "/{session_id}/findings/{finding_id}",
    dependencies=[Depends(enforce_rate_limit)],
)
async def resolve_finding(
    session_id: uuid.UUID,
    finding_id: uuid.UUID,
    payload: ResolveFindingRequest,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Lets the Migration Architect mark a review finding resolved or explicitly
    accepted as a risk instead of leaving it open forever — the counterpart to
    RULE-00x/LLM findings shipping as documented Risks when the refine-loop budget
    runs out (Doc 3 §3.1). Purely a record-keeping annotation: it does not re-run
    rules, mutate the plan, or gate approval — Gate 2 remains reachable regardless
    of open findings, same as today."""

    try:
        await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    try:
        record = await session_service.set_finding_resolution(
            db,
            session_id,
            finding_id,
            payload.resolution_status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="finding not found",
        ) from None

    return {
        "id": str(record.id),
        "resolution_status": record.resolution_status,
    }


@router.get(
    "/{session_id}/source-model-updates",
    dependencies=[Depends(enforce_rate_limit)],
)
@router.get(
    "/{session_id}/audit",
    dependencies=[Depends(enforce_rate_limit)],
    include_in_schema=False,
)
async def get_source_model_updates(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Source-model evidence trail.

    Patches are an internal deterministic mechanism. The product surface calls
    this captured source facts, assumptions, and open questions, never target
    architecture recommendations.
    """

    from sqlalchemy import select

    from app.db.models import PatchAuditRecord

    try:
        await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    result = await db.execute(
        select(PatchAuditRecord)
        .where(PatchAuditRecord.session_id == session_id)
        .order_by(PatchAuditRecord.created_at)
    )

    return {
        "records": [
            {
                "patch": r.patch_data,
                "outcome": r.outcome,
                "reason": r.reason,
                "justification": _patch_justification(
                    r.patch_data,
                    r.outcome,
                    r.reason,
                ),
                "model_version_before": r.model_version_before,
                "model_version_after": r.model_version_after,
            }
            for r in result.scalars().all()
        ]
    }


@router.get(
    "/{session_id}/review-quality",
    dependencies=[Depends(enforce_rate_limit)],
)
async def get_review_quality(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
) -> dict:
    """Judge scores over the LLM semantic critic's own findings, one per refine
    iteration — 'how good is the AI's critique', not part of the migration
    deliverable itself. Never gates approval; purely observability (PRD Decision
    Q7 override, see DECISIONS.md)."""

    try:
        await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    records = await session_service.get_review_quality(
        db,
        session_id,
    )

    return {
        "scores": [
            {
                "iteration": r.iteration,
                "evaluated_finding_count": r.evaluated_finding_count,
                "relevance_score": r.relevance_score,
                "specificity_score": r.specificity_score,
                "actionability_score": r.actionability_score,
                "context_awareness_score": r.context_awareness_score,
                "overall_score": r.overall_score,
                "rationale": r.rationale,
                "flagged_issues": r.flagged_issues,
            }
            for r in records
        ]
    }


@router.get(
    "/{session_id}/export",
    dependencies=[Depends(enforce_rate_limit)],
)
async def export_plan(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    format: str = "docx",
) -> Response:
    """Renders the 10-deliverable package. Requires gate 2 — an unapproved plan is
    not a deliverable."""

    try:
        session = await session_service.get_session_for_user(
            db,
            session_id,
            user.id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        ) from None

    if session.status != SessionStatus.EXPORTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="plan has not been approved — call /plan/approve first",
        )

    model = await session_service.accepted_model(db, session.id)
    plan = await session_service.latest_plan(db, session.id)
    context = await session_service.get_migration_context(db, session.id)

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no plan to export",
        )

    if format == "docx":
        content = render_docx(model, plan, context)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="migration-plan-{session_id}.docx"'
                )
            },
        )

    if format == "pdf":
        return Response(
            content=render_pdf(model, plan, context),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="migration-plan-{session_id}.pdf"'
                )
            },
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="format must be 'pdf' or 'docx'",
    )
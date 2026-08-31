"""Session lifecycle API.

Gate enforcement note: both gates check the PERSISTED session.status, not graph
state. A replayed or stale checkpoint therefore cannot skip a gate — the database row
is the authority (Doc 3: 'stages can't be skipped' has to hold under resume, not just
under happy-path traversal).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, Db, SessionLock, enforce_message_rate_limit, enforce_rate_limit, get_gateway
from app.config import get_settings
from app.core.exporter import render_docx, render_pdf
from app.core.graph_engine import compute_impact
from app.core.request_intelligence import classify_user_request
from app.db.models import SessionStatus
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.orchestration.checkpointer import get_checkpointer
from app.orchestration.graph import STRUCTURAL_PATCH_OPS, build_discovery_graph, build_planning_graph, build_review_discuss_graph
from app.orchestration.state import Stage
from app.security.session_lock import SessionBusyError
from app.services import session_service
from app.services.session_service import GateError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


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
    # LLM reads it as freeform text, same ingest_patches prompt, same validation),
    # not an automated IaC parser — that remains out of scope per the PRD's own
    # Non-Goals. The cap exists so one message can't blow past a sane prompt size
    # for the cheap-tier ingestion model.
    message: str = Field(min_length=1, max_length=50_000)
    message_id: str = Field(
        min_length=1,
        max_length=128,
        description="Client-generated idempotency key (e.g. a uuid) for this turn. "
        "A retried/double-submitted request with the same id is not re-processed (FR-E6).",
    )


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


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
            return f"Recommended because the described workflow implies {source} depends on {target} via {kind}."
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


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(enforce_rate_limit)])
async def create_session(payload: CreateSessionRequest, user: CurrentUser, db: Db) -> SessionResponse:
    session = await session_service.create_session(db, user.id, payload.name)
    return SessionResponse(id=session.id, name=session.name, status=session.status, token_usage=session.token_usage)


@router.get("", response_model=list[SessionResponse], dependencies=[Depends(enforce_rate_limit)])
async def list_sessions(user: CurrentUser, db: Db) -> list[SessionResponse]:
    """Every session this user owns, most recently active first — the dashboard a
    'resume days later' story actually needs."""

    sessions = await session_service.list_sessions_for_user(db, user.id)
    return [
        SessionResponse(id=s.id, name=s.name, status=s.status, token_usage=s.token_usage) for s in sessions
    ]


@router.get("/{session_id}/state", dependencies=[Depends(enforce_rate_limit)])
async def get_state(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    model = await session_service.latest_model(db, session.id)
    plan = await session_service.latest_plan(db, session.id)
    context = await session_service.get_migration_context(db, session.id)

    return {
        "session": {"id": str(session.id), "name": session.name, "status": session.status,
                     "token_usage": session.token_usage},
        "model": model.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if plan else None,
        "migration_context": context.model_dump(mode="json") if context else None,
    }


@router.get("/{session_id}/messages", dependencies=[Depends(enforce_rate_limit)])
async def get_conversation(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    """Full conversation history so ChatPanel can rehydrate after a refresh —
    previously there was no persisted record of turn text at all (see
    ConversationTurn's docstring)."""

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    turns = await session_service.list_conversation_turns(db, session_id)
    return {"turns": [{"role": t.role, "text": t.text, "created_at": t.created_at.isoformat()} for t in turns]}


@router.post("/{session_id}/messages", dependencies=[Depends(enforce_message_rate_limit)])
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

    Concurrency (FR-E6): message_id claiming (below) only rejects a REPLAY of the
    same message. Two genuinely different messages sent concurrently for the same
    session (two tabs, a scripted client) would otherwise both read the same
    latest model, both advance the same LangGraph checkpoint thread, and race on
    the next ModelVersion insert. session_lock serializes the whole turn —
    read model through persist — per session, across replicas (Redis-backed).
    """

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    # Captured once, up front: claim_message's IntegrityError branch rolls back
    # this db session, which expires every attribute on every ORM object loaded
    # through it (including `session` itself) — a later `str(session.id)` would
    # trigger a lazy-reload that MissingGreenlet's outside a proper async context.
    session_id_str = str(session.id)

    if session.status not in (SessionStatus.DISCOVERY, SessionStatus.PLANNING, SessionStatus.REVIEW):
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

    is_new_message = await session_service.claim_message(db, session.id, payload.message_id)
    if not is_new_message:
        await session_lock.release(session_id_str, lock_token)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this message_id was already processed for this session — not re-running the turn",
        )

    # Persisted immediately (not after the graph runs) so a page refresh mid-turn
    # still shows the message the user just sent, even if the run itself fails.
    await session_service.save_conversation_turn(db, session.id, "user", payload.message)

    settings = get_settings()
    meter = SessionTokenMeter(settings.session_token_budget, already_spent=session.token_usage or 0)
    model_before = await session_service.latest_model(db, session.id)
    previous_agent_turn = await session_service.latest_conversation_turn(db, session.id, role="agent")
    previous_agent_message = previous_agent_turn.text if previous_agent_turn is not None else None

    async def event_stream():
        checkpointer = get_checkpointer()
        # _persist_turn (below) may advance session.status (PLANNING -> REVIEW) once
        # a plan lands, so the branch this turn actually ran must be captured now —
        # checking session.status again afterward would silently pick the wrong shape.
        original_status = session.status

        if original_status == SessionStatus.DISCOVERY:
            request_impact = classify_user_request(payload.message)
            graph = build_discovery_graph(gateway, meter).compile(checkpointer=checkpointer)
            initial = {
                "session_id": str(session.id),
                "stage": Stage.DISCOVERY,
                "model": model_before,
                "user_message": payload.message,
                "previous_agent_message": previous_agent_message,
                "request_impact": request_impact,
            }
        elif original_status == SessionStatus.PLANNING:
            request_impact = classify_user_request(payload.message, after_gate_1=True)
            graph = build_planning_graph(gateway, meter).compile(checkpointer=checkpointer)
            accepted = await session_service.accepted_model(db, session.id)
            initial = {
                "session_id": str(session.id),
                "stage": Stage.PLANNING,
                "model": accepted,
                "user_message": payload.message,
                "previous_agent_message": previous_agent_message,
                "request_impact": request_impact,
                "migration_context": await session_service.get_migration_context(db, session.id),
                "narration": "",
                "pending_questions": [],
                "context_clarifying_questions": [],
                "error": None,
            }
        else:
            # REVIEW: discuss over a plan that already exists. Judged the same way
            # discovery judges a proposed addition; only cascades into a full
            # replan (compute_sequence onward) when the model actually changed —
            # see build_review_discuss_graph's docstring.
            request_impact = classify_user_request(payload.message, after_gate_1=True, review_stage=True)
            graph = build_review_discuss_graph(gateway, meter).compile(checkpointer=checkpointer)
            accepted = await session_service.accepted_model(db, session.id)
            initial = {
                "session_id": str(session.id),
                "stage": Stage.REVIEW,
                "model": accepted,
                "user_message": payload.message,
                "previous_agent_message": previous_agent_message,
                "request_impact": request_impact,
                # Read for context only (what relevance gets judged against) — a
                # discuss-only turn leaves this exact object in state untouched;
                # assemble_plan replaces it outright if the turn cascades into a
                # replan, so it's never mutated in place either way.
                "plan": await session_service.latest_plan(db, session.id),
                "migration_context": await session_service.get_migration_context(db, session.id),
                "narration": "",
                "pending_questions": [],
                "findings": [],
                "refine_iteration": 0,
                "review_quality_history": [],
                "error": None,
            }

        final_state = None
        accumulated_values = dict(initial)
        try:
            try:
                graph_started = False
                # Up to 3 attempts: a checkpoint written under an older code
                # version (a schema field/enum that changed shape since it was
                # written) can fail to decode even immediately after a reset,
                # since the reset only guarantees an EMPTY thread for THIS
                # retry — it does not guarantee the retry itself won't hit some
                # other stale row. One retry wasn't always enough in practice;
                # bail out for real (rather than silently loop) once nothing
                # has streamed after 3 fresh-thread attempts.
                for attempt in range(3):
                    try:
                        async for chunk in graph.astream(
                            initial, config=_thread_config(session.langgraph_thread_id), stream_mode="updates"
                        ):
                            graph_started = True
                            for node_name, node_output in chunk.items():
                                final_state = node_output
                                if isinstance(node_output, dict):
                                    accumulated_values.update(node_output)
                                yield {
                                    "id": node_name,
                                    "event": "node_complete",
                                    "data": json.dumps(
                                        {"node": node_name, "narration": (node_output or {}).get("narration")}
                                    ),
                                }
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        if graph_started or attempt == 2:
                            raise
                        logger.warning(
                            "checkpoint decode failed for session %s (attempt %d); resetting LangGraph "
                            "thread and retrying",
                            session.id,
                            attempt,
                        )
                        await session_service.reset_langgraph_thread(db, session)
                        final_state = None
                        accumulated_values = dict(initial)
            except Exception as exc:
                logger.exception("graph run failed for session %s", session.id)
                # Nothing was persisted for this turn — release the message_id
                # claim so a legitimate client retry isn't permanently rejected
                # as a duplicate (see claim_message's docstring).
                await session_service.release_message_claim(db, session.id, payload.message_id)
                # Stores the same text the client displays (below), not the raw
                # exception — internals stay out of the conversation history.
                await session_service.save_conversation_turn(db, session.id, "error", "planning run failed")
                yield {"event": "error", "data": json.dumps({"detail": "planning run failed", "error": str(exc)})}
                return

            values = accumulated_values or (final_state or {})
            await _persist_turn(db, session, meter, model_before, values)

            if original_status == SessionStatus.DISCOVERY:
                # Discovery narration/questions are genuinely per-turn LLM output.
                narration = values.get("narration")
                questions = values.get("pending_questions", [])
            elif original_status == SessionStatus.PLANNING:
                # Planning shares this thread's checkpointed state with any earlier
                # discovery turns, but no planning node ever sets `narration` or
                # `pending_questions` — those keys would otherwise still hold stale
                # discovery-stage text/questions from before Gate 1, which read as
                # nonsensical once a plan has actually been generated. Synthesize a
                # real status message from typed fields instead (never fresh LLM
                # prose — technique #12): the rich result itself is the Target
                # Architecture / Migration Plan sections the client re-fetches next.
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
                # REVIEW discuss: a "just discussing" turn (no structural patch
                # applied) behaves like discovery — the ingest node's own
                # narration/pending_questions are genuine per-turn LLM output. A
                # turn that DID change the model cascaded through a full replan
                # (build_review_discuss_graph), so the plan itself is the result —
                # synthesize a status message the same way planning does, never
                # fresh LLM prose about a plan that's already fully typed data.
                replanned = any(
                    str(r.outcome) == "applied" and str(r.patch.op) in STRUCTURAL_PATCH_OPS
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
                # Review discussion must never surface stale Discovery questions.
                # If a review turn needs user input, that belongs in the review
                # narration/open finding itself, not in Discovery's gap loop.
                questions = []

            # Mirrors exactly what ChatPanel synthesizes from this same payload
            # (narration + questions/clarifying_questions, "Understood." fallback)
            # so history read back later matches what was actually shown live.
            turn_error = values.get("error")
            if turn_error:
                await session_service.save_conversation_turn(db, session.id, "error", str(turn_error))
            else:
                clarifying_questions = values.get("context_clarifying_questions") or []
                display_parts = []
                if narration:
                    display_parts.append(narration)
                if clarifying_questions:
                    display_parts.append(
                        "I need to clarify a few things before continuing:\n"
                        + "\n".join(f"• {q}" for q in clarifying_questions)
                    )
                elif questions:
                    display_parts.append("\n".join(f"• {q}" for q in questions))
                await session_service.save_conversation_turn(
                    db, session.id, "agent", "\n\n".join(display_parts) or "Understood."
                )

            yield {
                "event": "turn_complete",
                "data": json.dumps(
                    {
                        "narration": narration,
                        "questions": questions,
                        "clarifying_questions": values.get("context_clarifying_questions", []),
                        "error": values.get("error"),
                        "model_version": getattr(values.get("model"), "version", None),
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
            await session_lock.release(str(session.id), lock_token)

    return EventSourceResponse(event_stream())


async def _persist_turn(db, session, meter: SessionTokenMeter, model_before, values: dict) -> None:
    """Writes the turn's artifacts. Runs after the graph completes so a mid-turn
    disconnect leaves the checkpoint (resumable) without half-written audit rows."""
    model = values.get("model")
    if model is not None and getattr(model, "version", 0) > model_before.version:
        await session_service.save_model_version(db, session.id, model)
        results = values.get("last_patch_results") or []
        await session_service.save_patch_audit(db, session.id, results, model_before.version)

    context = values.get("migration_context")
    if context is not None:
        await session_service.save_migration_context(db, session.id, context)

    plan = values.get("plan")
    if plan is not None:
        plan_row = await session_service.save_plan_version(db, session.id, plan)
        findings = values.get("findings") or []
        await session_service.save_findings(db, session.id, plan_row.id, findings)
        review_quality = values.get("review_quality_history") or []
        await session_service.save_review_quality(db, session.id, review_quality)
        if session.status == SessionStatus.PLANNING:
            session.status = SessionStatus.REVIEW

    session.token_usage = meter.spent
    await db.commit()


@router.post("/{session_id}/model/accept", dependencies=[Depends(enforce_rate_limit)])
async def accept_model(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    """Gate 1 — freezes the architecture model. Planning cannot start before this."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
        model = await session_service.accept_model(db, session)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None
    except GateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {"status": "accepted", "model_version": model.version, "session_status": session.status}


@router.post("/{session_id}/plan/approve", dependencies=[Depends(enforce_rate_limit)])
async def approve_plan(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    """Gate 2 — marks the plan final and unlocks export."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
        plan = await session_service.approve_plan(db, session)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None
    except GateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {"status": "final", "plan_version": plan.version, "session_status": session.status}


@router.get("/{session_id}/impact/{component_id}", dependencies=[Depends(enforce_rate_limit)])
async def get_component_impact(session_id: uuid.UUID, component_id: str, user: CurrentUser, db: Db) -> dict:
    """Reachability-based impact analysis over the current (latest) model: what
    depends on this component (upstream — affected if it changes or moves) and
    what it depends on (downstream). Backs 'what breaks if I touch this' — the
    same question the Engineering Director and Migration Architect personas ask
    before committing to a disposition, computed by GraphEngine rather than
    guessed at."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    model = await session_service.latest_model(db, session.id)
    if component_id not in model.component_ids():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no component with id '{component_id}'")

    return compute_impact(model, component_id)


@router.get("/{session_id}/findings", dependencies=[Depends(enforce_rate_limit)])
async def get_findings(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    from sqlalchemy import select

    from app.db.models import FindingRecord

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    result = await db.execute(
        select(FindingRecord).where(FindingRecord.session_id == session_id).order_by(FindingRecord.created_at.desc())
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
    resolution_status: str = Field(description="One of: 'resolved', 'accepted_as_risk', 'open'.")


@router.patch("/{session_id}/findings/{finding_id}", dependencies=[Depends(enforce_rate_limit)])
async def resolve_finding(
    session_id: uuid.UUID, finding_id: uuid.UUID, payload: ResolveFindingRequest, user: CurrentUser, db: Db
) -> dict:
    """Lets the Migration Architect mark a review finding resolved or explicitly
    accepted as a risk instead of leaving it open forever — the counterpart to
    RULE-00x/LLM findings shipping as documented Risks when the refine-loop budget
    runs out (Doc 3 §3.1). Purely a record-keeping annotation: it does not re-run
    rules, mutate the plan, or gate approval — Gate 2 remains reachable regardless
    of open findings, same as today."""

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    try:
        record = await session_service.set_finding_resolution(
            db, session_id, finding_id, payload.resolution_status
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found") from None

    return {
        "id": str(record.id),
        "resolution_status": record.resolution_status,
    }


@router.get("/{session_id}/audit", dependencies=[Depends(enforce_rate_limit)])
async def get_patch_audit(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    """Full patch audit trail — every proposal the LLM made, applied or rejected."""

    from sqlalchemy import select

    from app.db.models import PatchAuditRecord

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

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
                "justification": _patch_justification(r.patch_data, r.outcome, r.reason),
                "model_version_before": r.model_version_before,
                "model_version_after": r.model_version_after,
            }
            for r in result.scalars().all()
        ]
    }


@router.get("/{session_id}/review-quality", dependencies=[Depends(enforce_rate_limit)])
async def get_review_quality(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    """Judge scores over the LLM semantic critic's own findings, one per refine
    iteration — 'how good is the AI's critique', not part of the migration
    deliverable itself. Never gates approval; purely observability (PRD Decision
    Q7 override, see DECISIONS.md)."""

    try:
        await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    records = await session_service.get_review_quality(db, session_id)
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


@router.get("/{session_id}/export", dependencies=[Depends(enforce_rate_limit)])
async def export_plan(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    format: str = "docx",
) -> Response:
    """Renders the 10-deliverable package. Requires gate 2 — an unapproved plan is
    not a deliverable."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.EXPORTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="plan has not been approved — call /plan/approve first",
        )

    model = await session_service.accepted_model(db, session.id)
    plan = await session_service.latest_plan(db, session.id)
    context = await session_service.get_migration_context(db, session.id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no plan to export")

    if format == "docx":
        content = render_docx(model, plan, context)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="migration-plan-{session_id}.docx"'},
        )

    if format == "pdf":
        return Response(
            content=render_pdf(model, plan, context),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="migration-plan-{session_id}.pdf"'},
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format must be 'pdf' or 'docx'")

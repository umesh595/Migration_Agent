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

from app.api.deps import CurrentUser, Db, enforce_message_rate_limit, enforce_rate_limit, get_gateway
from app.config import get_settings
from app.core.exporter import render_docx, render_markdown
from app.db.models import SessionStatus
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.orchestration.checkpointer import get_checkpointer
from app.orchestration.graph import build_discovery_graph, build_planning_graph
from app.orchestration.state import Stage
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


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


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


@router.post("/{session_id}/messages", dependencies=[Depends(enforce_message_rate_limit)])
async def post_message(
    session_id: uuid.UUID,
    payload: MessageRequest,
    user: CurrentUser,
    db: Db,
    gateway: Annotated[LLMGateway, Depends(get_gateway)],
) -> EventSourceResponse:
    """Streams a discovery (or context-elicitation) turn over SSE.

    Resume semantics (DECISIONS.md): events carry an id equal to the completed graph
    node. A client reconnecting with Last-Event-ID resumes from the last COMPLETED
    node's persisted output — stage granularity, not token granularity. We never
    re-run an in-flight LLM call to synthesize partial text the client already saw.
    """

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status not in (SessionStatus.DISCOVERY, SessionStatus.PLANNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"session is in '{session.status}' — no further messages accepted",
        )

    settings = get_settings()
    meter = SessionTokenMeter(settings.session_token_budget, already_spent=session.token_usage or 0)
    model_before = await session_service.latest_model(db, session.id)

    async def event_stream():
        checkpointer = get_checkpointer()
        # _persist_turn (below) may advance session.status (PLANNING -> REVIEW) once
        # a plan lands, so the branch this turn actually ran must be captured now —
        # checking session.status again afterward would silently pick the wrong shape.
        original_status = session.status

        if original_status == SessionStatus.DISCOVERY:
            graph = build_discovery_graph(gateway, meter).compile(checkpointer=checkpointer)
            initial = {
                "session_id": str(session.id),
                "stage": Stage.DISCOVERY,
                "model": model_before,
                "user_message": payload.message,
            }
        else:
            graph = build_planning_graph(gateway, meter).compile(checkpointer=checkpointer)
            accepted = await session_service.accepted_model(db, session.id)
            initial = {
                "session_id": str(session.id),
                "stage": Stage.PLANNING,
                "model": accepted,
                "user_message": payload.message,
                "migration_context": await session_service.get_migration_context(db, session.id),
                "narration": "",
                "pending_questions": [],
                "context_clarifying_questions": [],
                "error": None,
            }

        final_state = None
        try:
            async for chunk in graph.astream(
                initial, config=_thread_config(session.langgraph_thread_id), stream_mode="updates"
            ):
                for node_name, node_output in chunk.items():
                    final_state = node_output
                    yield {
                        "id": node_name,
                        "event": "node_complete",
                        "data": json.dumps({"node": node_name, "narration": (node_output or {}).get("narration")}),
                    }
        except Exception as exc:
            logger.exception("graph run failed for session %s", session.id)
            yield {"event": "error", "data": json.dumps({"detail": "planning run failed", "error": str(exc)})}
            return

        await _persist_turn(db, session, meter, model_before)

        state_snapshot = await graph.aget_state(_thread_config(session.langgraph_thread_id))
        values = state_snapshot.values if state_snapshot else (final_state or {})

        if original_status == SessionStatus.DISCOVERY:
            # Discovery narration/questions are genuinely per-turn LLM output.
            narration = values.get("narration")
            questions = values.get("pending_questions", [])
        else:
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
            if values.get("error"):
                narration = None
            elif clarifying:
                narration = None
            elif plan is not None:
                narration = (
                    f"Migration plan generated: {len(plan.waves)} wave(s) covering "
                    f"{len(plan.component_plans)} component(s), {len(plan.risks)} risk(s) flagged. "
                    "Review the target architecture and full migration plan below."
                )
            else:
                narration = "Migration context captured."
            questions = []

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
                }
            ),
        }

    return EventSourceResponse(event_stream())


async def _persist_turn(db, session, meter: SessionTokenMeter, model_before) -> None:
    """Writes the turn's artifacts. Runs after the graph completes so a mid-turn
    disconnect leaves the checkpoint (resumable) without half-written audit rows."""

    checkpointer = get_checkpointer()
    config = _thread_config(session.langgraph_thread_id)
    snapshot = await checkpointer.aget_tuple(config)
    if snapshot is None:
        return

    values = snapshot.checkpoint.get("channel_values", {})
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
    format: str = "markdown",
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

    if format == "markdown":
        return Response(
            content=render_markdown(model, plan, context),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="migration-plan-{session_id}.md"'},
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format must be 'markdown' or 'docx'")

"""Third-party enterprise catalog integration endpoint.

Deliberately narrow: this imports components/dependencies from an external
enterprise catalog into the discovery-stage draft model, but every imported
record still flows through app.core.patch_validator / app.core.patch_applier
exactly like an LLM-proposed patch does. See DECISIONS.md ("Enterprise
catalog import — Non-Goals boundary respected, not silently ignored") for why
this is a scoped, user-triggered override of the PRD's "no automated
discovery" non-goal rather than a silent violation of it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import (
    CatalogProviderDep,
    CurrentUser,
    Db,
    SessionLock,
    enforce_message_rate_limit,
    enforce_rate_limit,
    get_gateway,
)

# Reused rather than re-derived — see sessions.py for why each exists.
from app.api.routers.sessions import _persist_turn, _render_user_message_history, _thread_config
from app.config import get_settings
from app.core.patch_applier import apply_patch_set
from app.core.request_intelligence import classify_user_request
from app.db.models import SessionStatus
from app.integrations import aws_session_cache
from app.integrations.aws_provider import AWSCredentials, AWSProviderError, fetch_aws_inventory, map_aws_inventory_to_patch_set
from app.integrations.catalog_provider import CatalogProviderError
from app.integrations.document_extractor import DocumentExtractionError, extract_text
from app.integrations.mapper import map_catalog_result_to_patch_set
from app.llm.gateway import LLMGateway, SessionTokenMeter
from app.orchestration.checkpointer import get_checkpointer
from app.orchestration.graph import build_discovery_graph
from app.orchestration.state import Stage
from app.security.session_lock import SessionBusyError
from app.services import session_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["integrations"])

# Matches MessageRequest.message's cap (sessions.py) — an uploaded document is
# fed through the identical conversational ingest pipeline, so the same limit
# applies for the same reason (a single ingest_patches call has a real prompt-
# size ceiling regardless of where the text came from).
_MAX_DOCUMENT_TEXT_LENGTH = 50_000


class CatalogImportRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Search query passed to the external catalog.")


class CatalogImportResponse(BaseModel):
    narration: str
    model_version: int
    applied: int
    rejected: int
    patch_results: list[dict]


@router.post(
    "/{session_id}/integrations/catalog/import",
    response_model=CatalogImportResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
async def import_from_catalog(
    session_id: uuid.UUID,
    payload: CatalogImportRequest,
    user: CurrentUser,
    db: Db,
    catalog_provider: CatalogProviderDep,
    session_lock: SessionLock,
) -> CatalogImportResponse:
    if catalog_provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="enterprise catalog integration is not configured for this deployment",
        )

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.DISCOVERY:
        # Planning consumes the frozen *accepted* model, not the mutable draft
        # this endpoint edits — importing into a session past Gate 1 would
        # silently create a ModelVersion the planning graph never reads.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"catalog import is only available during discovery — session is in '{session.status}'",
        )

    session_id_str = str(session.id)
    try:
        lock_token = await session_lock.acquire(session_id_str)
    except SessionBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another turn is already in progress for this session — wait for it to complete",
        ) from None

    try:
        try:
            fetch_result = await catalog_provider.fetch_components(query=payload.query)
        except CatalogProviderError as exc:
            logger.warning("catalog import failed for session %s: %s", session_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"enterprise catalog system error: {exc}",
            ) from exc

        patch_set = map_catalog_result_to_patch_set(fetch_result)

        model_before = await session_service.latest_model(db, session.id)
        # Re-importing an already-imported record is naturally idempotent:
        # its AddComponentPatch has the same id() every time (mapper.py's
        # external_component_id), and validate_patch rejects an
        # AddComponentPatch whose id already exists in the model — so a
        # repeated import produces REJECTED patch results, not duplicates,
        # with no separate idempotency-key mechanism needed here.
        new_model, results = apply_patch_set(model_before, patch_set)

        if new_model.version > model_before.version:
            await session_service.save_model_version(db, session.id, new_model)
            await session_service.save_patch_audit(db, session.id, results, model_before.version)
            await db.commit()

        applied = sum(1 for r in results if r.outcome == "applied")
        rejected = len(results) - applied

        return CatalogImportResponse(
            narration=patch_set.narration,
            model_version=new_model.version,
            applied=applied,
            rejected=rejected,
            patch_results=[r.model_dump(mode="json") for r in results],
        )
    finally:
        await session_lock.release(session_id_str, lock_token)


class DocumentImportResponse(BaseModel):
    narration: str | None
    questions: list[str]
    model_version: int
    error: str | None = None


@router.post(
    "/{session_id}/integrations/document/import",
    response_model=DocumentImportResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
async def import_from_document(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    session_lock: SessionLock,
    gateway: Annotated[LLMGateway, Depends(get_gateway)],
    _rate_limit: Annotated[None, Depends(enforce_message_rate_limit)],
    file: Annotated[UploadFile, File(description="A PDF, DOCX, or plain-text architecture document.")],
) -> DocumentImportResponse:
    """Extracts text from an uploaded architecture document and runs it through
    the SAME discovery graph a typed chat message uses — no new LLM prompt, no
    new extraction logic (see DECISIONS.md's "PRD-bump override" entry). The
    only new code here is turning the file into plain text; everything after
    that is the existing, already-verified conversational ingestion pipeline."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"document import is only available during discovery — session is in '{session.status}'",
        )

    content = await file.read()
    try:
        text = extract_text(file.filename or "upload", content)
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    if len(text) > _MAX_DOCUMENT_TEXT_LENGTH:
        text = text[:_MAX_DOCUMENT_TEXT_LENGTH]

    session_id_str = str(session.id)
    try:
        lock_token = await session_lock.acquire(session_id_str)
    except SessionBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another turn is already in progress for this session — wait for it to complete",
        ) from None

    try:
        model_before = await session_service.latest_model(db, session.id)
        conversation_turns = await session_service.list_conversation_turns(db, session.id)
        previous_agent_turn = await session_service.latest_conversation_turn(db, session.id, role="agent")
        request_impact = classify_user_request(text)

        await session_service.save_conversation_turn(
            db, session.id, "user", f"[Uploaded document: {file.filename}]\n\n{text}"
        )

        meter = SessionTokenMeter(get_settings().session_token_budget, already_spent=session.token_usage or 0)
        graph = build_discovery_graph(gateway, meter).compile(checkpointer=get_checkpointer())
        initial = {
            "session_id": session_id_str,
            "stage": Stage.DISCOVERY,
            "model": model_before,
            "user_message": text,
            "conversation_context": _render_user_message_history(conversation_turns),
            "previous_agent_message": previous_agent_turn.text if previous_agent_turn is not None else None,
            "request_impact": request_impact,
        }
        values = await graph.ainvoke(initial, config=_thread_config(session.langgraph_thread_id))

        await _persist_turn(db, session, meter, model_before, values)

        return DocumentImportResponse(
            narration=values.get("narration"),
            questions=values.get("pending_questions") or [],
            model_version=(values.get("model") or model_before).version,
            error=values.get("error"),
        )
    finally:
        await session_lock.release(session_id_str, lock_token)


class AWSImportRequest(BaseModel):
    access_key_id: str = Field(min_length=1)
    secret_access_key: str = Field(min_length=1)
    session_token: str | None = None
    region: str = Field(default="us-east-1", min_length=1, max_length=32)


class AWSImportResponse(BaseModel):
    narration: str
    model_version: int
    applied: int
    rejected: int
    patch_results: list[dict]


@router.post(
    "/{session_id}/integrations/cloud/aws/import",
    response_model=AWSImportResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
async def import_from_aws(
    session_id: uuid.UUID,
    payload: AWSImportRequest,
    user: CurrentUser,
    db: Db,
    session_lock: SessionLock,
) -> AWSImportResponse:
    """Read-only AWS resource-inventory import (see DECISIONS.md's "PRD-bump
    override" entry). Credentials are used once, for this call only, and are
    never written to the database — `payload` goes out of scope when this
    function returns, and nothing about it is logged."""

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AWS import is only available during discovery — session is in '{session.status}'",
        )

    session_id_str = str(session.id)
    try:
        lock_token = await session_lock.acquire(session_id_str)
    except SessionBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another turn is already in progress for this session — wait for it to complete",
        ) from None

    try:
        credentials = AWSCredentials(
            access_key_id=payload.access_key_id,
            secret_access_key=payload.secret_access_key,
            session_token=payload.session_token,
            region=payload.region,
        )
        try:
            fetch_result = await fetch_aws_inventory(credentials)
        except AWSProviderError as exc:
            # Never log `exc` at a level/sink that could echo the credentials
            # back — AWSProviderError wraps botocore's own exception message,
            # which for an auth failure names the rejected access key id but
            # never the secret; still, this stays at warning-with-session-id
            # only, matching the catalog import's own logging shape.
            logger.warning("AWS import failed for session %s", session_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AWS API error: {exc}",
            ) from exc
        finally:
            del credentials

        patch_set = map_aws_inventory_to_patch_set(fetch_result)

        model_before = await session_service.latest_model(db, session.id)
        new_model, results = apply_patch_set(model_before, patch_set)

        if new_model.version > model_before.version:
            await session_service.save_model_version(db, session.id, new_model)
            await session_service.save_patch_audit(db, session.id, results, model_before.version)
            await db.commit()

        applied = sum(1 for r in results if r.outcome == "applied")
        rejected = len(results) - applied

        return AWSImportResponse(
            narration=patch_set.narration,
            model_version=new_model.version,
            applied=applied,
            rejected=rejected,
            patch_results=[r.model_dump(mode="json") for r in results],
        )
    finally:
        await session_lock.release(session_id_str, lock_token)


class AWSConnectRequest(BaseModel):
    access_key_id: str = Field(min_length=1)
    secret_access_key: str = Field(min_length=1)
    session_token: str | None = None
    region: str = Field(default="us-east-1", min_length=1, max_length=32)


class AWSConnectResponse(BaseModel):
    connected: bool
    resource_count: int


@router.post(
    "/{session_id}/integrations/cloud/aws/connect",
    response_model=AWSConnectResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
async def connect_aws(
    session_id: uuid.UUID,
    payload: AWSConnectRequest,
    user: CurrentUser,
    db: Db,
) -> AWSConnectResponse:
    """Cloud-discovery-first (DECISIONS.md's "PRD-bump override" entry, spec §2):
    connects an AWS account for the REST of this session so later discovery
    turns can cross-reference live infrastructure facts (what a component
    actually runs on) instead of asking a question a non-technical — or even
    technical-but-didn't-build-it — user usually can't answer. Session-scoped
    only, same as the one-shot AWS import: credentials are cached in-process
    (app.integrations.aws_session_cache), never written to the database, and
    used only to fetch a read-only resource inventory. A real inventory fetch
    IS the connection test (rather than a separate lightweight call) — it
    validates the credentials and warms the cache in the same round trip.
    """

    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    if session.status != SessionStatus.DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AWS connect is only available during discovery — session is in '{session.status}'",
        )

    # Unlike the one-shot import endpoint, credentials are deliberately kept
    # (not deleted after this call) — that's the entire point of "connect":
    # caching them in-process for the rest of this session so later turns can
    # re-scan without asking the user to paste credentials again. They still
    # never reach the database (see app.integrations.aws_session_cache).
    credentials = AWSCredentials(
        access_key_id=payload.access_key_id,
        secret_access_key=payload.secret_access_key,
        session_token=payload.session_token,
        region=payload.region,
    )
    try:
        inventory = await fetch_aws_inventory(credentials)
    except AWSProviderError as exc:
        logger.warning("AWS connect failed for session %s", session_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AWS API error: {exc}") from exc

    session_id_str = str(session.id)
    aws_session_cache.connect(session_id_str, credentials)
    aws_session_cache.store_inventory(session_id_str, inventory)

    return AWSConnectResponse(connected=True, resource_count=len(inventory.resources))


@router.post("/{session_id}/integrations/cloud/aws/disconnect", dependencies=[Depends(enforce_rate_limit)])
async def disconnect_aws(session_id: uuid.UUID, user: CurrentUser, db: Db) -> dict:
    try:
        session = await session_service.get_session_for_user(db, session_id, user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from None

    was_connected = aws_session_cache.disconnect(str(session.id))
    return {"disconnected": was_connected}

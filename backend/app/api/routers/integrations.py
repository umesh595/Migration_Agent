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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CatalogProviderDep, CurrentUser, Db, SessionLock, enforce_rate_limit
from app.core.patch_applier import apply_patch_set
from app.db.models import SessionStatus
from app.integrations.catalog_provider import CatalogProviderError
from app.integrations.mapper import map_catalog_result_to_patch_set
from app.security.session_lock import SessionBusyError
from app.services import session_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["integrations"])


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

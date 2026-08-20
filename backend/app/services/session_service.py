"""Persistence + orchestration bridge. Every artifact the graph produces is written
here as a versioned row — the checkpoint is for resume, these tables are the audit
record (technique #13)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FindingRecord,
    MigrationContextRecord,
    MigrationSession,
    ModelVersion,
    PatchAuditRecord,
    PlanVersion,
    ProcessedMessage,
    ReviewQualityRecord,
    SessionStatus,
)
from app.schemas.architecture import ArchitectureModel, ModelStatus
from app.schemas.findings import Finding, ResolutionStatus
from app.schemas.migration_context import MigrationContext
from app.schemas.migration_plan import MigrationPlan, PlanStatus
from app.schemas.patches import PatchResult
from app.schemas.review_quality import ReviewQualityScore

logger = logging.getLogger(__name__)


class GateError(Exception):
    """Raised when an operation is attempted out of stage order. The API turns this
    into a 409 — gates are enforced against persisted status, not graph state."""


async def create_session(db: AsyncSession, user_id: uuid.UUID, name: str) -> MigrationSession:
    session = MigrationSession(
        user_id=user_id,
        name=name,
        status=SessionStatus.DISCOVERY,
        langgraph_thread_id=str(uuid.uuid4()),
    )
    db.add(session)
    await db.flush()

    db.add(ModelVersion(session_id=session.id, version=1, status="draft", data=ArchitectureModel().model_dump(mode="json")))
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[MigrationSession]:
    """Backs the session dashboard — without this, resuming a session days later
    (Migration — Story 9) requires the user to have bookmarked its URL, which isn't
    a real resume story."""

    result = await db.execute(
        select(MigrationSession)
        .where(MigrationSession.user_id == user_id)
        .order_by(MigrationSession.updated_at.desc())
    )
    return list(result.scalars().all())


async def claim_message(db: AsyncSession, session_id: uuid.UUID, message_id: str) -> bool:
    """FR-E6: atomically claims (session_id, message_id) via the DB unique
    constraint. Returns True if this is the first time this message_id has been
    seen for this session (caller should process the turn), False if it's a
    replay/double-submit (caller should skip re-running the graph). Commits
    immediately so the claim is visible to a concurrent request racing on the
    same message_id before either finishes the full turn."""

    db.add(ProcessedMessage(session_id=session_id, message_id=message_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def get_session_for_user(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> MigrationSession:
    result = await db.execute(
        select(MigrationSession).where(MigrationSession.id == session_id, MigrationSession.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise LookupError("session not found")
    return session


async def latest_model(db: AsyncSession, session_id: uuid.UUID) -> ArchitectureModel:
    result = await db.execute(
        select(ModelVersion).where(ModelVersion.session_id == session_id).order_by(ModelVersion.version.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return ArchitectureModel()
    return ArchitectureModel.model_validate(row.data)


async def save_model_version(db: AsyncSession, session_id: uuid.UUID, model: ArchitectureModel) -> None:
    existing = await db.execute(
        select(ModelVersion).where(ModelVersion.session_id == session_id, ModelVersion.version == model.version)
    )
    if existing.scalar_one_or_none() is not None:
        return  # idempotent: same version already persisted

    db.add(
        ModelVersion(
            session_id=session_id,
            version=model.version,
            status=str(model.status),
            data=model.model_dump(mode="json"),
        )
    )


async def save_patch_audit(
    db: AsyncSession, session_id: uuid.UUID, results: list[PatchResult], version_before: int
) -> None:
    for result in results:
        db.add(
            PatchAuditRecord(
                session_id=session_id,
                model_version_before=version_before,
                model_version_after=result.resulting_model_version,
                patch_data=result.patch.model_dump(mode="json"),
                outcome=str(result.outcome),
                reason=result.reason,
            )
        )


async def accept_model(db: AsyncSession, session: MigrationSession) -> ArchitectureModel:
    """Gate 1. Freezes the current model as accepted and advances the session."""

    if session.status != SessionStatus.DISCOVERY:
        raise GateError(f"cannot accept model: session is in '{session.status}', not discovery")

    model = await latest_model(db, session.id)
    if not model.components:
        raise GateError("cannot accept an empty architecture model — describe at least one component first")

    model.status = ModelStatus.ACCEPTED
    model.version += 1
    await save_model_version(db, session.id, model)

    session.status = SessionStatus.PLANNING
    await db.commit()
    return model


async def accepted_model(db: AsyncSession, session_id: uuid.UUID) -> ArchitectureModel:
    result = await db.execute(
        select(ModelVersion)
        .where(ModelVersion.session_id == session_id, ModelVersion.status == "accepted")
        .order_by(ModelVersion.version.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise GateError("no accepted model — gate 1 has not been passed")
    return ArchitectureModel.model_validate(row.data)


async def save_migration_context(db: AsyncSession, session_id: uuid.UUID, context: MigrationContext) -> None:
    existing = await db.execute(select(MigrationContextRecord).where(MigrationContextRecord.session_id == session_id))
    record = existing.scalar_one_or_none()
    if record is None:
        db.add(MigrationContextRecord(session_id=session_id, data=context.model_dump(mode="json")))
    else:
        record.data = context.model_dump(mode="json")


async def get_migration_context(db: AsyncSession, session_id: uuid.UUID) -> MigrationContext | None:
    result = await db.execute(select(MigrationContextRecord).where(MigrationContextRecord.session_id == session_id))
    record = result.scalar_one_or_none()
    return MigrationContext.model_validate(record.data) if record else None


async def save_plan_version(db: AsyncSession, session_id: uuid.UUID, plan: MigrationPlan) -> PlanVersion:
    existing = await db.execute(
        select(PlanVersion).where(PlanVersion.session_id == session_id, PlanVersion.version == plan.version)
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        row.data = plan.model_dump(mode="json")
        row.status = str(plan.status)
        return row

    record = PlanVersion(
        session_id=session_id,
        version=plan.version,
        status=str(plan.status),
        data=plan.model_dump(mode="json"),
    )
    db.add(record)
    await db.flush()
    return record


async def latest_plan(db: AsyncSession, session_id: uuid.UUID) -> MigrationPlan | None:
    result = await db.execute(
        select(PlanVersion).where(PlanVersion.session_id == session_id).order_by(PlanVersion.version.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    return MigrationPlan.model_validate(row.data) if row else None


async def save_findings(
    db: AsyncSession, session_id: uuid.UUID, plan_version_id: uuid.UUID | None, findings: list[Finding]
) -> None:
    for finding in findings:
        db.add(
            FindingRecord(
                session_id=session_id,
                plan_version_id=plan_version_id,
                source=str(finding.source),
                rule_id=finding.rule_id,
                severity=str(finding.severity),
                message=finding.message[:2048],
                related_component_ids=finding.related_component_ids,
                resolution_status=str(finding.resolution_status),
            )
        )


async def set_finding_resolution(
    db: AsyncSession, session_id: uuid.UUID, finding_id: uuid.UUID, resolution_status: str
) -> FindingRecord:
    """Lets a user mark a persisted finding resolved or accepted-as-risk instead of
    leaving every finding permanently 'open' — ResolutionStatus.RESOLVED and
    .ACCEPTED_AS_RISK previously had no code path that ever assigned them."""

    if resolution_status not in ResolutionStatus.values():
        raise ValueError(
            f"invalid resolution_status '{resolution_status}' — must be one of {sorted(ResolutionStatus.values())}"
        )

    result = await db.execute(
        select(FindingRecord).where(FindingRecord.id == finding_id, FindingRecord.session_id == session_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError("finding not found")

    record.resolution_status = resolution_status
    await db.commit()
    await db.refresh(record)
    return record


async def approve_plan(db: AsyncSession, session: MigrationSession) -> MigrationPlan:
    """Gate 2. Marks the reviewed plan final; export becomes available."""

    if session.status != SessionStatus.REVIEW:
        raise GateError(f"cannot approve plan: session is in '{session.status}', not review")

    plan = await latest_plan(db, session.id)
    if plan is None:
        raise GateError("no plan exists to approve")
    if plan.status == PlanStatus.DRAFT:
        raise GateError("plan has not completed review yet")

    plan.status = PlanStatus.FINAL
    plan.version += 1
    await save_plan_version(db, session.id, plan)

    session.status = SessionStatus.EXPORTED
    await db.commit()
    return plan


async def save_review_quality(db: AsyncSession, session_id: uuid.UUID, scores: list[ReviewQualityScore]) -> None:
    """Idempotent per (session, iteration) — a re-run of the same checkpoint (e.g.
    resume after disconnect) must not duplicate rows for an iteration already saved."""

    if not scores:
        return

    existing = await db.execute(
        select(ReviewQualityRecord.iteration).where(ReviewQualityRecord.session_id == session_id)
    )
    saved_iterations = {row for row in existing.scalars().all()}

    for score in scores:
        if score.iteration in saved_iterations:
            continue
        db.add(
            ReviewQualityRecord(
                session_id=session_id,
                iteration=score.iteration,
                evaluated_finding_count=score.evaluated_finding_count,
                relevance_score=score.relevance_score,
                specificity_score=score.specificity_score,
                actionability_score=score.actionability_score,
                context_awareness_score=score.context_awareness_score,
                overall_score=score.overall_score,
                rationale=score.rationale[:1024],
                flagged_issues=score.flagged_issues,
            )
        )


async def get_review_quality(db: AsyncSession, session_id: uuid.UUID) -> list[ReviewQualityRecord]:
    result = await db.execute(
        select(ReviewQualityRecord)
        .where(ReviewQualityRecord.session_id == session_id)
        .order_by(ReviewQualityRecord.iteration)
    )
    return list(result.scalars().all())

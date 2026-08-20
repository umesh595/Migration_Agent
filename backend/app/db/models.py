"""SQLAlchemy ORM models for the L0 data model (Doc 3 §2.2).

Note: LangGraph's own checkpoint tables (`checkpoints`, `checkpoint_writes`, etc.)
are managed by `PostgresSaver.setup()` in app/orchestration/checkpointer.py, not by
Alembic — they're a separate migration lifecycle owned by the langgraph library.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class SessionStatus(StrEnum):
    DISCOVERY = "discovery"
    PLANNING = "planning"
    REVIEW = "review"
    EXPORTED = "exported"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Disabled by an admin (FR-A5) rather than deleted, so sessions/audit history
    # for that user's work stays intact — deletion cascades are for project
    # deletion, not account deactivation.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # Bumped on password change and on explicit logout/revoke; every issued JWT embeds
    # the value current at issuance time, so bumping this instantly invalidates every
    # outstanding access and refresh token for this user (see security/tokens.py).
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["MigrationSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class MigrationSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), default="Untitled migration")
    status: Mapped[str] = mapped_column(String(32), default=SessionStatus.DISCOVERY)
    langgraph_thread_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="sessions")
    model_versions: Mapped[list["ModelVersion"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    plan_versions: Mapped[list["PlanVersion"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    patch_audit_records: Mapped[list["PatchAuditRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    migration_context: Mapped["MigrationContextRecord | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )
    findings: Mapped[list["FindingRecord"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    review_quality_records: Mapped[list["ReviewQualityRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_model_version_per_session"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | accepted
    data: Mapped[dict] = mapped_column(JSON, nullable=False)  # serialized ArchitectureModel
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="model_versions")


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_plan_version_per_session"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | reviewed | final
    data: Mapped[dict] = mapped_column(JSON, nullable=False)  # serialized MigrationPlan
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="plan_versions")


class PatchAuditRecord(Base):
    __tablename__ = "patch_audit_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    model_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    patch_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # applied | rejected
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="patch_audit_records")


class MigrationContextRecord(Base):
    __tablename__ = "migration_contexts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)  # serialized MigrationContext
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="migration_context")


class FindingRecord(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plan_versions.id", ondelete="CASCADE"), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # rule | llm
    rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(2048), nullable=False)
    related_component_ids: Mapped[list] = mapped_column(JSON, default=list)
    resolution_status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="findings")


class ProcessedMessage(Base):
    """Backs FR-E6 (idempotent turn processing): a client-supplied message_id is
    recorded here before the graph runs. A retried/double-submitted request with
    the same (session_id, message_id) hits the unique constraint and is rejected
    as a duplicate rather than re-running the graph and double-applying patches."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("session_id", "message_id", name="uq_processed_message_per_session"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewQualityRecord(Base):
    """Judge score over the LLM semantic critic's own output — see
    app/schemas/review_quality.py and DECISIONS.md (PRD Decision Q7 override)."""

    __tablename__ = "review_quality_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    specificity_score: Mapped[int] = mapped_column(Integer, nullable=False)
    actionability_score: Mapped[int] = mapped_column(Integer, nullable=False)
    context_awareness_score: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(String(1024), nullable=False)
    flagged_issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[MigrationSession] = relationship(back_populates="review_quality_records")

"""review quality records (LLM-as-judge, PRD Decision Q7 override)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_quality_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("iteration", sa.Integer, nullable=False),
        sa.Column("evaluated_finding_count", sa.Integer, nullable=False),
        sa.Column("relevance_score", sa.Integer, nullable=False),
        sa.Column("specificity_score", sa.Integer, nullable=False),
        sa.Column("actionability_score", sa.Integer, nullable=False),
        sa.Column("context_awareness_score", sa.Integer, nullable=False),
        sa.Column("overall_score", sa.Integer, nullable=False),
        sa.Column("rationale", sa.String(1024), nullable=False),
        sa.Column("flagged_issues", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_review_quality_records_session_id", "review_quality_records", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_review_quality_records_session_id", table_name="review_quality_records")
    op.drop_table("review_quality_records")

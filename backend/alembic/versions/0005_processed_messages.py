"""processed_messages table

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-20

FR-E6: idempotent turn processing via client-supplied message ids. Backs
sessions.post_message's duplicate-submit guard — see app/services/session_service.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "message_id", name="uq_processed_message_per_session"),
    )
    op.create_index("ix_processed_messages_session_id", "processed_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_processed_messages_session_id", table_name="processed_messages")
    op.drop_table("processed_messages")

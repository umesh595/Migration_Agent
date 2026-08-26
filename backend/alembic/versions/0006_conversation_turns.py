"""conversation_turns table

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-24

Persists what ChatPanel renders (see app/db/models.py:ConversationTurn) so a page
refresh restores the conversation instead of starting blank.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversation_turns_session_id", "conversation_turns", ["session_id"])
    op.create_index("ix_conversation_turns_created_at", "conversation_turns", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_turns_created_at", table_name="conversation_turns")
    op.drop_index("ix_conversation_turns_session_id", table_name="conversation_turns")
    op.drop_table("conversation_turns")

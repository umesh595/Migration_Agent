"""token_version on users

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-20

Backs refresh-token revocation: every JWT (access and refresh) embeds the
token_version that was current when it was issued. decode_token rejects a
token whose version doesn't match the user's current value. Bumping this
column immediately invalidates every outstanding token for that user —
without it, a stolen refresh token remained valid for its full TTL with no
way to revoke it (see DECISIONS.md).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")

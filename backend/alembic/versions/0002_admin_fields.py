"""admin fields on users

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14

FR-A5 requires admin-provisioned users (create/disable/reset) with no self-service
registration in v1. This adds the two columns that back that: is_admin (who may call
the /admin endpoints) and is_active (a disabled account, distinct from deletion — see
DECISIONS.md).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
    op.drop_column("users", "is_admin")

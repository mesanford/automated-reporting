"""add digest_subscriptions

Revision ID: bd0e1f2a3b08
Revises: ac9d0e1f2a07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bd0e1f2a3b08"
down_revision: Union[str, Sequence[str], None] = "ac9d0e1f2a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "digest_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False, index=True),
        sa.Column("user_subject", sa.String(), nullable=False, index=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("cadence", sa.String(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_send_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_send_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("workspace_id", "user_subject", name="uq_digest_workspace_user"),
    )


def downgrade() -> None:
    op.drop_table("digest_subscriptions")

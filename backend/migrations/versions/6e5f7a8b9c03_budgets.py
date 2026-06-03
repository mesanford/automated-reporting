"""add budgets

Revision ID: 6e5f7a8b9c03
Revises: 5d4e6f7a8b02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6e5f7a8b9c03"
down_revision: Union[str, Sequence[str], None] = "5d4e6f7a8b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_key", sa.String(), nullable=True),
        sa.Column("period_type", sa.String(), nullable=False),
        sa.Column("amount", sa.String(), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("alert_at_pct", sa.Integer(), nullable=True),
        sa.Column("last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("last_alert_period", sa.String(), nullable=True),
        sa.Column("created_by_subject", sa.String(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("budgets")

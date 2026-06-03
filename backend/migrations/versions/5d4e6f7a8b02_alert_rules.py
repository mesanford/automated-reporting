"""add alert_rules

Revision ID: 5d4e6f7a8b02
Revises: 4c3d5e6f7a01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5d4e6f7a8b02"
down_revision: Union[str, Sequence[str], None] = "4c3d5e6f7a01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("comparison", sa.String(), nullable=False),
        sa.Column("threshold", sa.String(), nullable=False),
        sa.Column(
            "channels",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_subject", sa.String(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
        sa.Column("last_value", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("alert_rules")

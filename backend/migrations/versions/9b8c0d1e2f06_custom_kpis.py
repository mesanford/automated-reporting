"""add custom_kpis

Revision ID: 9b8c0d1e2f06
Revises: 8a7b9c0d1e05
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9b8c0d1e2f06"
down_revision: Union[str, Sequence[str], None] = "8a7b9c0d1e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_kpis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="number"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_subject", sa.String(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("custom_kpis")

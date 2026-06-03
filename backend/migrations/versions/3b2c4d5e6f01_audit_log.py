"""add audit_log

Append-only table that records consequential workspace actions
(optimization approve/execute, connection delete, member changes, …).
Used by the role-gated mutations and the GET /workspaces/{id}/audit-log
endpoint.

Revision ID: 3b2c4d5e6f01
Revises: 2a1f3c4e5b6d
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b2c4d5e6f01"
down_revision: Union[str, Sequence[str], None] = "2a1f3c4e5b6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("actor_subject", sa.String(), nullable=False, index=True),
        sa.Column("action", sa.String(), nullable=False, index=True),
        sa.Column("target_type", sa.String(), nullable=True),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_log")

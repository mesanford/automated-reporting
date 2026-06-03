"""add base_currency + fx_rates

Revision ID: 7f6a8b9c0d04
Revises: 6e5f7a8b9c03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7f6a8b9c0d04"
down_revision: Union[str, Sequence[str], None] = "6e5f7a8b9c03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Workspace base currency. Default 'USD' for backward compat.
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column(
            "base_currency", sa.String(), nullable=False, server_default="USD",
        ))

    # 2. FX rate cache.
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("as_of_date", sa.String(), nullable=False, index=True),
        sa.Column("from_currency", sa.String(), nullable=False, index=True),
        sa.Column("to_currency", sa.String(), nullable=False, index=True),
        sa.Column("rate", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint(
            "as_of_date", "from_currency", "to_currency",
            name="uq_fx_rate_day_pair",
        ),
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("base_currency")

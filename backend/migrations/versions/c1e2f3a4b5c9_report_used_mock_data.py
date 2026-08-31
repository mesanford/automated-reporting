"""add reports.used_mock_data

Revision ID: c1e2f3a4b5c9
Revises: bd0e1f2a3b08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1e2f3a4b5c9"
down_revision: Union[str, Sequence[str], None] = "bd0e1f2a3b08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("used_mock_data", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("reports", "used_mock_data")

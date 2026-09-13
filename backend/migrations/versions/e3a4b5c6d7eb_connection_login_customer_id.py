"""Per-connection Google Ads manager context.

login-customer-id was previously read from a process-wide env var, which meant
every tenant's API calls ran through whichever manager account the host was
configured for. It belongs to the connection.

Revision ID: e3a4b5c6d7eb
Revises: d2f3a4b5c6da
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3a4b5c6d7eb"
down_revision: Union[str, Sequence[str], None] = "d2f3a4b5c6da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("login_customer_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "login_customer_id")

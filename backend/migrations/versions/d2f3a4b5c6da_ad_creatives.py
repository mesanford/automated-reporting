"""add ad_creatives

Ports the standalone creatives gallery's Firestore `ad_creatives` collection
into Postgres, workspace-scoped.

Revision ID: d2f3a4b5c6da
Revises: c1e2f3a4b5c9
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2f3a4b5c6da"
down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b5c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ad_creatives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("connection_id", sa.Integer(), nullable=True, index=True),
        sa.Column("platform", sa.String(), nullable=False, index=True),
        sa.Column("ad_id", sa.String(), nullable=False, index=True),
        sa.Column("account_id", sa.String(), nullable=True, index=True),
        sa.Column("ad_name", sa.String(), nullable=True),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("ad_text", sa.Text(), nullable=True),
        sa.Column("campaign_name", sa.String(), nullable=True),
        sa.Column("ad_group_name", sa.String(), nullable=True),
        sa.Column("creative_type", sa.String(), nullable=True),
        sa.Column("group_type", sa.String(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("source_asset_url", sa.Text(), nullable=True),
        sa.Column("asset_path", sa.String(), nullable=True),
        sa.Column("asset_content_type", sa.String(), nullable=True),
        sa.Column("embed_url", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(), nullable=True, index=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("review_updated_at", sa.DateTime(), nullable=True),
        sa.Column("review_updated_by", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            index=True,
        ),
        sa.UniqueConstraint(
            "workspace_id", "platform", "ad_id", name="uq_ad_creatives_ws_platform_ad"
        ),
    )


def downgrade() -> None:
    op.drop_table("ad_creatives")

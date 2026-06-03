"""add workspaces

Creates the multi-workspace tables (users, workspaces, memberships, invites),
adds workspace_id to every tenant-scoped table, backfills one Personal
workspace per distinct user_id seen in any existing table, then enforces
NOT NULL on workspace_id.

Idempotent w.r.t. existing data: legacy SQLite databases (where rows already
exist with user_id strings) are migrated in place. Fresh databases skip the
backfill cleanly.

Revision ID: 2a1f3c4e5b6d
Revises: 159a3ecc4f74
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2a1f3c4e5b6d"
down_revision: Union[str, Sequence[str], None] = "159a3ecc4f74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that need a workspace_id column.
SCOPED_TABLES: list[str] = [
    "sync_jobs",
    "reports",
    "connections",
    "user_settings",
    "optimization_plans",
    "optimization_rules",
    "conversations",
]


def upgrade() -> None:
    # 1. New tables ─────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("auth_subject", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(), unique=True, index=True),
        sa.Column("name", sa.String()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("created_by_subject", sa.String(), index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_subject", sa.String(), nullable=False, index=True),
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("workspace_id", "user_subject", name="uq_membership_workspace_user"),
    )

    op.create_table(
        "invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, index=True),
        sa.Column("invited_by_subject", sa.String()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )

    # 2. Add workspace_id (nullable for now) to every tenant-scoped table ──
    for table in SCOPED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))
            batch.create_index(f"ix_{table}_workspace_id", ["workspace_id"])

    # Conversations also gets a `visibility` column with a sane default.
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(
            sa.Column("visibility", sa.String(), nullable=False, server_default="private")
        )

    # 3. Backfill — one Personal workspace per distinct user_id string ─────
    bind = op.get_bind()

    distinct_subjects = bind.execute(sa.text(_DISTINCT_SUBJECTS_SQL)).fetchall()
    for (subject,) in distinct_subjects:
        if not subject:
            continue

        # Insert User (placeholder email — real email arrives on next sign-in).
        bind.execute(
            sa.text(
                "INSERT INTO users (auth_subject, email) "
                "VALUES (:subj, :email)"
            ),
            {"subj": subject, "email": f"{subject}@placeholder.local"},
        )

        # Insert one Personal workspace, slug guaranteed unique via auth_subject.
        result = bind.execute(
            sa.text(
                "INSERT INTO workspaces (name, slug, created_by_subject) "
                "VALUES ('Personal', :slug, :subj) "
                "RETURNING id"
                if bind.dialect.name == "postgresql"
                else "INSERT INTO workspaces (name, slug, created_by_subject) "
                     "VALUES ('Personal', :slug, :subj)"
            ),
            {"slug": f"personal-{subject}", "subj": subject},
        )

        if bind.dialect.name == "postgresql":
            workspace_id = result.scalar()
        else:
            workspace_id = bind.execute(
                sa.text("SELECT id FROM workspaces WHERE slug = :slug"),
                {"slug": f"personal-{subject}"},
            ).scalar()

        # Owner membership.
        bind.execute(
            sa.text(
                "INSERT INTO memberships (workspace_id, user_subject, role) "
                "VALUES (:wid, :subj, 'owner')"
            ),
            {"wid": workspace_id, "subj": subject},
        )

        # Fill workspace_id on every row that belongs to this subject.
        for table in SCOPED_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET workspace_id = :wid WHERE user_id = :subj"),
                {"wid": workspace_id, "subj": subject},
            )

    # 4. Enforce NOT NULL now that backfill is complete ───────────────────
    for table in SCOPED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("workspace_id", nullable=False)

    # 5. user_settings: UNIQUE moves from user_id to workspace_id ──────────
    with op.batch_alter_table("user_settings") as batch:
        # SQLite-safe drop of the old unique index (named ix_user_settings_user_id).
        try:
            batch.drop_index("ix_user_settings_user_id")
        except Exception:
            pass
        batch.create_index("ix_user_settings_user_id", ["user_id"])
        batch.create_unique_constraint(
            "uq_user_settings_workspace", ["workspace_id"]
        )


# Pull every distinct user_id string from each tenant-scoped table.
_DISTINCT_SUBJECTS_SQL = (
    "SELECT DISTINCT user_id FROM (\n"
    + "\n  UNION\n".join(
        f"  SELECT user_id FROM {t} WHERE user_id IS NOT NULL" for t in SCOPED_TABLES
    )
    + "\n) t"
)


def downgrade() -> None:
    # Drop workspace_id from each scoped table.
    for table in SCOPED_TABLES:
        with op.batch_alter_table(table) as batch:
            try:
                batch.drop_index(f"ix_{table}_workspace_id")
            except Exception:
                pass
            batch.drop_column("workspace_id")

    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("visibility")

    with op.batch_alter_table("user_settings") as batch:
        try:
            batch.drop_constraint("uq_user_settings_workspace", type_="unique")
        except Exception:
            pass

    op.drop_table("invites")
    op.drop_table("memberships")
    op.drop_table("workspaces")
    op.drop_table("users")

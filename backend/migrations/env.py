"""Alembic environment.

Reads the database URL from the same place the app does (`DATABASE_URL` env
var, falling back to local SQLite), and registers `models.Base.metadata` as
the target for autogenerate.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app.*` importable when running `alembic` from backend/.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import models  # noqa: E402,F401  (import for side effect: registers tables)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolution order for the DB URL:
#   1. URL already set on the Alembic config by the caller (e.g. main.py
#      passes `cfg.set_main_option("sqlalchemy.url", ...)` before invoking
#      command.upgrade). Keep it.
#   2. Otherwise, fall back to what the app uses at runtime — `DATABASE_URL`
#      env var via app.database.SQLALCHEMY_DATABASE_URL.
_existing_url = config.get_main_option("sqlalchemy.url") or ""
if not _existing_url or _existing_url.startswith("driver://"):
    from app.database import SQLALCHEMY_DATABASE_URL  # noqa: E402

    config.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)

target_metadata = models.Base.metadata


def _render_item(type_, obj, autogen_context):
    """Render JSONB types using the SQLAlchemy postgresql dialect import."""
    if type_ == "type" and obj.__class__.__name__ == "JSONB":
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.JSONB(astext_type=sa.Text())"
    return False


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        url = str(connection.engine.url)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

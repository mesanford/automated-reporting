from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.engine import Engine
from pathlib import Path

import logging
import os
from dotenv import load_dotenv

logger = logging.getLogger("antigravity")

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "antigravity.db"
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# Inject DB_PASSWORD into the URL if it's set and the URL doesn't already
# carry one. Cloud Run mounts secrets as env vars, but SQLAlchemy URLs don't
# auto-interpolate — so the deploy script ships a passwordless URL plus a
# DB_PASSWORD secret, and we stitch them together here.
_db_password = os.getenv("DB_PASSWORD")
if _db_password and "://" in SQLALCHEMY_DATABASE_URL and "@" in SQLALCHEMY_DATABASE_URL:
    from urllib.parse import quote
    scheme, rest = SQLALCHEMY_DATABASE_URL.split("://", 1)
    userinfo, hostpart = rest.split("@", 1)
    if ":" not in userinfo:
        SQLALCHEMY_DATABASE_URL = f"{scheme}://{userinfo}:{quote(_db_password, safe='')}@{hostpart}"

is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine_kwargs: dict = {"connect_args": connect_args}
if not is_sqlite:
    engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
        pool_pre_ping=True,
    )

engine = create_engine(SQLALCHEMY_DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base. Using the class-based DeclarativeBase
    (vs. the legacy declarative_base() factory) is what lets models.py use
    Mapped[]/mapped_column() with full mypy inference instead of every
    column typing as Column[T] at the class level."""

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_schema_compat(engine: Engine) -> None:
    """Add missing columns for existing SQLite tables used in local dev.

    SQLite's create_all() won't alter existing tables, so this keeps older
    local DB files compatible with new model fields.

    IMPORTANT — this is a local-dev convenience, not a substitute for
    Alembic. It only runs against SQLite, never Postgres, and its column
    list is a manually-maintained duplicate of what should already exist in
    an Alembic migration. If it patches a column here, that same column
    change needs its own migration in migrations/versions/ or Postgres
    environments (staging/prod) will be missing it — this function will
    NOT save you there. Every time this fires, a warning is logged so a
    forgotten migration doesn't go unnoticed.
    """
    if engine.dialect.name != "sqlite":
        return

    reports_column_ddl = {
        "scorecard_deltas": "JSON",
        "platform_deltas": "JSON",
        "comparison_type": "VARCHAR DEFAULT 'none'",
        "current_period_label": "VARCHAR",
        "prior_period_label": "VARCHAR",
        "platform_summary": "JSON",
        "hierarchy_summary": "JSON",
        "top_performer": "JSON",
        "bottom_performer": "JSON",
    }

    connections_column_ddl = {
        "available_accounts": "JSON",
        "selected_account_ids": "JSON",
        "last_sync_at": "TIMESTAMP",
        "last_sync_status": "VARCHAR",
        "last_sync_job_id": "INTEGER",
    }

    with engine.begin() as conn:
        table_exists = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reports'"
        ).first()
        if not table_exists:
            return

        existing_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(reports)").fetchall()
        }

        for col, ddl in reports_column_ddl.items():
            if col not in existing_columns:
                conn.exec_driver_sql(f"ALTER TABLE reports ADD COLUMN {col} {ddl}")
                logger.warning(
                    "ensure_sqlite_schema_compat: patched missing column "
                    "reports.%s onto local SQLite DB. Confirm a matching "
                    "Alembic migration exists, or Postgres environments "
                    "will be missing this column.",
                    col,
                )

        connections_exists = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='connections'"
        ).first()
        if not connections_exists:
            return

        connection_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(connections)").fetchall()
        }

        for col, ddl in connections_column_ddl.items():
            if col not in connection_columns:
                conn.exec_driver_sql(f"ALTER TABLE connections ADD COLUMN {col} {ddl}")
                logger.warning(
                    "ensure_sqlite_schema_compat: patched missing column "
                    "connections.%s onto local SQLite DB. Confirm a matching "
                    "Alembic migration exists, or Postgres environments "
                    "will be missing this column.",
                    col,
                )

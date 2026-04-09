from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Engine
from pathlib import Path

import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "antigravity.db"
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

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

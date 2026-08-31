import os
from pathlib import Path

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from sqlalchemy import inspect as sqlalchemy_inspect

from app.observability import configure_observability
from app.ratelimit import limiter

configure_observability()
logger = logging.getLogger("antigravity")

from app.api import activity, alerts, budgets, chat, creatives, digests, endpoints, internal, kpis, oauth, optimizations, schedules, share, views, workspaces
from app.services import task_handlers  # noqa: F401  (registers task handlers on import)
from app.database import SQLALCHEMY_DATABASE_URL, engine, ensure_sqlite_schema_compat
from app import models


BACKEND_ROOT = Path(__file__).resolve().parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def _initialize_database() -> None:
    """Bring the schema up to date.

    Preferred path: run Alembic migrations to head. If the running database
    predates Alembic (tables exist but `alembic_version` does not), stamp it
    at head first so the existing data is preserved.
    """
    inspector = sqlalchemy_inspect(engine)
    existing_tables = set(inspector.get_table_names())

    if ALEMBIC_INI.exists():
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(ALEMBIC_INI))
            cfg.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)

            legacy_db = "reports" in existing_tables and "alembic_version" not in existing_tables
            if legacy_db:
                command.stamp(cfg, "head")
            command.upgrade(cfg, "head")
            return
        except Exception as exc:  # noqa: BLE001
            # Fall through to create_all so the app at least boots on a fresh
            # install where Alembic deps haven't been installed yet.
            print(f"[startup] Alembic upgrade failed ({exc}); falling back to create_all.")

    missing_tables = [
        table for table in models.Base.metadata.sorted_tables
        if table.name not in existing_tables
    ]
    if missing_tables:
        models.Base.metadata.create_all(bind=engine, tables=missing_tables)


_initialize_database()
ensure_sqlite_schema_compat(engine)

# Preflight: refuse to boot in production with dangerous configs.
# `app.preflight` keeps the rules in one place; results are also surfaced
# through /readyz for ops visibility.
from app.preflight import run_or_raise  # noqa: E402

_preflight_report = run_or_raise()

app = FastAPI(title="Antigravity API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi's handler is typed for RateLimitExceeded specifically; Starlette's stub wants Callable[..., Exception] generically — a stub mismatch between the two libraries, not a real type error.


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    # Log with stack so Cloud Error Reporting groups it as a real error.
    logger.exception(
        "unhandled-exception",
        extra={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
    )


def _cors_origins() -> list[str]:
    """Comma-split CORS_ALLOWED_ORIGINS; default to localhost dev only.

    Wildcard origins are not safe with `allow_credentials=True` (browsers
    reject the combination per the CORS spec). Always be explicit.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-Id", "X-Workspace-Id"],
)


# Cap request body size. Cloud Run already enforces a 32 MiB request limit
# on streaming uploads, but we want a tighter, app-level guard with a clean
# 413 response — and we want it before the route reads anything off the wire.
MAX_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(25 * 1024 * 1024)))  # 25 MiB


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"request body exceeds {MAX_BODY_BYTES} bytes"},
                    )
            except ValueError:
                pass
    return await call_next(request)


@app.get("/")
async def root():
    return {"message": "Antigravity API is running"}


@app.get("/healthz")
async def healthz():
    """Cloud Run / liveness probe. Cheap — does not touch the DB."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness probe. Verifies the DB is reachable AND reports which
    subsystems are wired (auth, secrets, KMS, email, queue) so ops can
    see at a glance whether a deploy got the right env. Startup warnings
    are surfaced too — useful for catching "we shipped with dev auth on"
    type mistakes that don't fail boot but should be obvious."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=f"db unreachable: {exc}")

    subsystems = {f.code: f.message for f in _preflight_report.infos}
    warnings = [
        {"code": f.code, "message": f.message}
        for f in _preflight_report.warnings
    ]
    return {
        "status": "ready",
        "is_production": _preflight_report.is_production,
        "subsystems": subsystems,
        "warnings": warnings,
    }


app.include_router(endpoints.router, prefix="/api")
app.include_router(oauth.router, prefix="/api/auth", tags=["auth"])
app.include_router(optimizations.router, prefix="/api/optimizations", tags=["optimizations"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(internal.router, prefix="/api/internal", tags=["internal"])
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(schedules.router, prefix="/api", tags=["schedules"])
app.include_router(alerts.router, prefix="/api", tags=["alerts"])
app.include_router(budgets.router, prefix="/api", tags=["budgets"])
app.include_router(activity.router, prefix="/api", tags=["activity"])
app.include_router(views.router, prefix="/api", tags=["views"])
app.include_router(kpis.router, prefix="/api", tags=["kpis"])
app.include_router(share.router, prefix="/api", tags=["share"])
app.include_router(digests.router, prefix="/api", tags=["digests"])
app.include_router(creatives.router, prefix="/api", tags=["creatives"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

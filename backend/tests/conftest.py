"""Shared fixtures for the backend test suite.

Each test session gets a fresh isolated SQLite file. The app's database
URL is overridden before `main` is imported so SQLAlchemy and Alembic both
target the test DB. `ALLOW_DEV_AUTH=1` is set so tests can mint identities
via the `X-User-Id` header without standing up Firebase.

`auth_headers(user_id, workspace_id=None)` is the standard way to build
request headers — keeps tests free of repeated dict-spreading boilerplate.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator, Optional

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


def _stub_module(name: str, **attrs) -> None:
    """Insert a fake module into sys.modules so imports succeed."""
    import types

    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# The platform-connector code imports heavy SDKs (google-ads, bingads) at
# module load time. We don't exercise those paths in tests, and on some
# Pythons (3.14) google-ads fails to import outright. Stub them so the
# `main` import chain succeeds.
class _StubClass:
    def __init__(self, *_, **__):
        pass

    def __getattr__(self, _name):  # any attribute access is a no-op stub
        return _StubClass()


def _stub_package(name: str, **attrs) -> None:
    """Stub a module and mark it as a package so submodule imports work."""
    _stub_module(name, **attrs)
    sys.modules[name].__path__ = []  # makes it act like a package


_stub_package("google.ads")
_stub_package("google.ads.googleads")
_stub_module("google.ads.googleads.client", GoogleAdsClient=_StubClass)
_stub_module("google.ads.googleads.errors", GoogleAdsException=Exception)
_stub_package("bingads")
_stub_module(
    "bingads.authorization",
    AuthorizationData=_StubClass,
    OAuthDesktopMobileAuthCodeGrant=_StubClass,
    OAuthTokens=_StubClass,
)
_stub_package("bingads.v13")
_stub_module(
    "bingads.v13.reporting",
    ReportingServiceManager=_StubClass,
    ReportingDownloadParameters=_StubClass,
)
_stub_module("bingads.service_client", ServiceClient=_StubClass)


@pytest.fixture(scope="session")
def _test_db_url() -> Iterator[str]:
    """Session-scoped temp SQLite DB, cleaned up at the end."""
    fd, path = tempfile.mkstemp(suffix="-test.db", prefix="antigravity-")
    os.close(fd)
    url = f"sqlite:///{path}"
    yield url
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(scope="session", autouse=True)
def _configure_env(_test_db_url: str) -> None:
    """Wire env vars before `main` is imported."""
    os.environ["DATABASE_URL"] = _test_db_url
    os.environ["ALLOW_DEV_AUTH"] = "1"
    # Make sure Cloud Tasks paths fall back to in-process during tests.
    os.environ.pop("CLOUD_TASKS_QUEUE", None)
    # Keep secret resolution off the real GCP project.
    os.environ.pop("GCP_PROJECT_ID", None)


@pytest.fixture(scope="session")
def app(_configure_env):
    """Import the FastAPI app after env is configured."""
    import main  # noqa: WPS433
    return main.app


@pytest.fixture()
def client(app):
    """Per-test TestClient. Cheap to construct."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    """Open a real SQLAlchemy session for direct DB manipulation in tests."""
    from app.database import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _reset_db_between_tests(app):
    """Truncate every app table after each test.

    The session-scoped DB is *correct* for the schema (Alembic ran once),
    but row-level state leaks between tests — e.g. workspaces and
    connections from earlier tests pile up and pollute count-based
    assertions. Truncating in dependency-safe reverse order is fast
    (~1ms per test) and gives every test a clean slate without forcing
    each one to run the full Alembic upgrade.
    """
    yield

    from app.database import engine
    from app import models

    with engine.begin() as conn:
        # Reverse-sorted so FK children go before parents.
        for table in reversed(models.Base.metadata.sorted_tables):
            if table.name == "alembic_version":
                continue
            conn.execute(table.delete())


def auth_headers(user_id: str, workspace_id: Optional[int] = None) -> dict[str, str]:
    """Build the headers a test caller would send. Importable from any test."""
    h: dict[str, str] = {"X-User-Id": user_id}
    if workspace_id is not None:
        h["X-Workspace-Id"] = str(workspace_id)
    return h


# Make the helper importable as `from conftest import auth_headers` OR as
# a pytest fixture via `auth_headers` arg.
@pytest.fixture()
def headers():
    return auth_headers


@pytest.fixture()
def alice(request) -> str:
    """Per-test unique user id for 'Alice'. Avoids cross-test contamination
    in the session-scoped DB (Alice from test A would otherwise still own
    workspaces in test B and pollute membership lookups)."""
    return f"alice-{request.node.name}"


@pytest.fixture()
def bob(request) -> str:
    return f"bob-{request.node.name}"


@pytest.fixture()
def carol(request) -> str:
    return f"carol-{request.node.name}"

"""Retired endpoints return 410 Gone with a `replacement` pointer.

Three endpoints have been removed in favor of safer/scalable variants:
- `POST /api/sync/{id}` → `/api/sync/{id}/enqueue` + polling
- `POST /api/sync-all` → `/api/sync-all/enqueue` + polling
- `GET /api/auth/{platform}/login` (production) → `POST /api/auth/oauth/start`

The legacy /login is still alive when `ALLOW_DEV_AUTH=1` because the dev
flow has no Bearer token to exchange, but it must 410 outside dev so
real Firebase deploys can't accidentally rely on it.
"""
import os

import pytest

from tests.conftest import auth_headers


def test_sync_single_returns_410(client, alice):
    r = client.post("/api/sync/42", json={}, headers=auth_headers(alice))
    assert r.status_code == 410
    detail = r.json()["detail"]
    assert detail["replacement"] == "/api/sync/42/enqueue"
    assert "enqueue" in detail["message"]


def test_sync_all_returns_410(client, alice):
    r = client.post("/api/sync-all", json={}, headers=auth_headers(alice))
    assert r.status_code == 410
    detail = r.json()["detail"]
    assert detail["replacement"] == "/api/sync-all/enqueue"


def test_oauth_login_returns_410_outside_dev_mode(client, monkeypatch):
    """Production-style: `ALLOW_DEV_AUTH` unset → legacy /login is gone."""
    monkeypatch.delenv("ALLOW_DEV_AUTH", raising=False)
    r = client.get("/api/auth/google/login")
    assert r.status_code == 410
    detail = r.json()["detail"]
    assert detail["replacement"] == "/api/auth/oauth/start"


def test_oauth_login_still_works_in_dev_mode(client, monkeypatch):
    """Dev path: `ALLOW_DEV_AUTH=1` keeps the legacy endpoint alive so
    contributors can connect platforms locally without a Firebase
    project. Stub the client_id so we exercise just the redirect path."""
    monkeypatch.setenv("ALLOW_DEV_AUTH", "1")
    from app.api import oauth as oauth_module

    monkeypatch.setattr(oauth_module, "_platform_client_id", lambda p: "dev-client-id")

    # follow_redirects=False so we observe the 30x rather than the upstream
    # OAuth provider's response.
    r = client.get("/api/auth/google/login", follow_redirects=False)
    assert r.status_code == 307
    assert "accounts.google.com" in r.headers["location"]

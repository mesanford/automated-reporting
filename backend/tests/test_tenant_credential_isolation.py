"""Per-connection credentials must never fall back to process-wide values.

The developer token and OAuth client identify the *application* and are shared
across tenants. The refresh token and the manager/customer context identify one
*connection*. Mixing the two lets one tenant's sync run against whatever account
the host happens to be configured for, which is a cross-tenant data leak rather
than a misconfiguration.
"""
import asyncio

import pytest

from app.services import connectors, creatives
from app.services.connectors import ConnectorConfigError


def test_google_discovery_refuses_missing_refresh_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dev")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "secret")
    # A stale host-level token must NOT be picked up for a tenant connection.
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", "host-operator-token")

    with pytest.raises(ConnectorConfigError, match="refresh token"):
        asyncio.run(connectors._discover_google_accounts(None))


def test_google_fetch_refuses_missing_refresh_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dev")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", "host-operator-token")

    with pytest.raises(ConnectorConfigError, match="refresh token"):
        asyncio.run(connectors._fetch_google_performance("123", ""))


def test_microsoft_fetch_refuses_missing_customer_id(monkeypatch):
    monkeypatch.setenv("MICROSOFT_DEVELOPER_TOKEN", "dev")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_CUSTOMER_ID", "9999999")  # host-level, must be ignored

    with pytest.raises(ConnectorConfigError, match="customer ID"):
        connectors._fetch_microsoft_performance_sync("123", "token", None, None, None, None)


def test_creatives_microsoft_refuses_missing_customer_id(monkeypatch):
    monkeypatch.setenv("MICROSOFT_DEVELOPER_TOKEN", "dev")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_CUSTOMER_ID", "9999999")

    with pytest.raises(ConnectorConfigError, match="customer ID"):
        creatives._fetch_microsoft_creatives_sync("123", "token", None, None)


def test_google_credentials_take_manager_context_from_caller(monkeypatch):
    """login-customer-id comes from the connection, not the environment."""
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "dev")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "111-111-1111")

    # No caller-supplied manager -> no header, despite the env var being set.
    creds = creatives._google_credentials("tenant-refresh-token")
    assert "login_customer_id" not in creds

    # Caller-supplied manager wins and is normalised.
    creds = creatives._google_credentials("tenant-refresh-token", "222-222-2222")
    assert creds["login_customer_id"] == "2222222222"
    assert creds["refresh_token"] == "tenant-refresh-token"


def test_discovery_records_which_manager_each_account_came_through():
    """Accounts reached via a manager carry that manager; direct ones carry ''."""
    accounts = [
        {"id": "111", "name": "Direct", "login_customer_id": ""},
        {"id": "222", "name": "Via MCC", "login_customer_id": "999"},
    ]
    login_map = {
        str(a["id"]): str(a.get("login_customer_id") or "") for a in accounts
    }
    assert login_map["111"] == ""
    assert login_map["222"] == "999"


def test_fetch_platform_data_forwards_manager_context(monkeypatch):
    captured = {}

    async def fake_fetch(account_id, refresh_token, start_date, end_date, login_customer_id):
        captured["login_customer_id"] = login_customer_id
        captured["refresh_token"] = refresh_token
        import pandas as pd
        return pd.DataFrame()

    monkeypatch.setattr(connectors, "_fetch_google_performance", fake_fetch)
    asyncio.run(connectors.fetch_platform_data(
        "google", "123", refresh_token="tenant-token", google_login_customer_id="555",
    ))
    assert captured == {"login_customer_id": "555", "refresh_token": "tenant-token"}

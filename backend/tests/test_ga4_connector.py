"""GA4 connector (see docs/organic-and-ga4-integration-plan.md, Part B).

Mocks the google-analytics-data / google-analytics-admin client classes
directly rather than hitting real Google APIs — verifies the connector's
request construction and response mapping, not that GA4's API shape is
still current. Re-verify against a live GA4 property before trusting this
in production.
"""
from types import SimpleNamespace

import pytest

from app.services import connectors
from app.services.connectors import ConnectorConfigError


@pytest.fixture()
def fake_credentials(monkeypatch):
    """Bypass the real OAuth client_id/secret requirement and Credentials
    construction — used by tests that care about request/response mapping,
    not token refresh plumbing."""
    monkeypatch.setattr(connectors, "_google_analytics_credentials", lambda refresh_token: SimpleNamespace())


def _metric_value(v):
    return SimpleNamespace(value=str(v))


def _fake_report_row(date_str: str, channel: str, sessions: int, engaged: int, conversions: float, revenue: float):
    return SimpleNamespace(
        dimension_values=[SimpleNamespace(value=date_str), SimpleNamespace(value=channel)],
        metric_values=[
            _metric_value(sessions),
            _metric_value(engaged),
            _metric_value(conversions),
            _metric_value(revenue),
        ],
    )


async def test_fetch_google_analytics_maps_organic_sessions(monkeypatch, fake_credentials):
    fake_response = SimpleNamespace(rows=[
        _fake_report_row("20260105", "Organic Search", 500, 300, 12.0, 240.0),
        _fake_report_row("20260106", "Organic Social", 200, 90, 3.0, 60.0),
    ])

    class _FakeDataClient:
        def __init__(self, credentials=None):
            pass

        def run_report(self, request):
            return fake_response

    monkeypatch.setattr(
        "google.analytics.data_v1beta.BetaAnalyticsDataClient", _FakeDataClient
    )

    df = await connectors._fetch_google_analytics("123456", "refresh-token", "2026-01-01", "2026-01-31")

    assert not df.empty
    assert len(df) == 2
    assert set(df["platform"]) == {"google_analytics"}
    row0 = df.iloc[0]
    assert row0["date"] == "2026-01-05"
    assert row0["campaign"] == "Organic Search"
    assert row0["ga_sessions"] == 500
    assert row0["organic_reach"] == 500
    assert row0["organic_engagements"] == 300
    assert row0["conversions"] == 12.0
    assert row0["revenue"] == 240.0
    assert row0["spend"] == 0.0
    assert row0["_is_mock_data"] == False  # noqa: E712


async def test_google_analytics_credentials_requires_refresh_token(monkeypatch):
    # Exercise the real _google_analytics_credentials path (not the
    # autouse bypass) with a real client_id/secret but no refresh_token,
    # to isolate the refresh_token check specifically.
    from app.services import secrets_manager

    monkeypatch.setattr(
        secrets_manager, "get_secret",
        lambda name: "fake-value" if name in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET") else None,
    )
    with pytest.raises(ConnectorConfigError, match="refresh token"):
        connectors._google_analytics_credentials(None)


async def test_discover_google_analytics_properties(monkeypatch, fake_credentials):
    fake_summary = SimpleNamespace(
        property_summaries=[
            SimpleNamespace(property="properties/999", display_name="Acme Corp Site"),
        ]
    )

    class _FakeAdminClient:
        def __init__(self, credentials=None):
            pass

        def list_account_summaries(self):
            return [fake_summary]

    monkeypatch.setattr(
        "google.analytics.admin_v1beta.AnalyticsAdminServiceClient", _FakeAdminClient
    )

    accounts = await connectors._discover_google_analytics_properties("refresh-token")
    assert accounts == [{"id": "999", "name": "Acme Corp Site", "status": "ACTIVE", "currency": "N/A"}]

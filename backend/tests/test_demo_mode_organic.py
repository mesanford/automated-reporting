"""Facebook/Instagram/LinkedIn Organic connectors have no real API
integration yet (see docs/organic-and-ga4-integration-plan.md). They used to
silently return fabricated data regardless of environment or whether a real
access token was present. These tests lock in the fix: fabricated data
requires an explicit DEMO_MODE=1 opt-in, is always refused in production,
and is flagged via `usedMockData` when it is used.
"""
import pytest

from app.services import connectors
from app.services.connectors import ConnectorConfigError


@pytest.fixture(autouse=True)
def _clean_demo_env(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)


@pytest.mark.parametrize(
    "fetch_fn",
    [
        connectors._fetch_facebook_organic,
        connectors._fetch_instagram_organic,
        connectors._fetch_linkedin_organic,
    ],
)
async def test_organic_fetch_refuses_without_demo_mode(fetch_fn):
    with pytest.raises(ConnectorConfigError):
        await fetch_fn("acct-1", "", "2026-01-01", "2026-01-31")


@pytest.mark.parametrize(
    "fetch_fn",
    [
        connectors._fetch_facebook_organic,
        connectors._fetch_instagram_organic,
        connectors._fetch_linkedin_organic,
    ],
)
async def test_organic_fetch_refuses_in_production_even_with_demo_mode(fetch_fn, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConnectorConfigError):
        await fetch_fn("acct-1", "", "2026-01-01", "2026-01-31")


@pytest.mark.parametrize(
    "fetch_fn",
    [
        connectors._fetch_facebook_organic,
        connectors._fetch_instagram_organic,
        connectors._fetch_linkedin_organic,
    ],
)
async def test_organic_fetch_allows_explicit_demo_mode(fetch_fn, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    df = await fetch_fn("acct-1", "", "2026-01-01", "2026-01-07")
    assert not df.empty
    assert "_is_mock_data" in df.columns
    assert bool(df["_is_mock_data"].all())


def test_aggregate_data_surfaces_used_mock_data_flag():
    from app.services import etl

    # 30-day window makes an all-empty draw astronomically unlikely (each day
    # independently has a 60% chance of producing a row), so this shouldn't
    # need a skip like a narrower window would.
    mock_df = connectors._generate_mock_organic_data(
        "linkedin_organic", "acct-1", "2026-01-01", "2026-01-30"
    )
    assert not mock_df.empty

    result = etl.aggregate_data([mock_df])
    assert result["usedMockData"] is True


def test_aggregate_data_no_mock_flag_for_real_looking_data():
    import pandas as pd
    from app.services import etl

    real_df = pd.DataFrame([{
        "date": "2026-01-01", "platform": "google", "campaign": "Brand",
        "ad_group": "N/A", "ad_asset": "N/A", "spend": 100.0,
        "impressions": 1000, "clicks": 50, "conversions": 5, "revenue": 500.0,
    }])
    result = etl.aggregate_data([real_df])
    assert result["usedMockData"] is False

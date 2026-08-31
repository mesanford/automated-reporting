"""Real API paths for the organic connectors (Facebook, Instagram, LinkedIn
Organic), added after discovering these connectors previously returned
fabricated data unconditionally (see docs/organic-and-ga4-integration-plan.md
and tests/test_demo_mode_organic.py, which cover the fallback/gating side).

These tests mock the HTTP layer with httpx.MockTransport (no extra
dependency) rather than hitting real Meta/LinkedIn APIs — they verify the
connector logic (pagination, field mapping, error propagation) against the
documented API shapes, not that the shapes themselves are still current.
Re-verify against live accounts before treating this as fully trustworthy;
both platforms reshape these endpoints periodically.
"""
import httpx
import pytest

from app.services import connectors
from app.services.connectors import ConnectorError


class _RoutedTransport(httpx.MockTransport):
    """MockTransport that dispatches on a simple substring-of-path match,
    so each test only has to describe the handful of endpoints it cares
    about instead of a full URL router."""

    def __init__(self, routes: dict):
        self._routes = routes  # {substring: response_dict_or_callable}
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        for substring, body in self._routes.items():
            if substring in path:
                if isinstance(body, httpx.Response):
                    return body
                if callable(body):
                    return body(request)
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": f"no mock route for {path}"}})


@pytest.fixture(autouse=True)
def meta_app_secret(monkeypatch):
    """Supply a fixed Meta app secret for the whole module.

    Every Meta Graph call here signs its request with an appsecret_proof, so
    the connector needs META_CLIENT_SECRET. Without this the tests silently
    inherited whatever sat in a developer's backend/.env — which is exactly
    why they passed locally and failed in CI, where no such file exists.

    get_secret() memoises, so the cache is cleared on both sides of the test.
    """
    from app.services import secrets_manager

    monkeypatch.setenv("META_CLIENT_SECRET", "test-meta-app-secret")
    secrets_manager.invalidate_cache("META_CLIENT_SECRET")
    yield
    secrets_manager.invalidate_cache("META_CLIENT_SECRET")


@pytest.fixture()
def patch_async_client(monkeypatch):
    """Returns a function that, given a routes dict, makes every
    httpx.AsyncClient created during the test use that mock transport."""

    def _apply(routes: dict):
        transport = _RoutedTransport(routes)

        class _Client(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(connectors.httpx, "AsyncClient", _Client)

    return _apply


# ── Facebook Organic ─────────────────────────────────────────────────────────

async def test_facebook_organic_fetch_maps_real_api_response(patch_async_client):
    patch_async_client({
        "/123?": {"access_token": "page-token-abc"},  # page access token lookup
        "/123/feed": {
            "data": [
                {"id": "post_1", "message": "Hello world", "created_time": "2026-01-05T12:00:00+0000"},
            ],
            "paging": {},
        },
        "post_1/insights": {
            "data": [
                {"name": "post_impressions_unique", "values": [{"value": 500}]},
                {"name": "post_engaged_users", "values": [{"value": 40}]},
                {"name": "post_clicks", "values": [{"value": 12}]},
            ]
        },
        "post_1?": {"shares": {"count": 7}},
    })

    df = await connectors._fetch_facebook_organic("123", "user-token", "2026-01-01", "2026-01-31")

    assert not df.empty
    row = df.iloc[0]
    assert row["platform"] == "facebook_organic"
    assert row["organic_reach"] == 500
    assert row["organic_engagements"] == 40
    assert row["organic_shares"] == 7
    assert row["_is_mock_data"] == False  # noqa: E712 (explicit False check, not just falsy)


async def test_facebook_organic_fetch_raises_on_feed_error(patch_async_client):
    patch_async_client({
        "/123?": {"access_token": "page-token-abc"},
        "/123/feed": httpx.Response(400, json={"error": {"message": "Invalid OAuth token"}}),
    })

    with pytest.raises(ConnectorError):
        await connectors._fetch_facebook_organic("123", "user-token", "2026-01-01", "2026-01-31")


async def test_discover_facebook_pages(patch_async_client):
    patch_async_client({
        "me/accounts": {
            "data": [{"id": "123", "name": "My Page", "category": "Business"}],
            "paging": {},
        },
    })
    accounts = await connectors._discover_facebook_pages("user-token")
    assert accounts == [{"id": "123", "name": "My Page", "status": "ACTIVE", "currency": "N/A"}]


# ── Instagram Organic ────────────────────────────────────────────────────────

async def test_instagram_organic_fetch_maps_real_api_response(patch_async_client):
    def page_lookup(request: httpx.Request) -> httpx.Response:
        # First call (from _discover_facebook_pages via the fetch fn) hits
        # /{page}?fields=instagram_business_account; second hits
        # /{page}?fields=access_token for the page token exchange.
        if "instagram_business_account" in str(request.url):
            return httpx.Response(200, json={"instagram_business_account": {"id": "ig_1", "username": "acme"}})
        return httpx.Response(200, json={"access_token": "page-token-abc"})

    patch_async_client({
        "me/accounts": {"data": [{"id": "123", "name": "My Page"}], "paging": {}},
        "/123?": page_lookup,
        "ig_1/media": {
            "data": [{"id": "media_1", "caption": "New post", "timestamp": "2026-01-05T12:00:00+0000"}],
            "paging": {},
        },
        "media_1/insights": {
            "data": [
                {"name": "reach", "values": [{"value": 900}]},
                {"name": "engagement", "values": [{"value": 60}]},
                {"name": "shares", "values": [{"value": 3}]},
            ]
        },
    })

    df = await connectors._fetch_instagram_organic("ig_1", "user-token", "2026-01-01", "2026-01-31")

    assert not df.empty
    row = df.iloc[0]
    assert row["platform"] == "instagram_organic"
    assert row["organic_reach"] == 900
    assert row["organic_engagements"] == 60
    assert row["organic_shares"] == 3


async def test_instagram_organic_fetch_raises_when_no_linked_page(patch_async_client):
    patch_async_client({
        "me/accounts": {"data": [], "paging": {}},
    })
    with pytest.raises(ConnectorError):
        await connectors._fetch_instagram_organic("ig_missing", "user-token", "2026-01-01", "2026-01-31")


# ── LinkedIn Organic ─────────────────────────────────────────────────────────

async def test_linkedin_organic_fetch_maps_real_api_response(patch_async_client):
    patch_async_client({
        "organizationalEntityShareStatistics": {
            "elements": [
                {
                    "totalShareStatistics": {
                        "impressionCount": 1000,
                        "uniqueImpressionsCount": 800,
                        "shareCount": 15,
                        "likeCount": 50,
                        "commentCount": 10,
                        "clickCount": 25,
                    },
                    "timeRange": {"start": 1767225600000},  # 2026-01-01 UTC
                }
            ]
        },
    })

    df = await connectors._fetch_linkedin_organic("456", "user-token", "2026-01-01", "2026-01-31")

    assert not df.empty
    row = df.iloc[0]
    assert row["platform"] == "linkedin_organic"
    assert row["organic_reach"] == 800
    assert row["organic_shares"] == 15
    assert row["organic_engagements"] == 50 + 10 + 25


async def test_linkedin_organic_fetch_raises_on_api_error(patch_async_client):
    patch_async_client({
        "organizationalEntityShareStatistics": httpx.Response(
            403, json={"message": "ACCESS_DENIED", "status": 403}
        ),
    })
    with pytest.raises(ConnectorError):
        await connectors._fetch_linkedin_organic("456", "user-token", "2026-01-01", "2026-01-31")

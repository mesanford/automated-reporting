"""Ad creative gallery: listing, filters, review, workspace isolation, and the
one behaviour the whole review workflow rests on — a re-sync must not wipe a
reviewer's decision.

That guarantee is the Postgres counterpart of the standalone gallery's
`batch.set(..., merge=True)` fix, so it gets an explicit test here.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice, bob):
    ws_id = client.post(
        "/api/workspaces", json={"name": "CreativeCo"}, headers=auth_headers(alice)
    ).json()["id"]
    inv = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": f"{bob}@x.com", "role": "member"},
        headers=auth_headers(alice, ws_id),
    ).json()
    client.post(
        f"/api/workspaces/invites/accept?token={inv['token']}",
        headers=auth_headers(bob),
    )
    return ws_id


def _make_creative(db, workspace_id, **overrides):
    from app import models

    fields = {
        "workspace_id": workspace_id,
        "platform": "meta",
        "ad_id": "123",
        "ad_name": "Spring sale",
        "headline": "Half price sausage",
        "ad_text": "Only this week.",
        "campaign_name": "Spring",
    }
    fields.update(overrides)
    creative = models.AdCreative(**fields)
    db.add(creative)
    db.commit()
    db.refresh(creative)
    return creative


def test_list_returns_workspace_creatives(client, db, workspace, alice):
    _make_creative(db, workspace, ad_id="1")
    _make_creative(db, workspace, ad_id="2", platform="google")

    r = client.get(
        f"/api/workspaces/{workspace}/creatives", headers=auth_headers(alice, workspace)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {c["ad_id"] for c in body["creatives"]} == {"1", "2"}


def test_platform_and_review_filters(client, db, workspace, alice):
    _make_creative(db, workspace, ad_id="1", platform="meta")
    _make_creative(db, workspace, ad_id="2", platform="google", review_status="keep")

    only_google = client.get(
        f"/api/workspaces/{workspace}/creatives?platform=google",
        headers=auth_headers(alice, workspace),
    ).json()
    assert [c["ad_id"] for c in only_google["creatives"]] == ["2"]

    unreviewed = client.get(
        f"/api/workspaces/{workspace}/creatives?review_status=unreviewed",
        headers=auth_headers(alice, workspace),
    ).json()
    assert [c["ad_id"] for c in unreviewed["creatives"]] == ["1"]


def test_unknown_platform_filter_is_rejected(client, workspace, alice):
    r = client.get(
        f"/api/workspaces/{workspace}/creatives?platform=snapchat",
        headers=auth_headers(alice, workspace),
    )
    assert r.status_code == 400


def test_search_matches_headline_and_campaign(client, db, workspace, alice):
    _make_creative(db, workspace, ad_id="1", headline="Bacon deal", campaign_name="Q1")
    _make_creative(db, workspace, ad_id="2", headline="Ham deal", campaign_name="Q2")

    r = client.get(
        f"/api/workspaces/{workspace}/creatives?search=bacon",
        headers=auth_headers(alice, workspace),
    ).json()
    assert [c["ad_id"] for c in r["creatives"]] == ["1"]


def test_review_sets_status_and_author(client, db, workspace, bob):
    creative = _make_creative(db, workspace)

    r = client.patch(
        f"/api/workspaces/{workspace}/creatives/{creative.id}/review",
        json={"review_status": "change", "review_comment": "Swap the hero image"},
        headers=auth_headers(bob, workspace),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "change"
    assert body["review_comment"] == "Swap the hero image"
    assert body["review_updated_by"] == bob
    assert body["review_updated_at"] is not None


def test_review_can_be_cleared(client, db, workspace, alice):
    creative = _make_creative(db, workspace, review_status="keep")
    r = client.patch(
        f"/api/workspaces/{workspace}/creatives/{creative.id}/review",
        json={"review_status": ""},
        headers=auth_headers(alice, workspace),
    )
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] is None


def test_invalid_review_status_is_rejected(client, db, workspace, alice):
    creative = _make_creative(db, workspace)
    r = client.patch(
        f"/api/workspaces/{workspace}/creatives/{creative.id}/review",
        json={"review_status": "maybe"},
        headers=auth_headers(alice, workspace),
    )
    assert r.status_code == 400


def test_non_member_cannot_read_or_review(client, db, workspace, carol):
    creative = _make_creative(db, workspace)

    assert client.get(
        f"/api/workspaces/{workspace}/creatives", headers=auth_headers(carol, workspace)
    ).status_code == 403
    assert client.patch(
        f"/api/workspaces/{workspace}/creatives/{creative.id}/review",
        json={"review_status": "keep"},
        headers=auth_headers(carol, workspace),
    ).status_code == 403


def test_summary_counts_by_platform_and_review(client, db, workspace, alice):
    _make_creative(db, workspace, ad_id="1", platform="meta", review_status="keep")
    _make_creative(db, workspace, ad_id="2", platform="meta")
    _make_creative(db, workspace, ad_id="3", platform="google", review_status="remove")

    body = client.get(
        f"/api/workspaces/{workspace}/creatives/summary",
        headers=auth_headers(alice, workspace),
    ).json()
    assert body["total"] == 3
    assert body["by_platform"] == {"meta": 2, "google": 1}
    assert body["by_review_status"]["keep"] == 1
    assert body["by_review_status"]["remove"] == 1
    assert body["by_review_status"]["unreviewed"] == 1


def test_sync_requires_a_supported_connection(client, workspace, alice):
    r = client.post(
        f"/api/workspaces/{workspace}/creatives/sync",
        json={},
        headers=auth_headers(alice, workspace),
    )
    assert r.status_code == 400
    assert "connection" in r.json()["detail"].lower()


def test_asset_endpoint_404s_without_stored_asset(client, db, workspace, alice):
    creative = _make_creative(db, workspace)
    r = client.get(
        f"/api/workspaces/{workspace}/creatives/{creative.id}/asset",
        headers=auth_headers(alice, workspace),
    )
    assert r.status_code == 404


# ── The merge=True guarantee ─────────────────────────────────────────────────

def test_resync_updates_content_but_preserves_review(db, workspace):
    """A re-sync overwrites the platform-owned fields and leaves the review
    decision untouched. This is the behaviour the whole gallery depends on."""
    from app import models
    from app.services.creative_sync import _upsert

    creative = _make_creative(db, workspace, ad_id="777", headline="Old headline")
    creative.review_status = "change"
    creative.review_comment = "Use the winter shot"
    db.commit()

    inserted = _upsert(
        db,
        workspace,
        connection_id=None,
        record={
            "platform": "meta",
            "ad_id": "777",
            "account_id": "act_1",
            "ad_name": "Spring sale v2",
            "headline": "New headline",
            "ad_text": "Fresh copy.",
            "campaign_name": "Spring",
            "ad_group_name": None,
            "creative_type": None,
            "group_type": None,
            "final_url": "https://example.com",
            "source_asset_url": "https://cdn.example.com/a.jpg",
            "asset_path": "creatives/meta/1/777.jpg",
            "asset_content_type": "image/jpeg",
            "embed_url": None,
        },
    )
    db.commit()

    assert inserted is False  # updated the existing row, did not duplicate
    refreshed = (
        db.query(models.AdCreative)
        .filter(models.AdCreative.workspace_id == workspace, models.AdCreative.ad_id == "777")
        .one()
    )
    assert refreshed.headline == "New headline"
    assert refreshed.asset_path == "creatives/meta/1/777.jpg"
    assert refreshed.review_status == "change"
    assert refreshed.review_comment == "Use the winter shot"


def test_resync_keeps_existing_asset_when_download_fails(db, workspace):
    """A failed asset download returns None; that must not blank out a
    previously mirrored asset."""
    from app import models
    from app.services.creative_sync import _upsert

    _make_creative(
        db, workspace, ad_id="888",
        asset_path="creatives/meta/1/888.jpg", asset_content_type="image/jpeg",
    )

    _upsert(
        db, workspace, connection_id=None,
        record={
            "platform": "meta", "ad_id": "888", "account_id": None,
            "ad_name": "Still here", "headline": "h", "ad_text": None,
            "campaign_name": None, "ad_group_name": None, "creative_type": None,
            "group_type": None, "final_url": None,
            "source_asset_url": "https://cdn.example.com/gone.jpg",
            "asset_path": None, "asset_content_type": None, "embed_url": None,
        },
    )
    db.commit()

    refreshed = (
        db.query(models.AdCreative)
        .filter(models.AdCreative.workspace_id == workspace, models.AdCreative.ad_id == "888")
        .one()
    )
    assert refreshed.asset_path == "creatives/meta/1/888.jpg"
    assert refreshed.ad_name == "Still here"


# ── Fetcher-level behaviour ──────────────────────────────────────────────────

def test_empty_creatives_are_skipped():
    """No media, no headline, no text → not a creative. Carried over from all
    three source pipelines."""
    from app.services.creatives import _is_empty_creative

    assert _is_empty_creative({"headline": None, "ad_text": None, "source_asset_url": None})
    assert not _is_empty_creative({"headline": "Buy now", "ad_text": None, "source_asset_url": None})
    assert not _is_empty_creative({"headline": None, "ad_text": None, "embed_url": "https://yt/e/1"})


def test_na_sentinel_is_normalised_away():
    """The source pipelines wrote the literal string 'N/A' for empty fields."""
    from app.services.creatives import _clean

    assert _clean("N/A") is None
    assert _clean("nan") is None
    assert _clean("  ") is None
    assert _clean("  Real value ") == "Real value"


def test_unsupported_platform_is_rejected():
    from app.services.connectors import ConnectorConfigError
    from app.services.creatives import fetch_creatives

    with pytest.raises(ConnectorConfigError):
        asyncio.run(fetch_creatives("linkedin", "123", access_token="x"))

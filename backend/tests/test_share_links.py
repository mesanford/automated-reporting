"""Public report share links: mint (auth + role gate), anonymous view,
view counter, revoke, expiry."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace_and_report(client, db, alice, bob):
    """Alice creates the workspace, Bob is a member, both can see report."""
    r = client.post("/api/workspaces", json={"name": "Share Co"}, headers=auth_headers(alice))
    ws_id = r.json()["id"]
    inv = client.post(
        f"/api/workspaces/{ws_id}/invites",
        json={"email": f"{bob}@x.com", "role": "member"},
        headers=auth_headers(alice, ws_id),
    ).json()
    client.post(
        f"/api/workspaces/invites/accept?token={inv['token']}",
        headers=auth_headers(bob),
    )

    from app import models
    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        current_period_label="2026-06",
        scorecards={"totalSpend": 4242},
        gemini_analysis="hello",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return ws_id, report.id


def test_member_cannot_create_share_link(client, workspace_and_report, bob):
    ws_id, report_id = workspace_and_report
    r = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_mints_link_and_token_is_returned_once(client, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    r = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["token"], str) and len(body["token"]) >= 40
    saved_token = body["token"]

    # List should NOT leak the raw token (token field is None on list).
    rows = client.get(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        headers=auth_headers(alice, ws_id),
    ).json()
    assert len(rows) == 1
    assert rows[0]["token"] is None

    # The raw token still works as a public viewer.
    view = client.get(f"/api/share/{saved_token}")
    assert view.status_code == 200


def test_create_validates_expiry_bounds(client, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    for bad in (0, -1, 366, 9999):
        r = client.post(
            f"/api/workspaces/{ws_id}/reports/{report_id}/share",
            json={"expires_in_days": bad},
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 400, bad


def test_public_view_returns_report_without_auth(client, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    token = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    ).json()["token"]

    # NO auth headers — that's the whole point of a public link.
    r = client.get(f"/api/share/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["id"] == report_id
    assert body["report"]["scorecards"]["totalSpend"] == 4242
    assert "expires_at" in body["share"]


def test_public_view_includes_currency_for_the_printable_view(
    client, workspace_and_report, alice
):
    """The printable/PDF view has no workspace context of its own, so the public
    payload has to carry the currency or money figures would silently render as
    USD for every recipient."""
    ws_id, report_id = workspace_and_report
    token = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    ).json()["token"]

    body = client.get(f"/api/share/{token}").json()
    assert "workspace" in body
    assert body["workspace"]["base_currency"]  # never null/empty
    # Only the two fields the printable cover needs are exposed.
    assert set(body["workspace"].keys()) == {"name", "base_currency"}


def test_public_view_bumps_view_counter(client, db, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    token = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    ).json()["token"]

    client.get(f"/api/share/{token}")
    client.get(f"/api/share/{token}")
    client.get(f"/api/share/{token}")

    from app import models
    link = (
        db.query(models.ReportShareLink)
        .filter(models.ReportShareLink.workspace_id == ws_id)
        .first()
    )
    db.refresh(link)
    assert link.view_count == 3
    assert link.last_viewed_at is not None


def test_revoke_returns_410_on_subsequent_view(client, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    minted = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    ).json()

    # First view works.
    assert client.get(f"/api/share/{minted['token']}").status_code == 200

    client.delete(
        f"/api/workspaces/{ws_id}/share/{minted['id']}",
        headers=auth_headers(alice, ws_id),
    )

    r = client.get(f"/api/share/{minted['token']}")
    assert r.status_code == 410
    assert "revoked" in r.json()["detail"].lower()


def test_expired_link_returns_410(client, db, workspace_and_report, alice):
    ws_id, report_id = workspace_and_report
    minted = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 1},
        headers=auth_headers(alice, ws_id),
    ).json()

    from app import models
    link = (
        db.query(models.ReportShareLink)
        .filter(models.ReportShareLink.id == minted["id"])
        .first()
    )
    link.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    r = client.get(f"/api/share/{minted['token']}")
    assert r.status_code == 410
    assert "expired" in r.json()["detail"].lower()


def test_unknown_token_returns_404(client):
    r = client.get("/api/share/this-is-not-a-real-token-abc123")
    assert r.status_code == 404


def test_token_not_stored_plaintext(client, db, workspace_and_report, alice):
    """The raw token never sits in the DB — we store sha256(token)."""
    ws_id, report_id = workspace_and_report
    minted = client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": 7},
        headers=auth_headers(alice, ws_id),
    ).json()
    token = minted["token"]

    from app import models
    link = (
        db.query(models.ReportShareLink)
        .filter(models.ReportShareLink.id == minted["id"])
        .first()
    )
    assert link.token_hash != token
    assert len(link.token_hash) == 64  # sha256 hex

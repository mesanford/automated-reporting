"""Email digests: subscription CRUD, renderer, scheduler tick that picks
due rows + advances next_send_at."""
from datetime import datetime, timedelta

import pytest

from tests.conftest import auth_headers


@pytest.fixture()
def workspace(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "Digest Co"}, headers=auth_headers(alice)
    )
    return r.json()["id"]


def test_next_send_daily_picks_today_or_tomorrow():
    from app.services.digests import compute_next_send

    # 09:00 UTC → today 13:00
    n = compute_next_send("daily", now=datetime(2026, 5, 1, 9, 0, 0))
    assert n == datetime(2026, 5, 1, 13, 0, 0)
    # 14:00 UTC → tomorrow 13:00
    n = compute_next_send("daily", now=datetime(2026, 5, 1, 14, 0, 0))
    assert n == datetime(2026, 5, 2, 13, 0, 0)


def test_next_send_weekly_picks_next_monday():
    from app.services.digests import compute_next_send

    # 2026-05-01 is a Friday → next Monday 2026-05-04 13:00.
    n = compute_next_send("weekly", now=datetime(2026, 5, 1, 9, 0, 0))
    assert n.weekday() == 0
    assert n.hour == 13


def test_initial_subscription_uses_identity_email(client, workspace, alice):
    """No `email` in the body — falls back to identity. Dev auth gives
    identity.email = None, so a bare subscribe should 400 with a clear
    message rather than silently sending nothing."""
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "daily"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400
    assert "valid email" in r.json()["detail"].lower()


def test_subscribe_with_explicit_email(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "weekly", "email": "ALICE@X.COM"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@x.com"  # lowercased
    assert body["cadence"] == "weekly"
    assert body["is_active"] is True
    assert body["next_send_at"] is not None


def test_subscribe_validates_cadence(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "hourly", "email": "a@x.com"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_subscribe_is_idempotent(client, workspace, alice):
    """A second subscribe for the same user overwrites cadence + email
    instead of creating a duplicate row."""
    ws_id = workspace
    client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "daily", "email": "a@x.com"},
        headers=auth_headers(alice, ws_id),
    )
    r = client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "weekly", "email": "b@x.com"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "b@x.com"
    assert body["cadence"] == "weekly"


def test_unsubscribe_flips_is_active(client, workspace, alice):
    ws_id = workspace
    client.post(
        f"/api/workspaces/{ws_id}/digests/subscribe",
        json={"cadence": "daily", "email": "a@x.com"},
        headers=auth_headers(alice, ws_id),
    )
    r = client.post(
        f"/api/workspaces/{ws_id}/digests/unsubscribe",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200

    me = client.get(
        f"/api/workspaces/{ws_id}/digests/me", headers=auth_headers(alice, ws_id)
    ).json()
    assert me["is_active"] is False


def test_render_digest_body_includes_workspace_and_metrics(db, workspace, alice):
    ws_id = workspace
    from app import models
    from app.services.digests import render_digest_body

    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        current_period_label="2026-05",
        scorecards={"totalSpend": 1234, "totalConversions": 56, "blendedCPA": 22, "blendedROAS": 3.5},
    )
    db.add(report)
    sub = models.DigestSubscription(
        workspace_id=ws_id, user_subject=alice, email="a@x.com",
        cadence="daily", is_active=1,
        next_send_at=datetime.utcnow(),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    subject, body = render_digest_body(db, sub)
    assert "Digest Co" in subject
    assert "Spend" in body and "1,234" in body
    assert "Conversions" in body and "56" in body
    assert "Blended CPA" in body and "22" in body
    assert "ROAS" in body and "3.5x" in body


def test_tick_sends_due_and_advances(db, workspace, alice, monkeypatch):
    """Two subs: one due, one in the future. Tick sends only the due
    one + advances its next_send_at."""
    ws_id = workspace
    from app import models
    from app.services import digests, notifications

    captured = []

    def fake_send(channel, subject, body):
        captured.append((channel["to"], subject))
        return {"delivered": True, "channel": "email", "error": None}

    monkeypatch.setattr(notifications, "send_to_channel", fake_send)

    past_sub = models.DigestSubscription(
        workspace_id=ws_id, user_subject="due-user", email="due@x.com",
        cadence="daily", is_active=1,
        next_send_at=datetime.utcnow() - timedelta(hours=1),
    )
    future_sub = models.DigestSubscription(
        workspace_id=ws_id, user_subject="later-user", email="later@x.com",
        cadence="daily", is_active=1,
        next_send_at=datetime.utcnow() + timedelta(hours=2),
    )
    db.add_all([past_sub, future_sub])
    db.commit()
    db.refresh(past_sub)
    db.refresh(future_sub)
    past_id, future_id = past_sub.id, future_sub.id

    sent = digests.send_due_digests(db)
    assert len(sent) == 1
    assert sent[0]["delivered"] is True
    assert sent[0]["email"] == "due@x.com"

    db.expire_all()
    past_after = db.query(models.DigestSubscription).filter(models.DigestSubscription.id == past_id).first()
    future_after = db.query(models.DigestSubscription).filter(models.DigestSubscription.id == future_id).first()
    assert past_after.last_sent_at is not None
    assert past_after.next_send_at > datetime.utcnow()
    assert future_after.last_sent_at is None
    assert len(captured) == 1


def test_tick_records_error_but_still_advances_on_failure(db, workspace, alice, monkeypatch):
    """A permanently-broken address should advance next_send_at — otherwise
    the row backs up forever. The error is recorded on the row."""
    ws_id = workspace
    from app import models
    from app.services import digests, notifications

    def fake_send(channel, subject, body):
        return {"delivered": False, "channel": "email", "error": "550 mailbox unavailable"}

    monkeypatch.setattr(notifications, "send_to_channel", fake_send)

    sub = models.DigestSubscription(
        workspace_id=ws_id, user_subject="bad-user", email="bad@x.com",
        cadence="daily", is_active=1,
        next_send_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    sid = sub.id

    digests.send_due_digests(db)

    db.expire_all()
    sub = db.query(models.DigestSubscription).filter(models.DigestSubscription.id == sid).first()
    assert sub.last_sent_at is not None  # advanced anyway
    assert sub.last_send_error == "550 mailbox unavailable"
    assert sub.next_send_at > datetime.utcnow()


def test_tick_endpoint_requires_oidc(client, monkeypatch):
    """Without ALLOW_INTERNAL_NO_OIDC, the tick endpoint refuses a
    request that has no bearer token."""
    monkeypatch.delenv("ALLOW_INTERNAL_NO_OIDC", raising=False)
    r = client.post("/api/internal/digests/tick", json={})
    assert r.status_code == 401


def test_tick_endpoint_processes_due_with_dev_oidc(client, db, workspace, alice, monkeypatch):
    monkeypatch.setenv("ALLOW_INTERNAL_NO_OIDC", "1")

    from app import models
    from app.services import notifications

    monkeypatch.setattr(
        notifications, "send_to_channel",
        lambda channel, subject, body: {"delivered": True, "channel": "email", "error": None},
    )

    db.add(models.DigestSubscription(
        workspace_id=workspace, user_subject="x", email="x@x.com",
        cadence="daily", is_active=1,
        next_send_at=datetime.utcnow() - timedelta(minutes=5),
    ))
    db.commit()

    r = client.post("/api/internal/digests/tick", json={})
    assert r.status_code == 200
    assert len(r.json()["sent"]) >= 1

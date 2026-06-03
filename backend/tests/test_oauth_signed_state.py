"""Server-signed OAuth state: sign/verify, tamper detection, expiry,
and the POST /api/auth/oauth/start endpoint.

Closes the gap where the OAuth callback couldn't be authenticated via
Bearer (browsers can't attach a header to a top-level navigation back
from the OAuth provider). The signed state carries the user identity
HMAC-protected by a server secret.
"""
import time

import pytest

from tests.conftest import auth_headers


def test_sign_and_verify_roundtrip():
    from app.api.oauth import sign_state, verify_state

    state = sign_state(
        platform="google",
        workspace_id=42,
        connection_id=99,
        user_subject="firebase-uid-abc",
    )
    assert state.startswith("v3.")

    payload = verify_state(state)
    assert payload is not None
    assert payload.platform == "google"
    assert payload.workspace_id == 42
    assert payload.connection_id == 99
    assert payload.user_subject == "firebase-uid-abc"


def test_verify_rejects_tampered_payload():
    """Flipping one byte of the payload invalidates the HMAC."""
    from app.api.oauth import sign_state, verify_state

    state = sign_state(
        platform="google", workspace_id=1, connection_id=None, user_subject="alice"
    )
    _, body, sig = state.split(".", 2)

    # Swap a character in the body and re-stitch with the original sig.
    bad_body = ("A" if body[0] != "A" else "B") + body[1:]
    tampered = f"v3.{bad_body}.{sig}"

    assert verify_state(tampered) is None


def test_verify_rejects_tampered_signature():
    from app.api.oauth import sign_state, verify_state

    state = sign_state(
        platform="meta", workspace_id=2, connection_id=None, user_subject="bob"
    )
    _, body, sig = state.split(".", 2)
    bad_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert verify_state(f"v3.{body}.{bad_sig}") is None


def test_verify_rejects_expired_state(monkeypatch):
    from app.api import oauth

    # Sign now, then jump the clock past TTL.
    state = oauth.sign_state(
        platform="google", workspace_id=1, connection_id=None, user_subject="alice"
    )

    fake_now = time.time() + oauth.STATE_TTL_SECONDS + 10
    monkeypatch.setattr(oauth.time, "time", lambda: fake_now)
    assert oauth.verify_state(state) is None


def test_verify_rejects_legacy_state_format():
    """Old v1/v2 state shouldn't accidentally pass v3 verification."""
    from app.api.oauth import verify_state

    assert verify_state("v2:google:42:99") is None
    assert verify_state("google") is None
    assert verify_state("") is None
    assert verify_state("v3.malformed") is None


@pytest.fixture()
def alice_workspace(client, db, alice):
    """Alice's Personal workspace, ready to target with OAuth start."""
    client.get("/api/chat/conversations", headers=auth_headers(alice))
    from app import models

    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.created_by_subject == alice)
        .first()
    )
    return ws.id


def test_oauth_start_requires_known_platform(client, alice, alice_workspace):
    r = client.post(
        "/api/auth/oauth/start",
        json={"platform": "unknown_provider", "workspace_id": alice_workspace},
        headers=auth_headers(alice, alice_workspace),
    )
    assert r.status_code == 400


def test_oauth_start_returns_signed_redirect(client, alice, alice_workspace, monkeypatch):
    """Happy path: get back a provider URL whose `state` parameter verifies."""
    # Stub the client_id so the endpoint doesn't 500 on missing credentials.
    from app.api import oauth as oauth_module

    monkeypatch.setattr(oauth_module, "_platform_client_id", lambda p: "test-client-id")

    r = client.post(
        "/api/auth/oauth/start",
        json={"platform": "google", "workspace_id": alice_workspace},
        headers=auth_headers(alice, alice_workspace),
    )
    assert r.status_code == 200, r.text
    url = r.json()["redirect_url"]

    # Parse the state out of the redirect URL.
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(url).query)
    state = qs["state"][0]
    payload = oauth_module.verify_state(state)
    assert payload is not None
    assert payload.platform == "google"
    assert payload.workspace_id == alice_workspace
    assert payload.user_subject == alice


def test_oauth_start_rejects_workspace_non_membership(client, alice, bob, alice_workspace, monkeypatch):
    """Bob trying to start OAuth for Alice's workspace → 403."""
    from app.api import oauth as oauth_module

    monkeypatch.setattr(oauth_module, "_platform_client_id", lambda p: "test-client-id")

    r = client.post(
        "/api/auth/oauth/start",
        json={"platform": "google", "workspace_id": alice_workspace},
        headers=auth_headers(bob),
    )
    assert r.status_code == 403

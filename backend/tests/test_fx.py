"""FX service + workspace base_currency."""
import pytest

from tests.conftest import auth_headers


def test_identity_conversion_short_circuits(db):
    from app.services.fx import convert

    out, env = convert(100.0, from_currency="USD", to_currency="USD", db=db)
    assert out == 100.0
    assert env["source"] == "identity"
    assert env["rate"] == 1.0


def test_cached_rate_used_when_available(db, monkeypatch):
    from app import models
    from app.services import fx
    from datetime import date

    db.add(models.FxRate(
        as_of_date=date.today().isoformat(),
        from_currency="EUR",
        to_currency="USD",
        rate="1.20",
    ))
    db.commit()

    def boom(*args, **kwargs):
        raise AssertionError("fx fetcher should not be called when cache hits")

    monkeypatch.setattr(fx, "_fetch_from_frankfurter", boom)

    out, env = fx.convert(100.0, from_currency="EUR", to_currency="USD", db=db)
    assert out == 120.0
    assert env["source"] == "cached"


def test_fetcher_called_and_cached_when_missing(db, monkeypatch):
    from app import models
    from app.services import fx

    calls = []

    def fake_fetch(from_ccy, to_ccy):
        calls.append((from_ccy, to_ccy))
        return 1.10

    monkeypatch.setattr(fx, "_fetch_from_frankfurter", fake_fetch)

    out, env = fx.convert(50.0, from_currency="EUR", to_currency="USD", db=db)
    assert out == pytest.approx(55.0)
    assert env["source"] == "frankfurter"
    assert calls == [("EUR", "USD")]

    # Second call same day: cache hits, fetcher not called again.
    calls.clear()
    out2, env2 = fx.convert(200.0, from_currency="EUR", to_currency="USD", db=db)
    assert env2["source"] == "cached"
    assert calls == []
    assert out2 == pytest.approx(220.0)


def test_fallback_when_fetch_fails(db, monkeypatch):
    """API down → return unconverted amount + `fallback` source.
    Critical: must not raise."""
    from app.services import fx

    monkeypatch.setattr(fx, "_fetch_from_frankfurter", lambda *_: None)

    out, env = fx.convert(100.0, from_currency="GBP", to_currency="USD", db=db)
    assert out == 100.0
    assert env["source"] == "fallback"
    assert env["rate"] == 1.0


def test_workspace_has_base_currency_default_usd(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "FX Co"}, headers=auth_headers(alice)
    )
    assert r.json()["base_currency"] == "USD"


def test_workspace_update_changes_base_currency(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "FX Co"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]

    r = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"base_currency": "eur"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert r.json()["base_currency"] == "EUR"  # normalized to upper


def test_workspace_update_rejects_invalid_currency(client, alice):
    r = client.post(
        "/api/workspaces", json={"name": "x"}, headers=auth_headers(alice)
    )
    ws_id = r.json()["id"]

    for bad in ("EU", "EURO", "12X", "1U$"):
        r = client.patch(
            f"/api/workspaces/{ws_id}",
            json={"base_currency": bad},
            headers=auth_headers(alice, ws_id),
        )
        assert r.status_code == 400, bad


def test_workspace_update_role_gated(client, alice, bob):
    r = client.post(
        "/api/workspaces", json={"name": "x"}, headers=auth_headers(alice)
    )
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

    r = client.patch(
        f"/api/workspaces/{ws_id}",
        json={"base_currency": "EUR"},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403

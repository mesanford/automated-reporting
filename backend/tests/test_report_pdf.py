"""PDF endpoints.

Rendering itself is stubbed — launching Chromium in unit tests would be slow and
would make the suite depend on a browser build. What is worth asserting here is
the access control around it, and that a missing Chromium degrades to a clear
503 rather than a stack trace.
"""
import pytest

from app import models
from app.services import pdf as pdf_service
# `workspace_and_report` lives in test_share_links; import it so pytest can
# resolve it as a fixture here too.
from tests.conftest import auth_headers
from tests.test_share_links import workspace_and_report  # noqa: F401


@pytest.fixture
def fake_render(monkeypatch):
    calls = []

    def _render(token, **kwargs):
        calls.append(token)
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(pdf_service, "render_share_pdf", _render)
    return calls


def _mint(client, ws_id, report_id, alice, days=7):
    return client.post(
        f"/api/workspaces/{ws_id}/reports/{report_id}/share",
        json={"expires_in_days": days},
        headers=auth_headers(alice, ws_id),
    ).json()["token"]


def test_public_pdf_download(client, workspace_and_report, alice, fake_render):
    ws_id, report_id = workspace_and_report
    token = _mint(client, ws_id, report_id, alice)

    r = client.get(f"/api/share/{token}/pdf")  # no auth, by design
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment; filename=" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")
    assert fake_render == [token]


def test_revoked_link_cannot_be_rendered(client, db, workspace_and_report, alice, fake_render):
    """The PDF route must not become a way around revocation."""
    ws_id, report_id = workspace_and_report
    token = _mint(client, ws_id, report_id, alice)
    link = db.query(models.ReportShareLink).first()
    link.is_active = 0
    db.commit()

    r = client.get(f"/api/share/{token}/pdf")
    assert r.status_code == 410
    assert fake_render == []  # never reached the renderer


def test_expired_link_cannot_be_rendered(client, db, workspace_and_report, alice, fake_render):
    from datetime import datetime, timedelta

    ws_id, report_id = workspace_and_report
    token = _mint(client, ws_id, report_id, alice)
    link = db.query(models.ReportShareLink).first()
    link.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    assert client.get(f"/api/share/{token}/pdf").status_code == 410
    assert fake_render == []


def test_unknown_token_404(client, fake_render):
    assert client.get("/api/share/nope/pdf").status_code == 404
    assert fake_render == []


def test_authenticated_pdf_revokes_its_ephemeral_link(
    client, db, workspace_and_report, alice, fake_render
):
    """The operator route mints a link for one render and must not leave it live."""
    ws_id, report_id = workspace_and_report

    r = client.get(
        f"/api/workspaces/{ws_id}/reports/{report_id}/pdf",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")

    links = db.query(models.ReportShareLink).all()
    assert len(links) == 1
    assert links[0].is_active == 0, "ephemeral render link was left active"
    assert links[0].created_by_subject.startswith("system:pdf:")


def test_ephemeral_link_is_revoked_even_when_rendering_fails(
    client, db, workspace_and_report, alice, monkeypatch
):
    def _boom(token, **kwargs):
        raise pdf_service.PdfRenderError("chromium exploded")

    monkeypatch.setattr(pdf_service, "render_share_pdf", _boom)
    ws_id, report_id = workspace_and_report

    r = client.get(
        f"/api/workspaces/{ws_id}/reports/{report_id}/pdf",
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 502
    links = db.query(models.ReportShareLink).all()
    assert links and links[0].is_active == 0, "a failed render left a live link"


def test_missing_chromium_returns_503(client, workspace_and_report, alice, monkeypatch):
    def _unavailable(token, **kwargs):
        raise pdf_service.PdfUnavailable("no chromium here")

    monkeypatch.setattr(pdf_service, "render_share_pdf", _unavailable)
    ws_id, report_id = workspace_and_report
    token = _mint(client, ws_id, report_id, alice)

    r = client.get(f"/api/share/{token}/pdf")
    assert r.status_code == 503
    assert "not available" in r.json()["detail"]


def test_filename_is_derived_from_the_period_label():
    assert pdf_service.pdf_filename("October 2026 vs September", 7) == "October-2026-vs-September.pdf"
    assert pdf_service.pdf_filename(None, 7) == "report-7.pdf"
    assert pdf_service.pdf_filename("Q4 / 2026", 7) == "Q4-2026.pdf"

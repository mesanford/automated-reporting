"""Server-rendered PDFs of report pages.

Why headless Chrome rather than a Python PDF library: the printable layout
already exists as a React component, and the whole point of a client deliverable
is that the file the client opens matches what the operator previewed. Rebuilding
that layout in ReportLab or WeasyPrint would guarantee the two drift apart. So we
drive the real page through the real rendering engine.

Why Chrome specifically: browser print output differs between engines — margins,
background graphics, and page-break handling in particular — so a PDF produced by
one recipient's browser is not the PDF produced by another's. Rendering once,
server-side, is what makes the output identical for everyone.

The renderer loads the *public* share print route. Headless Chrome has no session
and cannot carry a bearer token, so callers that are authenticated mint a
short-lived share link and point the renderer at that instead of trying to
authenticate the browser.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("pipeline")

DEFAULT_TIMEOUT_MS = 30_000
# Give client-side fetch + React render time to settle after network idle.
SETTLE_MS = 500


class PdfUnavailable(RuntimeError):
    """Chromium isn't installed in this image, or failed to launch."""


class PdfRenderError(RuntimeError):
    """The page loaded but could not be turned into a PDF."""


def frontend_base_url() -> str:
    return (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")


def is_available() -> bool:
    """True when Playwright and a Chromium build are both present.

    Cheap enough to call from a readiness probe; does not launch a browser.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
        return bool(path) and os.path.exists(path)
    except Exception:
        return False


def render_url_to_pdf(
    url: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    paper_format: str = "Letter",
    landscape: bool = False,
) -> bytes:
    """Render `url` to PDF bytes with headless Chromium.

    Blocking: callers should run this in a thread. `print_background=True`
    matters — Chrome omits background colours by default, which would strip the
    table headers and the fabricated-data warning banner.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - import guard
        raise PdfUnavailable(
            "Playwright is not installed. PDF rendering requires the "
            "playwright package and a Chromium build."
        ) from exc

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    args=[
                        # Cloud Run containers have a small default /dev/shm.
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ]
                )
            except Exception as exc:
                raise PdfUnavailable(f"Could not launch Chromium: {exc}") from exc

            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # The print route auto-fires window.print(); in headless Chrome
                # that is a no-op, but the layout still needs a beat to settle.
                page.wait_for_timeout(SETTLE_MS)
                return page.pdf(
                    format=paper_format,
                    landscape=landscape,
                    print_background=True,
                    margin={
                        "top": "1.5cm",
                        "bottom": "1.5cm",
                        "left": "1.5cm",
                        "right": "1.5cm",
                    },
                )
            finally:
                browser.close()
    except PdfUnavailable:
        raise
    except Exception as exc:
        raise PdfRenderError(f"Failed to render {url}: {exc}") from exc


def render_share_pdf(token: str, **kwargs) -> bytes:
    """Render the public printable view for a share token."""
    return render_url_to_pdf(f"{frontend_base_url()}/share/{token}/print", **kwargs)


def pdf_filename(label: Optional[str], report_id: int) -> str:
    """A filename a client can recognise in their downloads folder."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label or "").strip("-")
    return f"{slug or f'report-{report_id}'}.pdf"

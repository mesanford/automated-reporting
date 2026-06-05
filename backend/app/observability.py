"""GCP-native observability bootstrap.

On Cloud Run, structured logs sent to stdout flow into Cloud Logging
automatically — but `google.cloud.logging.Client().setup_logging()` upgrades
the experience: it formats records with severity/trace fields, hooks
`sys.excepthook` so uncaught exceptions surface in Error Reporting, and
attaches the Cloud Run resource labels.

Outside Cloud Run (local dev, tests) we skip the GCP client and fall back
to a plain `logging.basicConfig` so output stays readable.
"""

from __future__ import annotations

import logging
import os


def configure_observability() -> None:
    is_production = os.getenv("APP_ENV", "").lower() == "production"
    has_gcp_project = bool(os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"))

    if is_production and has_gcp_project:
        try:
            import google.cloud.logging
            from google.cloud.logging.handlers import setup_logging

            client = google.cloud.logging.Client()
            handler = client.get_default_handler()
            setup_logging(handler, log_level=logging.INFO)
            logging.getLogger(__name__).info("gcp-logging-initialized")
            return
        except Exception as exc:  # noqa: BLE001
            logging.basicConfig(level=logging.INFO)
            logging.getLogger(__name__).warning(
                "gcp-logging-init-failed", extra={"error": str(exc)}
            )
            return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

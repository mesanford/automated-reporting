"""Startup preflight: fail fast on dangerous configs.

The goal is to make production misconfigurations loud at boot rather than
silent until the first request. We classify each issue at one of three
levels:

- `error`   — refuse to boot. Used in production for combinations that
              would let real users in via the dev escape hatch.
- `warning` — log loudly but boot. Used in dev where the dev paths are
              expected.
- `info`    — informational; surfaced through `/readyz` so ops can
              confirm which subsystems are wired.

Production is signaled by `APP_ENV=production`. Any other value (or
unset) is treated as dev/staging. Keeping the trigger explicit means
nobody flips production on by accident.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Literal, Mapping

Severity = Literal["error", "warning", "info"]


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.code}: {self.message}"


@dataclass
class PreflightReport:
    is_production: bool
    findings: List[Finding] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def infos(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "info"]


class PreflightError(RuntimeError):
    """Raised on production with one or more error-level findings."""


def is_production(env: Mapping[str, str] | None = None) -> bool:
    env = env if env is not None else os.environ
    return env.get("APP_ENV", "").strip().lower() == "production"


def run_preflight(env: Mapping[str, str] | None = None) -> PreflightReport:
    """Inspect environment, return a report. Caller decides whether to raise."""
    env = env if env is not None else os.environ
    prod = is_production(env)
    report = PreflightReport(is_production=prod)

    # 1. Dev auth escape hatch must NOT be on in production.
    if (env.get("ALLOW_DEV_AUTH", "0") == "1"):
        if prod:
            report.findings.append(Finding(
                "error", "DEV_AUTH_IN_PROD",
                "ALLOW_DEV_AUTH=1 lets any client mint requests via the X-User-Id header. "
                "Refusing to boot. Unset it or set APP_ENV to something other than 'production'.",
            ))
        else:
            report.findings.append(Finding(
                "warning", "DEV_AUTH_ACTIVE",
                "ALLOW_DEV_AUTH=1 — anyone reaching the API can act as any user via X-User-Id. "
                "Fine for local dev; never set this in production.",
            ))

    # 2. ALLOW_INTERNAL_NO_OIDC bypasses the Cloud Tasks OIDC check on the
    # internal worker. Same shape as the dev-auth case.
    if (env.get("ALLOW_INTERNAL_NO_OIDC", "0") == "1"):
        if prod:
            report.findings.append(Finding(
                "error", "INTERNAL_NO_OIDC_IN_PROD",
                "ALLOW_INTERNAL_NO_OIDC=1 disables the OIDC check on /api/internal/tasks/run, "
                "letting anyone invoke arbitrary task handlers. Refusing to boot.",
            ))
        else:
            report.findings.append(Finding(
                "warning", "INTERNAL_NO_OIDC_ACTIVE",
                "Cloud Tasks OIDC verification is disabled — local-only.",
            ))

    # 3. Production needs Firebase Admin credentials so Bearer tokens
    # actually verify. (App imports succeed without them, but every
    # authenticated endpoint will return 503.)
    if prod and not (
        env.get("GOOGLE_APPLICATION_CREDENTIALS")
        or env.get("FIREBASE_PROJECT_ID")
    ):
        report.findings.append(Finding(
            "error", "FIREBASE_NOT_CONFIGURED",
            "Production requires Firebase Admin credentials. "
            "Set GOOGLE_APPLICATION_CREDENTIALS (path to service-account JSON) "
            "or FIREBASE_PROJECT_ID + Application Default Credentials.",
        ))

    # 4. CORS allowlist sanity — wildcard is unreachable through code today
    # (we already strip "*" in main.py), but warn if no production origin
    # was set and the app is in prod.
    if prod and not env.get("CORS_ALLOWED_ORIGINS"):
        report.findings.append(Finding(
            "warning", "CORS_DEFAULTING_TO_LOCALHOST",
            "CORS_ALLOWED_ORIGINS is unset in production; defaulting to localhost. "
            "Real browsers from your deployed frontend will be rejected. Set this.",
        ))

    # 4b. PDF rendering needs a Chromium build in the image. Not fatal -- the
    # endpoints return a clear 503 without it -- but silently losing the client
    # deliverable is worth a warning at boot rather than a support ticket.
    if prod and not env.get("DISABLE_PDF_RENDERING"):
        try:
            from app.services.pdf import is_available as _pdf_available

            if not _pdf_available():
                report.findings.append(Finding(
                    "warning", "PDF_RENDERING_UNAVAILABLE",
                    "Chromium is not available, so report PDF downloads will return "
                    "503. Add `playwright install --with-deps chromium` to the image, "
                    "or set DISABLE_PDF_RENDERING=1 to silence this.",
                ))
        except Exception:
            report.findings.append(Finding(
                "warning", "PDF_RENDERING_UNAVAILABLE",
                "Could not determine Chromium availability; report PDF downloads "
                "may return 503.",
            ))

    # 5. Encryption key must exist in production.
    if prod and not (
        env.get("ENCRYPTION_KEY")
        or env.get("GCP_PROJECT_ID")  # Secret Manager fallback
        or env.get("KMS_KEY_NAME")
    ):
        report.findings.append(Finding(
            "error", "NO_TOKEN_ENCRYPTION",
            "No token encryption configured. Set KMS_KEY_NAME (preferred) "
            "or ENCRYPTION_KEY (legacy Fernet path). Without one, OAuth "
            "tokens would round-trip a randomly-generated local key file.",
        ))

    # 6. Informational signals about wired subsystems — surfaced via /readyz.
    report.findings.append(Finding(
        "info", "AUTH_BACKEND",
        "firebase" if (env.get("GOOGLE_APPLICATION_CREDENTIALS") or env.get("FIREBASE_PROJECT_ID"))
        else "dev-header" if env.get("ALLOW_DEV_AUTH") == "1"
        else "none",
    ))
    report.findings.append(Finding(
        "info", "SECRETS_BACKEND",
        "secret-manager" if env.get("GCP_PROJECT_ID") else "env-vars",
    ))
    report.findings.append(Finding(
        "info", "TOKEN_ENCRYPTION",
        "kms-envelope" if env.get("KMS_KEY_NAME")
        else "fernet" if env.get("ENCRYPTION_KEY") or env.get("GCP_PROJECT_ID")
        else "local-keyfile",
    ))
    report.findings.append(Finding(
        "info", "EMAIL_BACKEND",
        "sendgrid" if env.get("SENDGRID_API_KEY") or env.get("GCP_PROJECT_ID")
        else "noop",
    ))
    report.findings.append(Finding(
        "info", "TASK_QUEUE",
        "cloud-tasks" if env.get("CLOUD_TASKS_QUEUE") else "in-process",
    ))

    return report


def run_or_raise(env: Mapping[str, str] | None = None) -> PreflightReport:
    """Run preflight, raise PreflightError on production errors, log warnings.

    Called at startup from `main.py`. The dev path is forgiving — warnings
    print to stderr but the app boots. Production errors raise so the
    process exits with a clear message in the orchestrator's logs.
    """
    import sys

    report = run_preflight(env)

    for f in report.errors:
        print(str(f), file=sys.stderr)
    for f in report.warnings:
        print(str(f), file=sys.stderr)

    if report.is_production and report.errors:
        codes = ", ".join(f.code for f in report.errors)
        raise PreflightError(f"Refusing to start with errors: {codes}")

    return report

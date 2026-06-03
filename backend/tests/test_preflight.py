"""Boot-time preflight: refuses dangerous configs in production, warns
in dev. Also surfaced through /readyz so ops can see at a glance whether
the deploy got the env right.

Tests are env-isolated — `run_preflight(env={...})` doesn't read the
process environment, so the global ALLOW_DEV_AUTH=1 used by the rest of
the test suite doesn't leak in.
"""
from app.preflight import (
    PreflightError,
    is_production,
    run_or_raise,
    run_preflight,
)


def test_is_production_only_matches_exact_string():
    assert is_production({"APP_ENV": "production"}) is True
    assert is_production({"APP_ENV": "Production"}) is True  # case-insensitive
    assert is_production({"APP_ENV": "prod"}) is False  # only "production"
    assert is_production({"APP_ENV": "staging"}) is False
    assert is_production({}) is False


class TestProductionErrors:
    def test_dev_auth_in_production_is_error(self):
        report = run_preflight({"APP_ENV": "production", "ALLOW_DEV_AUTH": "1"})
        codes = [f.code for f in report.errors]
        assert "DEV_AUTH_IN_PROD" in codes

    def test_internal_no_oidc_in_production_is_error(self):
        report = run_preflight({
            "APP_ENV": "production",
            "ALLOW_INTERNAL_NO_OIDC": "1",
            "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
            "ENCRYPTION_KEY": "x",
        })
        codes = [f.code for f in report.errors]
        assert "INTERNAL_NO_OIDC_IN_PROD" in codes

    def test_no_firebase_in_production_is_error(self):
        report = run_preflight({"APP_ENV": "production", "ENCRYPTION_KEY": "x"})
        codes = [f.code for f in report.errors]
        assert "FIREBASE_NOT_CONFIGURED" in codes

    def test_no_token_encryption_in_production_is_error(self):
        report = run_preflight({
            "APP_ENV": "production",
            "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
        })
        codes = [f.code for f in report.errors]
        assert "NO_TOKEN_ENCRYPTION" in codes


class TestProductionWarnings:
    def test_missing_cors_in_production_is_warning_not_error(self):
        """CORS defaulting to localhost won't break boot, but real browsers
        will be rejected — flag it loudly without refusing to start."""
        report = run_preflight({
            "APP_ENV": "production",
            "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
            "ENCRYPTION_KEY": "x",
        })
        warning_codes = [f.code for f in report.warnings]
        error_codes = [f.code for f in report.errors]
        assert "CORS_DEFAULTING_TO_LOCALHOST" in warning_codes
        assert "CORS_DEFAULTING_TO_LOCALHOST" not in error_codes


class TestDevMode:
    def test_dev_auth_in_dev_is_warning(self):
        report = run_preflight({"ALLOW_DEV_AUTH": "1"})
        codes = [f.code for f in report.warnings]
        assert "DEV_AUTH_ACTIVE" in codes
        assert report.errors == []

    def test_internal_no_oidc_in_dev_is_warning(self):
        report = run_preflight({"ALLOW_INTERNAL_NO_OIDC": "1"})
        codes = [f.code for f in report.warnings]
        assert "INTERNAL_NO_OIDC_ACTIVE" in codes
        assert report.errors == []

    def test_dev_with_no_firebase_does_not_error(self):
        """Dev should boot with zero config. Common for first-time setup."""
        report = run_preflight({})
        assert report.errors == []


class TestSubsystemInfo:
    """The infos drive what /readyz reports. Each must show *something*."""

    def test_no_config_reports_dev_defaults(self):
        report = run_preflight({})
        infos = {f.code: f.message for f in report.infos}
        assert infos["AUTH_BACKEND"] == "none"
        assert infos["SECRETS_BACKEND"] == "env-vars"
        assert infos["TOKEN_ENCRYPTION"] == "local-keyfile"
        assert infos["EMAIL_BACKEND"] == "noop"
        assert infos["TASK_QUEUE"] == "in-process"

    def test_full_prod_config_reports_real_backends(self):
        report = run_preflight({
            "APP_ENV": "production",
            "GOOGLE_APPLICATION_CREDENTIALS": "/sa.json",
            "GCP_PROJECT_ID": "p",
            "KMS_KEY_NAME": "projects/p/locations/us/keyRings/r/cryptoKeys/k",
            "SENDGRID_API_KEY": "sg.x",
            "CLOUD_TASKS_QUEUE": "projects/p/locations/us/queues/q",
            "CORS_ALLOWED_ORIGINS": "https://app.example.com",
        })
        infos = {f.code: f.message for f in report.infos}
        assert infos["AUTH_BACKEND"] == "firebase"
        assert infos["SECRETS_BACKEND"] == "secret-manager"
        assert infos["TOKEN_ENCRYPTION"] == "kms-envelope"
        assert infos["EMAIL_BACKEND"] == "sendgrid"
        assert infos["TASK_QUEUE"] == "cloud-tasks"


class TestRunOrRaise:
    def test_raises_on_production_errors(self):
        import pytest

        with pytest.raises(PreflightError) as exc:
            run_or_raise({"APP_ENV": "production", "ALLOW_DEV_AUTH": "1"})
        assert "DEV_AUTH_IN_PROD" in str(exc.value)

    def test_does_not_raise_on_dev_warnings(self):
        # Should return cleanly even with warnings.
        report = run_or_raise({"ALLOW_DEV_AUTH": "1"})
        assert report.warnings  # warnings exist
        assert report.errors == []


def test_readyz_returns_subsystems(client):
    """Integration: /readyz exposes the subsystem map so deploys can be
    verified end-to-end without log-diving."""
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert "subsystems" in body
    assert "warnings" in body
    assert "is_production" in body
    # The test session always sets ALLOW_DEV_AUTH=1, so we get a warning.
    warning_codes = [w["code"] for w in body["warnings"]]
    assert "DEV_AUTH_ACTIVE" in warning_codes

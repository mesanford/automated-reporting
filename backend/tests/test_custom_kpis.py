"""Custom KPI formula evaluator (safety-critical) + CRUD."""
import pytest

from tests.conftest import auth_headers


# ── Formula evaluator: safety + correctness ────────────────────────────────


class TestFormulaSafety:
    """The evaluator must reject any code path that would let users
    execute arbitrary Python. The AST allowlist is the whole point —
    these tests make sure that allowlist is actually enforced."""

    def test_disallows_attribute_access(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("totalSpend.__class__")

    def test_disallows_function_calls(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("__import__('os').system('rm -rf /')")

    def test_disallows_subscripts(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("totalSpend[0]")

    def test_disallows_unknown_variables(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("secret_variable + 1")

    def test_disallows_comparison_operators(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("totalSpend > 100")

    def test_rejects_empty_formula(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("")

    def test_rejects_syntax_errors(self):
        from app.services.kpi_formula import FormulaError, validate

        with pytest.raises(FormulaError):
            validate("totalSpend + ")


class TestFormulaCorrectness:
    def test_simple_arithmetic(self):
        from app.services.kpi_formula import evaluate

        v, err = evaluate(
            "totalSpend / totalConversions",
            {"totalSpend": 1000, "totalConversions": 20},
        )
        assert err is None
        assert v == 50.0

    def test_unary_minus(self):
        from app.services.kpi_formula import evaluate

        v, err = evaluate("-totalSpend + totalRevenue",
                          {"totalSpend": 1000, "totalRevenue": 1500})
        assert err is None
        assert v == 500.0

    def test_parentheses_and_precedence(self):
        from app.services.kpi_formula import evaluate

        v, err = evaluate(
            "(totalRevenue - totalSpend) / totalSpend",
            {"totalRevenue": 1500, "totalSpend": 1000},
        )
        assert err is None
        assert v == 0.5

    def test_division_by_zero_is_zero_not_error(self):
        """We prefer a sensible zero over a runtime error in a dashboard
        — analytics formulas often divide by metrics that are sometimes 0."""
        from app.services.kpi_formula import evaluate

        v, err = evaluate(
            "totalSpend / totalConversions",
            {"totalSpend": 1000, "totalConversions": 0},
        )
        assert err is None
        assert v == 0.0

    def test_missing_scorecard_treated_as_zero(self):
        from app.services.kpi_formula import evaluate

        v, err = evaluate("totalRevenue - totalSpend", {"totalSpend": 100})
        assert err is None
        assert v == -100.0

    def test_numeric_literals(self):
        from app.services.kpi_formula import evaluate

        v, err = evaluate("totalSpend * 1.15", {"totalSpend": 100})
        assert err is None
        assert v == pytest.approx(115.0)


# ── CRUD + role gating + evaluation endpoint ───────────────────────────────


@pytest.fixture()
def workspace(client, alice, bob):
    r = client.post("/api/workspaces", json={"name": "KPI Co"}, headers=auth_headers(alice))
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
    return ws_id


def test_member_cannot_create_kpi(client, workspace, bob):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/kpis",
        json={"name": "Margin", "formula": "totalRevenue - totalSpend", "format": "currency"},
        headers=auth_headers(bob, ws_id),
    )
    assert r.status_code == 403


def test_owner_creates_kpi(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/kpis",
        json={"name": "Margin", "formula": "totalRevenue - totalSpend", "format": "currency"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Margin"


def test_create_rejects_bad_formula(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/kpis",
        json={"name": "x", "formula": "__import__('os')", "format": "number"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_create_rejects_bad_format(client, workspace, alice):
    ws_id = workspace
    r = client.post(
        f"/api/workspaces/{ws_id}/kpis",
        json={"name": "x", "formula": "totalSpend", "format": "bogus"},
        headers=auth_headers(alice, ws_id),
    )
    assert r.status_code == 400


def test_evaluate_against_report(client, db, workspace, alice):
    ws_id = workspace
    client.post(
        f"/api/workspaces/{ws_id}/kpis",
        json={
            "name": "Margin",
            "formula": "totalRevenue - totalSpend",
            "format": "currency",
        },
        headers=auth_headers(alice, ws_id),
    )

    from app import models
    report = models.Report(
        workspace_id=ws_id, user_id=alice,
        scorecards={"totalSpend": 1000, "totalRevenue": 1750},
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    rows = client.get(
        f"/api/reports/{report.id}/kpis",
        headers=auth_headers(alice, ws_id),
    ).json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Margin"
    assert rows[0]["value"] == 750.0
    assert rows[0]["error"] is None


def test_variables_endpoint_lists_allowlist(client, alice):
    r = client.get("/api/kpi-variables", headers=auth_headers(alice))
    assert r.status_code == 200
    body = r.json()
    assert "totalSpend" in body["variables"]
    assert "blendedROAS" in body["variables"]
    assert "currency" in body["formats"]

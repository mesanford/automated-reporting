"""render_chart tool: returns a chart_spec envelope the frontend uses to
render inline charts in the conversation."""
import pytest


def test_render_chart_returns_spec():
    from app.services.analytics_tools import render_chart

    spec = render_chart(
        db=None,
        workspace_id=1,
        chart_type="line",
        title="Spend over time",
        x_label="Date",
        y_label="USD",
        data=[{"x": "2026-05-01", "y": 100}, {"x": "2026-05-02", "y": 150}],
    )
    assert spec["kind"] == "chart_spec"
    assert spec["chart_type"] == "line"
    assert spec["title"] == "Spend over time"
    assert len(spec["data"]) == 2


def test_render_chart_caps_data_at_200_rows():
    from app.services.analytics_tools import render_chart

    big = [{"x": i, "y": i} for i in range(500)]
    spec = render_chart(
        db=None, workspace_id=1, chart_type="bar",
        title="t", x_label="x", y_label="y", data=big,
    )
    assert len(spec["data"]) == 200


def test_render_chart_rejects_unknown_type():
    from app.services.analytics_tools import render_chart

    spec = render_chart(
        db=None, workspace_id=1, chart_type="pie",
        title="t", x_label="x", y_label="y", data=[{"x": 1, "y": 1}],
    )
    assert "error" in spec


def test_render_chart_rejects_empty_data():
    from app.services.analytics_tools import render_chart

    spec = render_chart(
        db=None, workspace_id=1, chart_type="line",
        title="t", x_label="x", y_label="y", data=[],
    )
    assert "error" in spec


def test_render_chart_is_registered_for_dispatch():
    from app.services.analytics_tools import TOOL_FUNCTIONS

    assert "render_chart" in TOOL_FUNCTIONS


def test_render_chart_appears_in_declarations():
    """Gemini needs the schema to know how to call the tool."""
    from app.services.analytics_tools import TOOL_DECLARATIONS

    names = [d["name"] for d in TOOL_DECLARATIONS]
    assert "render_chart" in names

    decl = next(d for d in TOOL_DECLARATIONS if d["name"] == "render_chart")
    required = decl["parameters"].get("required", [])
    for must in ("chart_type", "title", "x_label", "y_label", "data"):
        assert must in required


def test_chat_system_persona_mentions_render_chart():
    """The model needs to know that calling render_chart is in-scope."""
    from app.services.gemini import CHAT_SYSTEM_PERSONA

    assert "render_chart" in CHAT_SYSTEM_PERSONA

"""Analytics tools exposed to Gemini for conversational analytics.

Each function returns plain JSON-serializable dicts/lists. They read from
the existing `Report` and `Connection` tables — performance data already
lives inside Report JSON columns populated by services/etl.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import models


def _scorecards_summary(report: models.Report) -> Dict[str, Any]:
    sc = report.scorecards or {}
    return {
        "report_id": report.id,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "current_period": report.current_period_label,
        "prior_period": report.prior_period_label,
        "comparison_type": report.comparison_type,
        "total_spend": sc.get("totalSpend"),
        "total_conversions": sc.get("totalConversions"),
        "total_revenue": sc.get("totalRevenue"),
        "blended_cpa": sc.get("blendedCPA"),
        "blended_roas": sc.get("blendedROAS"),
        "blended_ctr": sc.get("blendedCTR"),
    }


def list_reports(db: Session, workspace_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """List the most recent reports for the user."""
    reports = (
        db.query(models.Report)
        .filter(models.Report.workspace_id == workspace_id)
        .order_by(models.Report.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_scorecards_summary(r) for r in reports]


def get_report_summary(db: Session, workspace_id: int, report_id: int) -> Dict[str, Any]:
    """Return scorecards, deltas, platform summary, and top/bottom performers for a report."""
    report = (
        db.query(models.Report)
        .filter(models.Report.id == report_id, models.Report.workspace_id == workspace_id)
        .first()
    )
    if not report:
        return {"error": f"Report {report_id} not found."}
    return {
        **_scorecards_summary(report),
        "scorecard_deltas": report.scorecard_deltas,
        "platform_summary": report.platform_summary,
        "platform_deltas": report.platform_deltas,
        "top_performer": report.top_performer,
        "bottom_performer": report.bottom_performer,
    }


def top_campaigns(
    db: Session,
    workspace_id: int,
    report_id: int,
    metric: str = "spend",
    limit: int = 10,
    platform: Optional[str] = None,
    ascending: bool = False,
) -> List[Dict[str, Any]]:
    """Return top-N campaigns from a report sorted by `metric`.

    metric: spend | conversions | revenue | cpa | ctr | cvr | cpc | cpm | roas
    """
    report = (
        db.query(models.Report)
        .filter(models.Report.id == report_id, models.Report.workspace_id == workspace_id)
        .first()
    )
    if not report or not report.campaign_summary:
        return []

    rows = list(report.campaign_summary)
    if platform:
        rows = [r for r in rows if str(r.get("platform", "")).lower() == platform.lower()]

    def _key(row: Dict[str, Any]) -> float:
        v = row.get(metric)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    rows.sort(key=_key, reverse=not ascending)
    return rows[:limit]


def aggregate_across_reports(
    db: Session,
    workspace_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: str = "platform",
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate metrics across multiple reports within a date range.

    `date_from` / `date_to` are ISO date strings (YYYY-MM-DD) compared against
    Report.created_at. `group_by` is one of: platform | campaign | report.
    """
    q = db.query(models.Report).filter(models.Report.workspace_id == workspace_id)
    if date_from:
        try:
            q = q.filter(models.Report.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(models.Report.created_at <= datetime.fromisoformat(date_to))
        except ValueError:
            pass

    reports = q.order_by(models.Report.created_at.asc()).all()
    if not reports:
        return {"groups": [], "report_count": 0}

    buckets: Dict[str, Dict[str, float]] = {}

    def _add(key: str, row: Dict[str, Any]) -> None:
        b = buckets.setdefault(
            key,
            {"spend": 0.0, "impressions": 0.0, "clicks": 0.0, "conversions": 0.0, "revenue": 0.0},
        )
        for m in ("spend", "impressions", "clicks", "conversions", "revenue"):
            try:
                b[m] += float(row.get(m) or 0)
            except (TypeError, ValueError):
                pass

    for report in reports:
        if group_by == "report":
            sc = report.scorecards or {}
            _add(
                f"report:{report.id}",
                {
                    "spend": sc.get("totalSpend"),
                    "impressions": sc.get("totalImpressions"),
                    "clicks": sc.get("totalClicks"),
                    "conversions": sc.get("totalConversions"),
                    "revenue": sc.get("totalRevenue"),
                },
            )
            continue

        source = report.platform_summary if group_by == "platform" else report.campaign_summary
        if not source:
            continue
        for row in source:
            if platform and str(row.get("platform", "")).lower() != platform.lower():
                continue
            key = str(row.get(group_by) or row.get("platform") or "unknown")
            _add(key, row)

    groups = []
    for key, b in buckets.items():
        spend = b["spend"]
        impressions = b["impressions"]
        clicks = b["clicks"]
        conversions = b["conversions"]
        revenue = b["revenue"]
        groups.append(
            {
                group_by: key,
                "spend": round(spend, 2),
                "impressions": int(impressions),
                "clicks": int(clicks),
                "conversions": int(conversions),
                "revenue": round(revenue, 2),
                "cpa": round(spend / conversions, 2) if conversions else None,
                "ctr": round((clicks / impressions) * 100, 2) if impressions else None,
                "roas": round(revenue / spend, 2) if spend else None,
            }
        )
    groups.sort(key=lambda g: g["spend"], reverse=True)
    return {
        "groups": groups,
        "report_count": len(reports),
        "date_from": date_from,
        "date_to": date_to,
    }


def list_budget_pacing(db: Session, workspace_id: int) -> List[Dict[str, Any]]:
    """Return current pacing for every active budget in the workspace.

    Lets the chat answer "are we on pace?" without forcing the user to
    leave the conversation.
    """
    from app.services.budgets import compute_pacing

    budgets = (
        db.query(models.Budget)
        .filter(
            models.Budget.workspace_id == workspace_id,
            models.Budget.is_active == 1,
        )
        .all()
    )
    return [
        {
            "id": b.id,
            "name": b.name,
            "scope": f"{b.scope_type}:{b.scope_key}" if b.scope_key else b.scope_type,
            "period_type": b.period_type,
            **compute_pacing(db, b),
        }
        for b in budgets
    ]


def render_chart(
    db: Session,
    workspace_id: int,
    *,
    chart_type: str,
    title: str,
    x_label: str,
    y_label: str,
    data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a chart spec for the frontend to render inline.

    The model calls this when the user asked for a visualization. We don't
    fetch data here — the model has already gathered it via the other
    tools (e.g. `aggregate_across_reports`) and is now packaging the
    result for display. Returning a tagged `chart_spec` envelope makes
    the rendering path unambiguous on the client side.

    `data` is a list of `{x, y}` pairs; on the frontend, x is the
    category/date and y is the value. Multi-series is one row per
    `{x, series, y}` triple.
    """
    if chart_type not in {"line", "bar"}:
        return {"error": f"Unsupported chart_type: {chart_type}"}
    if not isinstance(data, list) or not data:
        return {"error": "data must be a non-empty list of points"}

    # Best-effort: cap rows so the model can't accidentally cram a 1000-point chart.
    capped = data[:200]

    return {
        "kind": "chart_spec",
        "chart_type": chart_type,
        "title": title,
        "x_label": x_label,
        "y_label": y_label,
        "data": capped,
    }


def list_connections(db: Session, workspace_id: int) -> List[Dict[str, Any]]:
    """List active ad-platform connections for the user."""
    connections = (
        db.query(models.Connection)
        .filter(models.Connection.workspace_id == workspace_id, models.Connection.is_active == 1)
        .all()
    )
    return [
        {
            "id": c.id,
            "platform": c.platform,
            "account_name": c.account_name,
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "last_sync_status": c.last_sync_status,
        }
        for c in connections
    ]


# ── Tool dispatch table ──────────────────────────────────────────────────────

TOOL_FUNCTIONS = {
    "list_reports": list_reports,
    "get_report_summary": get_report_summary,
    "top_campaigns": top_campaigns,
    "aggregate_across_reports": aggregate_across_reports,
    "list_connections": list_connections,
    "list_budget_pacing": list_budget_pacing,
    "render_chart": render_chart,
}


def call_tool(name: str, db: Session, workspace_id: int, args: Dict[str, Any]) -> Any:
    """Dispatch a Gemini function call to the corresponding tool."""
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(db=db, workspace_id=workspace_id, **(args or {}))
    except TypeError as exc:
        return {"error": f"Invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{name} failed: {exc}"}


# ── Gemini FunctionDeclaration schemas ───────────────────────────────────────

TOOL_DECLARATIONS: List[Dict[str, Any]] = [
    {
        "name": "list_reports",
        "description": "List the user's most recent ad performance reports with scorecard summaries.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER", "description": "Max number of reports to return (default 20)."},
            },
        },
    },
    {
        "name": "get_report_summary",
        "description": "Get scorecards, period deltas, platform breakdown, and top/bottom performers for one report.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "report_id": {"type": "INTEGER", "description": "ID of the report."},
            },
            "required": ["report_id"],
        },
    },
    {
        "name": "top_campaigns",
        "description": "Return the top-N campaigns from a single report sorted by a metric.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "report_id": {"type": "INTEGER"},
                "metric": {
                    "type": "STRING",
                    "description": "spend | conversions | revenue | cpa | ctr | cvr | cpc | cpm | roas",
                },
                "limit": {"type": "INTEGER"},
                "platform": {"type": "STRING", "description": "Optional platform filter (google, meta, linkedin, tiktok, microsoft)."},
                "ascending": {"type": "BOOLEAN", "description": "Ascending order (useful for worst performers by CPA)."},
            },
            "required": ["report_id"],
        },
    },
    {
        "name": "aggregate_across_reports",
        "description": "Aggregate spend/clicks/conversions/revenue across multiple reports in a date range, grouped by platform, campaign, or report.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "ISO date (YYYY-MM-DD) lower bound on report creation."},
                "date_to": {"type": "STRING", "description": "ISO date (YYYY-MM-DD) upper bound on report creation."},
                "group_by": {"type": "STRING", "description": "platform | campaign | report"},
                "platform": {"type": "STRING", "description": "Optional platform filter."},
            },
        },
    },
    {
        "name": "list_connections",
        "description": "List the user's active ad-platform connections (Google Ads, Meta, LinkedIn, TikTok, Microsoft).",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "list_budget_pacing",
        "description": (
            "Return current pacing for every active budget in the workspace. "
            "Each row carries period, amount, spent, target_at_today, pace_pct, "
            "pct_used, and status (on_pace|under_pace|over_pace|exhausted)."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "render_chart",
        "description": (
            "Render an inline chart in the chat. Call this when the user asks "
            "for a visualization or when a chart would be clearer than a table. "
            "Gather the data via other tools first, then pass the rows here."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "chart_type": {"type": "STRING", "description": "line | bar"},
                "title": {"type": "STRING"},
                "x_label": {"type": "STRING"},
                "y_label": {"type": "STRING"},
                "data": {
                    "type": "ARRAY",
                    "description": "Rows like {x: 'Google', y: 1234} or {x: '2026-05-01', series: 'Meta', y: 200}.",
                    "items": {"type": "OBJECT"},
                },
            },
            "required": ["chart_type", "title", "x_label", "y_label", "data"],
        },
    },
]

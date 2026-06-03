"""Budget pacing calculator.

Given a `Budget`, compute:
- `period_start` / `period_end` — boundaries of the current period.
- `spent` — sum of spend from workspace reports within the period,
  scoped by the budget's scope_type/key.
- `target_at_today` — linear pro-rated target (budget * days_elapsed / days_in_period).
- `pace_pct` — spent / target_at_today (1.0 = exactly on pace).
- `pct_used` — spent / amount.
- `status` — `on_pace` (90-110%), `under_pace` (<90%), `over_pace` (>110%),
  `exhausted` (pct_used >= 100%).

The computation reads spend out of the `Report.platform_summary` JSON
column rather than the raw connection rows; this matches how the
existing analytics tools work and stays consistent with the cross-workspace
isolation invariants.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models


def _period_bounds(
    period_type: str, now: datetime, start_date: datetime
) -> Tuple[datetime, datetime, str]:
    """Return (start, end, label) for the period containing `now`.

    `start_date` anchors the start of the *first* period; subsequent periods
    roll forward. For monthly: each calendar month. For quarterly: Jan-Mar,
    Apr-Jun, Jul-Sep, Oct-Dec.
    """
    if period_type == "monthly":
        ps = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(ps.year, ps.month)[1]
        pe = ps.replace(day=last_day, hour=23, minute=59, second=59)
        label = ps.strftime("%Y-%m")
        return ps, pe, label

    if period_type == "quarterly":
        q_start_month = ((now.month - 1) // 3) * 3 + 1
        ps = now.replace(
            month=q_start_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        q_end_month = q_start_month + 2
        last_day = calendar.monthrange(ps.year, q_end_month)[1]
        pe = ps.replace(month=q_end_month, day=last_day, hour=23, minute=59, second=59)
        label = f"{ps.year}-Q{(q_start_month - 1) // 3 + 1}"
        return ps, pe, label

    raise ValueError(f"Unsupported period_type: {period_type!r}")


def _sum_spend_for_budget(
    db: Session, budget: models.Budget, period_start: datetime, period_end: datetime
) -> float:
    """Sum spend across the workspace's reports within the period, scoped
    by the budget's scope."""
    reports = (
        db.query(models.Report)
        .filter(
            models.Report.workspace_id == budget.workspace_id,
            models.Report.created_at >= period_start,
            models.Report.created_at <= period_end,
        )
        .all()
    )

    total = 0.0
    for r in reports:
        scorecards = r.scorecards or {}
        if budget.scope_type == "workspace":
            v = scorecards.get("totalSpend")
            if v is not None:
                try:
                    total += float(v)
                except (TypeError, ValueError):
                    pass
        elif budget.scope_type == "platform":
            for row in (r.platform_summary or []):
                if str(row.get("platform", "")).lower() == str(budget.scope_key).lower():
                    try:
                        total += float(row.get("spend") or 0)
                    except (TypeError, ValueError):
                        pass
        elif budget.scope_type == "connection":
            # We don't track per-connection spend on Reports today (the
            # ETL aggregates across all selected accounts of a connection
            # into the platform_summary). Approximate by attributing the
            # platform sum to whichever connection matches the platform.
            # Future schema can carry connection_id through; this is a
            # best-effort that's still useful.
            try:
                conn_id = int(budget.scope_key)
            except (TypeError, ValueError):
                continue
            conn = db.query(models.Connection).filter(models.Connection.id == conn_id).first()
            if not conn:
                continue
            for row in (r.platform_summary or []):
                if str(row.get("platform", "")).lower() == str(conn.platform).lower():
                    try:
                        total += float(row.get("spend") or 0)
                    except (TypeError, ValueError):
                        pass
    return round(total, 2)


def compute_pacing(
    db: Session, budget: models.Budget, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Return the pacing envelope the API + UI display."""
    now = now or datetime.utcnow()
    ps, pe, label = _period_bounds(budget.period_type, now, budget.start_date)

    try:
        amount = float(budget.amount)
    except (TypeError, ValueError):
        amount = 0.0

    spent = _sum_spend_for_budget(db, budget, ps, pe)

    days_total = max(1, (pe.date() - ps.date()).days + 1)
    days_elapsed = max(0, min(days_total, (now.date() - ps.date()).days + 1))
    target = amount * (days_elapsed / days_total)

    pace_pct = (spent / target) if target > 0 else (1.0 if spent == 0 else float("inf"))
    pct_used = (spent / amount) if amount > 0 else 0.0

    if pct_used >= 1.0:
        status = "exhausted"
    elif pace_pct > 1.10:
        status = "over_pace"
    elif pace_pct < 0.90:
        status = "under_pace"
    else:
        status = "on_pace"

    return {
        "budget_id": budget.id,
        "period_label": label,
        "period_start": ps.isoformat(),
        "period_end": pe.isoformat(),
        "amount": round(amount, 2),
        "spent": spent,
        "target_at_today": round(target, 2),
        "pace_pct": round(pace_pct, 3) if pace_pct != float("inf") else None,
        "pct_used": round(pct_used, 3),
        "status": status,
        "days_elapsed": days_elapsed,
        "days_total": days_total,
    }


def evaluate_budget_alerts(
    db: Session,
    *,
    workspace_id: int,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Run after each sync. For every active budget with `alert_at_pct`
    configured, fire a notification once per period if `pct_used` crosses
    that threshold. Dedupes via `last_alert_period`."""
    from app.services.notifications import send_google_chat_message
    import os

    now = now or datetime.utcnow()
    budgets = (
        db.query(models.Budget)
        .filter(
            models.Budget.workspace_id == workspace_id,
            models.Budget.is_active == 1,
            models.Budget.alert_at_pct.isnot(None),
        )
        .all()
    )

    triggered: List[Dict[str, Any]] = []
    for budget in budgets:
        pacing = compute_pacing(db, budget, now=now)
        threshold = (budget.alert_at_pct or 0) / 100.0
        if pacing["pct_used"] < threshold:
            continue
        if budget.last_alert_period == pacing["period_label"]:
            continue  # already alerted this period

        # Audit + dedupe stamp.
        db.add(models.AuditLog(
            workspace_id=workspace_id,
            actor_subject=budget.created_by_subject or "system",
            action="budget.threshold_crossed",
            target_type="budget",
            target_id=str(budget.id),
            payload={
                "name": budget.name,
                "period": pacing["period_label"],
                "pct_used": pacing["pct_used"],
                "spent": pacing["spent"],
                "amount": pacing["amount"],
            },
        ))
        budget.last_alert_at = now
        budget.last_alert_period = pacing["period_label"]

        # Best-effort fan-out via the workspace's Google Chat webhook if
        # configured. Slack/email channels are an alert-rule concern; here
        # we use the simplest path.
        webhook = (
            db.query(models.UserSettings)
            .filter(models.UserSettings.workspace_id == workspace_id)
            .first()
        )
        if webhook and webhook.google_chat_webhook:
            send_google_chat_message(
                webhook.google_chat_webhook,
                f"💰 *Budget alert: {budget.name}* — {pacing['pct_used']*100:.0f}% of "
                f"${pacing['amount']:.0f} used in {pacing['period_label']} "
                f"(spent ${pacing['spent']:.0f}).",
            )

        triggered.append({
            "budget_id": budget.id,
            "name": budget.name,
            "pct_used": pacing["pct_used"],
            "period": pacing["period_label"],
        })

    db.commit()
    return triggered

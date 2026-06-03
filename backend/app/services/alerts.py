"""Alert rule evaluation.

Each rule is checked against a freshly-produced Report. The supported
comparisons cover the common ad-ops alert shapes:

- `gt` / `lt`: absolute threshold on a scorecard metric.
- `pct_change_gt`: trigger if |percent_change| exceeds the threshold
  (read from `scorecard_deltas`, which the ETL already computes).

Triggered rules fan out to every configured channel and write an
`alert.trigger` audit entry per rule. Non-triggered rules are a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services import notifications

logger = logging.getLogger(__name__)


def _scorecard_value(report: models.Report, metric: str) -> Optional[float]:
    scorecards = report.scorecards or {}
    val = scorecards.get(metric)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _delta_pct(report: models.Report, metric: str) -> Optional[float]:
    """Pull |percent change| for `metric` from scorecard_deltas."""
    deltas = report.scorecard_deltas or {}
    entry = deltas.get(metric) or deltas.get(metric.replace("total", "").lower())
    if not isinstance(entry, dict):
        return None
    raw = entry.get("value")
    if not isinstance(raw, str):
        return None
    # Format is like "+12.3%" or "-4.5%".
    try:
        return abs(float(raw.replace("%", "").replace("+", "")))
    except ValueError:
        return None


def _evaluate_one(
    rule: models.AlertRule, report: models.Report
) -> Tuple[bool, Optional[float]]:
    """Return (triggered, observed_value)."""
    try:
        threshold = float(rule.threshold)
    except (TypeError, ValueError):
        return False, None

    if rule.comparison == "gt":
        v = _scorecard_value(report, rule.metric)
        return (v is not None and v > threshold), v
    if rule.comparison == "lt":
        v = _scorecard_value(report, rule.metric)
        return (v is not None and v < threshold), v
    if rule.comparison == "pct_change_gt":
        v = _delta_pct(report, rule.metric)
        return (v is not None and v > threshold), v
    return False, None


def evaluate_rules_for_report(
    db: Session,
    *,
    workspace_id: int,
    report: models.Report,
) -> List[Dict[str, Any]]:
    """Run every active alert rule for the workspace against the report.

    Returns a list of dicts describing what triggered + the channel
    delivery envelopes, so callers can log or surface them.
    """
    rules = (
        db.query(models.AlertRule)
        .filter(
            models.AlertRule.workspace_id == workspace_id,
            models.AlertRule.is_active == 1,
        )
        .all()
    )

    triggered: List[Dict[str, Any]] = []
    for rule in rules:
        fired, observed = _evaluate_one(rule, report)
        if not fired:
            continue

        subject = f"[Antigravity] Alert: {rule.name}"
        body = (
            f"Rule: {rule.name}\n"
            f"Metric: {rule.metric}\n"
            f"Comparison: {rule.comparison} {rule.threshold}\n"
            f"Observed: {observed}\n"
            f"Report: {report.id} ({report.current_period_label or 'current'})\n"
        )

        from app.api.alerts import decrypt_channels_for_delivery

        deliveries = [
            notifications.send_to_channel(c, subject, body)
            for c in decrypt_channels_for_delivery(rule.channels)
        ]

        rule.last_triggered_at = datetime.utcnow()
        rule.last_value = "" if observed is None else f"{observed}"

        # Audit entry per trigger.
        db.add(models.AuditLog(
            workspace_id=workspace_id,
            actor_subject="scheduler" if rule.created_by_subject == "scheduler" else (rule.created_by_subject or "system"),
            action="alert.trigger",
            target_type="alert_rule",
            target_id=str(rule.id),
            payload={
                "name": rule.name,
                "metric": rule.metric,
                "observed": observed,
                "report_id": report.id,
                "deliveries": deliveries,
            },
        ))

        triggered.append({
            "rule_id": rule.id,
            "name": rule.name,
            "observed": observed,
            "deliveries": deliveries,
        })

    db.commit()
    return triggered

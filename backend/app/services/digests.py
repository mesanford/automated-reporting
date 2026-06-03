"""Email digest renderer + scheduler tick.

A digest is a short text snapshot of the workspace's most recent report
plus any active budgets that are off-pace. Designed to be read at a
glance on a phone — no charts, no markdown, just numbers with units.

Cadence is `daily` or `weekly`; same `next_send_at` advancement pattern
as `ScheduledSync`. Cloud Scheduler hits `/api/internal/digests/tick`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services.budgets import compute_pacing

logger = logging.getLogger(__name__)


def compute_next_send(cadence: str, *, now: Optional[datetime] = None) -> datetime:
    """Daily → next 13:00 UTC; weekly → next Monday 13:00 UTC.

    The fixed times are deliberate: digest emails want to hit inboxes at
    a predictable time of day, not whenever the user subscribed."""
    now = now or datetime.utcnow()
    base = now.replace(hour=13, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        return base if base > now else base + timedelta(days=1)
    if cadence == "weekly":
        # Monday=0
        days_ahead = (0 - now.weekday()) % 7
        candidate = base + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate
    raise ValueError(f"Unsupported cadence: {cadence!r}")


def _format_money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.0f}"


def render_digest_body(
    db: Session, sub: models.DigestSubscription
) -> Tuple[str, str]:
    """Return (subject, plain-text body) for this subscription's workspace."""
    ws = (
        db.query(models.Workspace)
        .filter(models.Workspace.id == sub.workspace_id)
        .first()
    )
    if not ws:
        return ("Antigravity digest", "(workspace no longer exists)")

    currency = ws.base_currency or "USD"

    # Most recent report.
    report = (
        db.query(models.Report)
        .filter(models.Report.workspace_id == sub.workspace_id)
        .order_by(models.Report.created_at.desc())
        .first()
    )

    # Active budgets sorted by status — off-pace ones surface first.
    budgets = (
        db.query(models.Budget)
        .filter(
            models.Budget.workspace_id == sub.workspace_id,
            models.Budget.is_active == 1,
        )
        .all()
    )

    pacings: List[Dict[str, Any]] = [
        {"name": b.name, **compute_pacing(db, b)} for b in budgets
    ]
    pacings.sort(key=lambda p: (
        0 if p["status"] == "exhausted" else
        1 if p["status"] == "over_pace" else
        2 if p["status"] == "under_pace" else 3
    ))

    cadence_label = "Daily" if sub.cadence == "daily" else "Weekly"
    subject = f"[Antigravity] {cadence_label} digest — {ws.name}"

    lines: List[str] = []
    lines.append(f"{cadence_label} digest for {ws.name}")
    lines.append(f"As of {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    if report:
        sc = report.scorecards or {}
        lines.append(f"LATEST REPORT — {report.current_period_label or f'#{report.id}'}")
        lines.append(
            f"  Spend:       {_format_money(float(sc.get('totalSpend') or 0), currency)}"
        )
        lines.append(
            f"  Conversions: {int(sc.get('totalConversions') or 0):,}"
        )
        lines.append(
            f"  Blended CPA: {_format_money(float(sc.get('blendedCPA') or 0), currency)}"
        )
        roas = sc.get("blendedROAS")
        if roas is not None:
            lines.append(f"  ROAS:        {roas}x")
        lines.append("")
    else:
        lines.append("(No reports yet in this workspace.)")
        lines.append("")

    if pacings:
        lines.append("BUDGETS")
        for p in pacings:
            tone = {
                "exhausted": "⚠️ exhausted",
                "over_pace":  "▲ over pace",
                "under_pace": "▼ under pace",
                "on_pace":    "✓ on pace",
            }.get(p["status"], p["status"])
            lines.append(
                f"  {tone}  {p['name']}: "
                f"{_format_money(p['spent'], currency)} / "
                f"{_format_money(p['amount'], currency)} "
                f"({int(p['pct_used'] * 100)}%) — {p['period_label']}"
            )
        lines.append("")

    lines.append("View the full dashboard:")
    lines.append("  (sign in to your Antigravity workspace)")
    lines.append("")
    lines.append("Manage or unsubscribe in workspace Settings → Digests.")

    return subject, "\n".join(lines)


def send_due_digests(
    db: Session, *, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Find all active subscriptions whose `next_send_at` is in the past,
    render + send each, advance `next_send_at`. Returns a list of
    delivery summaries for the tick endpoint to surface.

    On delivery failure we still advance `next_send_at` — otherwise a
    permanently-broken address would back up forever — but record the
    error on the row so the user can see what went wrong."""
    from app.services import notifications

    now = now or datetime.utcnow()
    due = (
        db.query(models.DigestSubscription)
        .filter(
            models.DigestSubscription.is_active == 1,
            models.DigestSubscription.next_send_at <= now,
        )
        .all()
    )

    sent: List[Dict[str, Any]] = []
    for sub in due:
        subject, body = render_digest_body(db, sub)
        delivery = notifications.send_to_channel(
            {"type": "email", "to": sub.email}, subject, body,
        )
        sub.last_sent_at = now
        sub.last_send_error = None if delivery.get("delivered") else delivery.get("error")
        sub.next_send_at = compute_next_send(sub.cadence, now=now)
        sent.append({
            "subscription_id": sub.id,
            "email": sub.email,
            "delivered": bool(delivery.get("delivered")),
            "error": delivery.get("error"),
        })

    db.commit()
    return sent

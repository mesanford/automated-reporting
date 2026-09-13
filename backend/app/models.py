from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, Integer, String, DateTime, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

# Portable JSON: compiles to JSONB on Postgres (indexable/queryable), JSON on SQLite.
JSONPortable = JSON().with_variant(JSONB(), "postgresql")

# NOTE on nullability: every mapped_column() below passes `nullable=` explicitly
# rather than relying on Mapped[]/Optional[] type inference, so the actual DDL
# is byte-for-byte identical to the pre-migration Column()-based declarations —
# this was a typing-only migration (Column[T] -> Mapped[T] for real mypy
# support), not a schema change. Where a column has a Python-side `default=`
# and is practically always populated (timestamps, status strings), the
# Mapped[] hint is left non-Optional for ergonomics even though the DB column
# itself may still permit NULL; the explicit `nullable=` kwarg is what governs
# actual behavior, and always wins over the annotation.


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # creator (audit)
    connection_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=True)  # pending, running, completed, failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    current_step: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g., "Discovering accounts", "Fetching data"
    total_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    accounts_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    total_accounts: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Cumulative detailed logs
    report_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Reference to generated report after completion
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # creator (audit)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    chart_data: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    scorecards: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    scorecard_deltas: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    platform_deltas: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    comparison_type: Mapped[str] = mapped_column(String, default="none", nullable=True)
    current_period_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prior_period_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    campaign_summary: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    hierarchy_summary: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    platform_summary: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    top_performer: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    bottom_performer: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    gemini_analysis: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 1 if any source row was fabricated (demo mode). NOT NULL server_default
    # "0" at the DB level (see migration c1e2f3a4b5c9) — nullable=False here
    # keeps the model in sync with that.
    used_mock_data: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # who connected it (audit)
    platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # google, meta, linkedin, tiktok, ...
    account_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    account_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    access_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Manager/customer context this connection acts through (Google Ads
    # login-customer-id). Per-connection, never process-wide: two tenants
    # reaching the same API need different manager contexts.
    login_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=True)
    available_accounts: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    selected_account_ids: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # success, failed, pending
    last_sync_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class UserSettings(Base):
    """Workspace-scoped settings. Despite the legacy table name, the
    `google_chat_webhook` is a workspace concern (e.g. all teammates share
    the channel), so uniqueness is now on workspace_id, not user_id."""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), unique=True, index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # last editor (audit)
    google_chat_webhook: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class OptimizationPlan(Base):
    __tablename__ = "optimization_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # creator (audit)
    connection_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # google, meta, etc.
    campaign_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ad_group_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    change_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # budget_increase, pause_underperforming, etc.
    original_value: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    proposed_value: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=True)  # pending, approved, rejected, executed, failed
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_automated: Mapped[int] = mapped_column(Integer, default=0, nullable=True)  # 1 if automated, 0 if manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # author
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    visibility: Mapped[str] = mapped_column(String, nullable=False, default="private")  # private | workspace
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("conversations.id"), index=True, nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # user | assistant | tool
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tool_payload: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class OptimizationRule(Base):
    __tablename__ = "optimization_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # creator (audit), not data scope
    platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    change_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # Identifies which type of change is automated
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


# ── Multi-workspace tables ──────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    auth_subject: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, unique=True, index=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # FK-by-value to users.auth_subject
    base_currency: Mapped[str] = mapped_column(String, nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class FxRate(Base):
    """Daily-cached FX rates keyed by (date, from, to).

    Backs `services/fx.convert(amount, from_ccy, to_ccy)`. We hit the
    Frankfurter open API at most once per (from, to, day) pair across all
    workspaces; everything else reads from this row.
    """
    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    as_of_date: Mapped[str] = mapped_column(String, nullable=False, index=True)  # YYYY-MM-DD
    from_currency: Mapped[str] = mapped_column(String, nullable=False, index=True)
    to_currency: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rate: Mapped[str] = mapped_column(String, nullable=False)  # decimal stored as string
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "as_of_date", "from_currency", "to_currency", name="uq_fx_rate_day_pair"
        ),
    )


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_subject: Mapped[str] = mapped_column(String, index=True, nullable=False)  # users.auth_subject
    role: Mapped[str] = mapped_column(String, nullable=False, default="owner")  # owner | admin | member | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_subject", name="uq_membership_workspace_user"),
    )


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    invited_by_subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class ScheduledSync(Base):
    """One recurring sync-all per row. Evaluated by the scheduler tick,
    which picks rows with `next_run_at <= now AND is_active=1`, enqueues
    sync_all_connections, then advances next_run_at."""
    __tablename__ = "scheduled_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    frequency: Mapped[str] = mapped_column(String, nullable=False)  # daily | weekly
    hour_utc: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-23
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0=Mon..6=Sun, only for weekly
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class AlertRule(Base):
    """Threshold-based alerts evaluated after each sync.

    `metric` is one of the scorecard keys (totalSpend, blendedCPA, ...).
    `comparison` is gt|lt|pct_change_gt. For pct_change_gt, the threshold
    is a percentage; we compare current vs prior period from scorecard_deltas.
    `channels` is a JSON array of channel specs, e.g. [{"type":"slack","url":"..."}].
    """
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    comparison: Mapped[str] = mapped_column(String, nullable=False)  # gt | lt | pct_change_gt
    threshold: Mapped[str] = mapped_column(String, nullable=False)  # stored as string to keep migration simple
    channels: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)  # list of channel specs
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_value: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Budget(Base):
    """Spend ceiling for a workspace, platform, or single connection.

    `scope_type` is one of `workspace` (total across all platforms),
    `platform` (one platform, name in `scope_key`), or `connection`
    (one Connection.id stringified in `scope_key`).

    `period_type` is `monthly` or `quarterly`. Pacing math is calculated
    relative to the current period boundaries derived from `start_date`.

    `alert_at_pct` (e.g. 80) fires a notification when spent / amount
    crosses that fraction. `last_alert_at` records the most recent
    firing so we don't repeatedly alert within the same period.
    """
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    scope_type: Mapped[str] = mapped_column(String, nullable=False)  # workspace | platform | connection
    scope_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # platform name or connection id string
    period_type: Mapped[str] = mapped_column(String, nullable=False)  # monthly | quarterly
    amount: Mapped[str] = mapped_column(String, nullable=False)  # USD, stored as string for migration simplicity
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    alert_at_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0..100, NULL disables
    last_alert_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_alert_period: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. "2026-05" to dedupe within period
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class SavedView(Base):
    """User-defined dashboard filters/preferences.

    `config` is a small JSON blob (`{platform_filter, metric_focus, ...}`)
    that the dashboard interprets. Visibility `private` (only the author
    sees it) or `workspace` (everyone in the workspace).
    """
    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String, index=True, nullable=False)  # creator
    name: Mapped[str] = mapped_column(String, nullable=False)
    visibility: Mapped[str] = mapped_column(String, nullable=False, default="private")  # private | workspace
    config: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    is_default: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # only one per (workspace, user)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class DigestSubscription(Base):
    """Per-user email digest. Owner of the row is `user_subject`; the
    row itself is workspace-scoped so the digest content can be limited
    to that workspace's data.

    `next_send_at` is the scheduler tick's key — same pattern as
    ScheduledSync. `last_sent_at` records the most recent send for dedupe
    + display."""
    __tablename__ = "digest_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_subject: Mapped[str] = mapped_column(String, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    cadence: Mapped[str] = mapped_column(String, nullable=False)  # daily | weekly
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    next_send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_send_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_subject", name="uq_digest_workspace_user"),
    )


class ReportShareLink(Base):
    """Read-only public link to a Report.

    `token_hash` is sha256 of the raw token (the raw token is only ever
    returned at mint time and never stored). Expired links 410-Gone.
    Revoked links flip `is_active=0` without affecting the row, so we
    keep the audit trail.
    """
    __tablename__ = "report_share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("reports.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class CustomKpi(Base):
    """User-defined metric formulas evaluated against a Report's scorecards.

    `formula` is restricted to arithmetic on existing scorecard names
    (totalSpend, totalConversions, totalRevenue, totalImpressions,
    totalClicks, blendedCPA, blendedCTR, blendedCVR, blendedCPC,
    blendedCPM, blendedROAS) plus literals. Evaluated by
    `services/kpi_formula.evaluate` with an AST-allowlist parser — no
    string `eval`, no attribute access.

    `format`: how the dashboard renders the result ('currency', 'percent',
    'ratio', 'number', 'integer').
    """
    __tablename__ = "custom_kpis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False, default="number")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_subject: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)


class AuditLog(Base):
    """Append-only record of consequential workspace actions.

    Written at every spend-impacting or governance action — execute, approve,
    delete connection, invite, role change, member remove. Read via
    GET /api/workspaces/{id}/audit-log (owner/admin only).
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    actor_subject: Mapped[str] = mapped_column(String, index=True, nullable=False)  # who did it
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)  # e.g. "optimization.execute"
    target_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "optimization_plan", "connection", "membership"
    target_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[Optional[Any]] = mapped_column(JSONPortable, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=True)


class AdCreative(Base):
    """One ad creative pulled from a platform, scoped to a workspace.

    Ported from the standalone gallery's Firestore `ad_creatives` collection.
    Identity is `(workspace_id, platform, ad_id)` — the Postgres equivalent of
    that app's `{platform}_{ad_id}` document ID, plus the workspace dimension
    it had no concept of.

    The review_* columns are written ONLY by the review endpoint and are
    deliberately excluded from the sync upsert's update set. That is what makes
    a re-sync preserve reviews, and is the direct equivalent of the gallery's
    `batch.set(..., merge=True)` fix.
    """
    __tablename__ = "ad_creatives"
    __table_args__ = (
        UniqueConstraint("workspace_id", "platform", "ad_id", name="uq_ad_creatives_ws_platform_ad"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    connection_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    platform: Mapped[str] = mapped_column(String, index=True, nullable=False)  # meta, google, microsoft
    ad_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    account_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    ad_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    headline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ad_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    campaign_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ad_group_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    creative_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # IMAGE, YOUTUBE_VIDEO, RESPONSIVE_SEARCH_AD, ...
    group_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # "Ad Group" / "Asset Group" (Google)
    final_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Where the asset came from on the platform. Meta signs these and they
    # expire, which is why the bytes are mirrored rather than hotlinked.
    source_asset_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Object path inside the creatives bucket / local asset dir. NULL when the
    # creative is text-only or the download failed.
    asset_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    asset_content_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Set instead of asset_path for assets that cannot be stored as files —
    # YouTube video assets keep their embed URL, as in the source pipeline.
    embed_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    review_status: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # keep, remove, change
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=True)

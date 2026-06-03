from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from .database import Base

# Portable JSON: compiles to JSONB on Postgres (indexable/queryable), JSON on SQLite.
JSONPortable = JSON().with_variant(JSONB(), "postgresql")

class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # creator (audit)
    connection_id = Column(Integer, index=True)
    status = Column(String, default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    progress_percent = Column(Integer, default=0)
    current_step = Column(String)  # e.g., "Discovering accounts", "Fetching data"
    total_steps = Column(Integer, default=0)
    accounts_synced = Column(Integer, default=0)
    total_accounts = Column(Integer, default=0)
    error_message = Column(String)
    logs = Column(Text)  # Cumulative detailed logs
    report_id = Column(Integer)  # Reference to generated report after completion
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # creator (audit)
    created_at = Column(DateTime, default=datetime.utcnow)
    chart_data = Column(JSONPortable)
    scorecards = Column(JSONPortable)
    scorecard_deltas = Column(JSONPortable)
    platform_deltas = Column(JSONPortable)
    comparison_type = Column(String, default="none")
    current_period_label = Column(String)
    prior_period_label = Column(String)
    campaign_summary = Column(JSONPortable)
    hierarchy_summary = Column(JSONPortable)
    platform_summary = Column(JSONPortable)
    top_performer = Column(JSONPortable)
    bottom_performer = Column(JSONPortable)
    gemini_analysis = Column(String)

class Connection(Base):
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # who connected it (audit)
    platform = Column(String)  # google, meta, linkedin, tiktok
    account_id = Column(String)
    account_name = Column(String)
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(DateTime)
    is_active = Column(Integer, default=1)
    available_accounts = Column(JSONPortable)
    selected_account_ids = Column(JSONPortable)
    last_sync_at = Column(DateTime)
    last_sync_status = Column(String)  # success, failed, pending
    last_sync_job_id = Column(Integer)

class UserSettings(Base):
    """Workspace-scoped settings. Despite the legacy table name, the
    `google_chat_webhook` is a workspace concern (e.g. all teammates share
    the channel), so uniqueness is now on workspace_id, not user_id."""
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), unique=True, index=True, nullable=False)
    user_id = Column(String, index=True)  # last editor (audit)
    google_chat_webhook = Column(String, nullable=True)

class OptimizationPlan(Base):
    __tablename__ = "optimization_plans"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # creator (audit)
    connection_id = Column(Integer, index=True)
    platform = Column(String)  # google, meta, etc.
    campaign_name = Column(String, nullable=True)
    ad_group_name = Column(String, nullable=True)
    change_type = Column(String)  # budget_increase, pause_underperforming, etc.
    original_value = Column(JSONPortable)
    proposed_value = Column(JSONPortable)
    status = Column(String, default="pending")  # pending, approved, rejected, executed, failed
    reasoning = Column(Text)
    is_automated = Column(Integer, default=0)  # 1 if automated, 0 if manual
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # author
    title = Column(String)
    visibility = Column(String, nullable=False, default="private")  # private | workspace
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    role = Column(String)  # user | assistant | tool
    content = Column(Text)
    tool_name = Column(String, nullable=True)
    tool_call_id = Column(String, nullable=True)
    tool_payload = Column(JSONPortable, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class OptimizationRule(Base):
    __tablename__ = "optimization_rules"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True)  # creator (audit), not data scope
    platform = Column(String)
    change_type = Column(String)  # Identifies which type of change is automated
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Multi-workspace tables ──────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    auth_subject = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    created_by_subject = Column(String, index=True)  # FK-by-value to users.auth_subject
    base_currency = Column(String, nullable=False, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)


class FxRate(Base):
    """Daily-cached FX rates keyed by (date, from, to).

    Backs `services/fx.convert(amount, from_ccy, to_ccy)`. We hit the
    Frankfurter open API at most once per (from, to, day) pair across all
    workspaces; everything else reads from this row.
    """
    __tablename__ = "fx_rates"

    id = Column(Integer, primary_key=True, index=True)
    as_of_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    from_currency = Column(String, nullable=False, index=True)
    to_currency = Column(String, nullable=False, index=True)
    rate = Column(String, nullable=False)  # decimal stored as string
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "as_of_date", "from_currency", "to_currency", name="uq_fx_rate_day_pair"
        ),
    )


class Membership(Base):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_subject = Column(String, index=True, nullable=False)  # users.auth_subject
    role = Column(String, nullable=False, default="owner")  # owner | admin | member | viewer
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_subject", name="uq_membership_workspace_user"),
    )


class Invite(Base):
    __tablename__ = "invites"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    email = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    token_hash = Column(String, nullable=False, index=True)
    invited_by_subject = Column(String)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScheduledSync(Base):
    """One recurring sync-all per row. Evaluated by the scheduler tick,
    which picks rows with `next_run_at <= now AND is_active=1`, enqueues
    sync_all_connections, then advances next_run_at."""
    __tablename__ = "scheduled_syncs"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    frequency = Column(String, nullable=False)  # daily | weekly
    hour_utc = Column(Integer, nullable=False)  # 0-23
    day_of_week = Column(Integer, nullable=True)  # 0=Mon..6=Sun, only for weekly
    is_active = Column(Integer, default=1, nullable=False)
    created_by_subject = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=False, index=True)


class AlertRule(Base):
    """Threshold-based alerts evaluated after each sync.

    `metric` is one of the scorecard keys (totalSpend, blendedCPA, ...).
    `comparison` is gt|lt|pct_change_gt. For pct_change_gt, the threshold
    is a percentage; we compare current vs prior period from scorecard_deltas.
    `channels` is a JSON array of channel specs, e.g. [{"type":"slack","url":"..."}].
    """
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    metric = Column(String, nullable=False)
    comparison = Column(String, nullable=False)  # gt | lt | pct_change_gt
    threshold = Column(String, nullable=False)  # stored as string to keep migration simple
    channels = Column(JSONPortable, nullable=True)  # list of channel specs
    is_active = Column(Integer, default=1, nullable=False)
    created_by_subject = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_triggered_at = Column(DateTime, nullable=True)
    last_value = Column(String, nullable=True)


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

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    scope_type = Column(String, nullable=False)  # workspace | platform | connection
    scope_key = Column(String, nullable=True)  # platform name or connection id string
    period_type = Column(String, nullable=False)  # monthly | quarterly
    amount = Column(String, nullable=False)  # USD, stored as string for migration simplicity
    start_date = Column(DateTime, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    alert_at_pct = Column(Integer, nullable=True)  # 0..100, NULL disables
    last_alert_at = Column(DateTime, nullable=True)
    last_alert_period = Column(String, nullable=True)  # e.g. "2026-05" to dedupe within period
    created_by_subject = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SavedView(Base):
    """User-defined dashboard filters/preferences.

    `config` is a small JSON blob (`{platform_filter, metric_focus, ...}`)
    that the dashboard interprets. Visibility `private` (only the author
    sees it) or `workspace` (everyone in the workspace).
    """
    __tablename__ = "saved_views"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)  # creator
    name = Column(String, nullable=False)
    visibility = Column(String, nullable=False, default="private")  # private | workspace
    config = Column(JSONPortable, nullable=True)
    is_default = Column(Integer, default=0, nullable=False)  # only one per (workspace, user)
    created_at = Column(DateTime, default=datetime.utcnow)


class DigestSubscription(Base):
    """Per-user email digest. Owner of the row is `user_subject`; the
    row itself is workspace-scoped so the digest content can be limited
    to that workspace's data.

    `next_send_at` is the scheduler tick's key — same pattern as
    ScheduledSync. `last_sent_at` records the most recent send for dedupe
    + display."""
    __tablename__ = "digest_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    user_subject = Column(String, index=True, nullable=False)
    email = Column(String, nullable=False)
    cadence = Column(String, nullable=False)  # daily | weekly
    is_active = Column(Integer, default=1, nullable=False)
    next_send_at = Column(DateTime, nullable=False, index=True)
    last_sent_at = Column(DateTime, nullable=True)
    last_send_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    report_id = Column(Integer, ForeignKey("reports.id"), index=True, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    created_by_subject = Column(String, index=True)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)
    last_viewed_at = Column(DateTime, nullable=True)
    view_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


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

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    name = Column(String, nullable=False)
    formula = Column(Text, nullable=False)
    format = Column(String, nullable=False, default="number")
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_by_subject = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Append-only record of consequential workspace actions.

    Written at every spend-impacting or governance action — execute, approve,
    delete connection, invite, role change, member remove. Read via
    GET /api/workspaces/{id}/audit-log (owner/admin only).
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), index=True, nullable=False)
    actor_subject = Column(String, index=True, nullable=False)  # who did it
    action = Column(String, nullable=False, index=True)  # e.g. "optimization.execute"
    target_type = Column(String, nullable=True)  # "optimization_plan", "connection", "membership"
    target_id = Column(String, nullable=True)
    payload = Column(JSONPortable, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

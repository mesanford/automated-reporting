from sqlalchemy import Column, Integer, String, DateTime, JSON, Text
from datetime import datetime
from .database import Base

class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
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
    user_id = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    chart_data = Column(JSON)
    scorecards = Column(JSON)
    scorecard_deltas = Column(JSON)
    platform_deltas = Column(JSON)
    comparison_type = Column(String, default="none")
    current_period_label = Column(String)
    prior_period_label = Column(String)
    campaign_summary = Column(JSON)
    hierarchy_summary = Column(JSON)
    platform_summary = Column(JSON)
    top_performer = Column(JSON)
    bottom_performer = Column(JSON)
    gemini_analysis = Column(String)

class Connection(Base):
    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True) # Linked to Auth provider ID
    platform = Column(String)  # google, meta, linkedin, tiktok
    account_id = Column(String)
    account_name = Column(String)
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(DateTime)
    is_active = Column(Integer, default=1)
    available_accounts = Column(JSON)
    selected_account_ids = Column(JSON)
    last_sync_at = Column(DateTime)
    last_sync_status = Column(String)  # success, failed, pending
    last_sync_job_id = Column(Integer)

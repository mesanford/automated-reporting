from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from .database import Base

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

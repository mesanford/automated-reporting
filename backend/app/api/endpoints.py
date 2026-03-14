from fastapi import APIRouter, UploadFile, File, Depends
from typing import List
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import random
from app.services import etl, gemini
from app.database import get_db
from app import models
from app.api.auth import get_current_user
from app.services.security import encrypt_token

router = APIRouter()

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    comparison_files: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    dataframes = []
    for file in files:
        content = await file.read()
        df = etl.process_csv(content, file.filename)
        if not df.empty:
            dataframes.append(df)

    comparison_dataframes = []
    for file in comparison_files:
        content = await file.read()
        df = etl.process_csv(content, file.filename)
        if not df.empty:
            comparison_dataframes.append(df)

    if not dataframes:
        return {
            "status": "error",
            "message": "No valid data found in uploaded files."
        }

    aggregated = etl.aggregate_data(
        dataframes,
        comparison_dataframes=comparison_dataframes if comparison_dataframes else None,
    )
    analysis = gemini.generate_analysis(aggregated["geminiInput"])

    new_report = models.Report(
        user_id=user_id,
        chart_data=aggregated["chartData"],
        scorecards=aggregated["scorecards"],
        scorecard_deltas=aggregated["scorecardDeltas"],
        platform_deltas=aggregated["platformDeltas"],
        comparison_type=aggregated["comparisonType"],
        current_period_label=aggregated["currentPeriodLabel"],
        prior_period_label=aggregated["priorPeriodLabel"],
        campaign_summary=aggregated["campaignSummary"],
        platform_summary=aggregated["platformSummary"],
        top_performer=aggregated["topPerformer"],
        bottom_performer=aggregated["bottomPerformer"],
        gemini_analysis=analysis
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    return {
        "status":             "success",
        "id":                 new_report.id,
        "chartData":          aggregated["chartData"],
        "scorecards":         aggregated["scorecards"],
        "scorecardDeltas":    aggregated["scorecardDeltas"],
        "platformDeltas":     aggregated["platformDeltas"],
        "comparisonType":     aggregated["comparisonType"],
        "currentPeriodLabel": aggregated["currentPeriodLabel"],
        "priorPeriodLabel":   aggregated["priorPeriodLabel"],
        "campaignSummary":    aggregated["campaignSummary"],
        "platformSummary":    aggregated["platformSummary"],
        "topPerformer":       aggregated["topPerformer"],
        "bottomPerformer":    aggregated["bottomPerformer"],
        "geminiAnalysis":     analysis
    }

@router.get("/reports")
async def get_reports(db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    reports = db.query(models.Report).filter(models.Report.user_id == user_id).order_by(models.Report.created_at.desc()).all()
    return reports

@router.get("/connections")
async def get_connections(db: Session = Depends(get_db), user_id: str = Depends(get_current_user)):
    return db.query(models.Connection).filter(models.Connection.user_id == user_id).all()

@router.post("/connections")
async def add_connection(
    platform: str, 
    account_name: str, 
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    # Mock adding a connection with encrypted tokens
    new_conn = models.Connection(
        user_id=user_id,
        platform=platform,
        account_name=account_name,
        account_id=f"ACC-{random.randint(1000,9999)}",
        access_token=encrypt_token("mock_token"),
        refresh_token=encrypt_token("mock_refresh"),
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    db.add(new_conn)
    db.commit()
    db.refresh(new_conn)
    return new_conn

@router.post("/sync/{connection_id}")
async def sync_connection(
    connection_id: int, 
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    from app.services import connectors
    
    connection = db.query(models.Connection).filter(
        models.Connection.id == connection_id,
        models.Connection.user_id == user_id
    ).first()
    
    if not connection:
        return {"status": "error", "message": "Connection not found"}
        
    # Fetch data (mocked)
    df = await connectors.fetch_platform_data(connection.platform, connection.account_id)
    
    aggregated = etl.aggregate_data([df])
    analysis = gemini.generate_analysis(aggregated["geminiInput"])

    new_report = models.Report(
        user_id=user_id,
        chart_data=aggregated["chartData"],
        scorecards=aggregated["scorecards"],
        scorecard_deltas=aggregated["scorecardDeltas"],
        platform_deltas=aggregated["platformDeltas"],
        comparison_type=aggregated["comparisonType"],
        current_period_label=aggregated["currentPeriodLabel"],
        prior_period_label=aggregated["priorPeriodLabel"],
        campaign_summary=aggregated["campaignSummary"],
        platform_summary=aggregated["platformSummary"],
        top_performer=aggregated["topPerformer"],
        bottom_performer=aggregated["bottomPerformer"],
        gemini_analysis=analysis
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    return {
        "status":             "success",
        "id":                 new_report.id,
        "chartData":          aggregated["chartData"],
        "scorecards":         aggregated["scorecards"],
        "scorecardDeltas":    aggregated["scorecardDeltas"],
        "platformDeltas":     aggregated["platformDeltas"],
        "comparisonType":     aggregated["comparisonType"],
        "currentPeriodLabel": aggregated["currentPeriodLabel"],
        "priorPeriodLabel":   aggregated["priorPeriodLabel"],
        "campaignSummary":    aggregated["campaignSummary"],
        "platformSummary":    aggregated["platformSummary"],
        "topPerformer":       aggregated["topPerformer"],
        "bottomPerformer":    aggregated["bottomPerformer"],
        "geminiAnalysis":     analysis
    }
@router.post("/sync/all")
async def sync_all_connections(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    from app.services import connectors
    
    connections = db.query(models.Connection).filter(
        models.Connection.user_id == user_id,
        models.Connection.is_active == 1
    ).all()
    
    if not connections:
        return {"status": "error", "message": "No active connections found."}
        
    dataframes = []
    for conn in connections:
        try:
            df = await connectors.fetch_platform_data(conn.platform, conn.account_id)
            if not df.empty:
                dataframes.append(df)
        except Exception as e:
            print(f"Error syncing {conn.platform}: {str(e)}")
            
    if not dataframes:
        return {"status": "error", "message": "Could not fetch data from any connection."}
        
    aggregated = etl.aggregate_data(dataframes)
    analysis = gemini.generate_analysis(aggregated["geminiInput"])

    new_report = models.Report(
        user_id=user_id,
        chart_data=aggregated["chartData"],
        scorecards=aggregated["scorecards"],
        scorecard_deltas=aggregated["scorecardDeltas"],
        platform_deltas=aggregated["platformDeltas"],
        comparison_type=aggregated["comparisonType"],
        current_period_label=aggregated["currentPeriodLabel"],
        prior_period_label=aggregated["priorPeriodLabel"],
        campaign_summary=aggregated["campaignSummary"],
        platform_summary=aggregated["platformSummary"],
        top_performer=aggregated["topPerformer"],
        bottom_performer=aggregated["bottomPerformer"],
        gemini_analysis=analysis
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    return {
        "status":             "success",
        "id":                 new_report.id,
        "chartData":          aggregated["chartData"],
        "scorecards":         aggregated["scorecards"],
        "scorecardDeltas":    aggregated["scorecardDeltas"],
        "platformDeltas":     aggregated["platformDeltas"],
        "comparisonType":     aggregated["comparisonType"],
        "currentPeriodLabel": aggregated["currentPeriodLabel"],
        "priorPeriodLabel":   aggregated["priorPeriodLabel"],
        "campaignSummary":    aggregated["campaignSummary"],
        "platformSummary":    aggregated["platformSummary"],
        "topPerformer":       aggregated["topPerformer"],
        "bottomPerformer":    aggregated["bottomPerformer"],
        "geminiAnalysis":     analysis
    }

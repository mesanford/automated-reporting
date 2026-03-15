from fastapi import APIRouter, UploadFile, File, Depends
from fastapi.responses import PlainTextResponse
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


def _md_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    divider = ["---"] * len(header)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(divider) + " |"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _build_report_markdown(report: models.Report) -> str:
    scorecards = report.scorecards or {}
    deltas = report.scorecard_deltas or {}
    platform_summary = report.platform_summary or []
    hierarchy_summary = report.hierarchy_summary or {"campaign": [], "adGroup": [], "adAsset": []}
    if not hierarchy_summary.get("campaign") and report.campaign_summary:
        hierarchy_summary["campaign"] = [
            {
                "platform": row.get("platform", "unknown"),
                "name": row.get("campaign", "Unnamed"),
                "spend": row.get("spend", 0),
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "conversions": row.get("conversions", 0),
                "cpa": row.get("cpa", 0),
                "ctr": row.get("ctr", 0),
                "cvr": row.get("cvr", 0),
                "cpc": row.get("cpc", 0),
                "roas": row.get("roas", "N/A"),
                "spend_share": row.get("spend_share", 0),
            }
            for row in (report.campaign_summary or [])
        ]

    lines: List[str] = []
    lines.append(f"# Full Performance Report (ID {report.id})")
    lines.append("")
    lines.append(f"Generated: {report.created_at}")
    lines.append(f"Comparison: {report.comparison_type or 'none'}")
    lines.append(f"Current period: {report.current_period_label or 'N/A'}")
    lines.append(f"Prior period: {report.prior_period_label or 'N/A'}")
    lines.append("")

    lines.append("## Blended Scorecards")
    score_rows = [["Metric", "Value", "Delta", "Confidence"]]
    metrics = [
        ("totalSpend", "Total Spend", "$"),
        ("totalImpressions", "Impressions", ""),
        ("totalClicks", "Clicks", ""),
        ("totalConversions", "Conversions", ""),
        ("blendedCPA", "Blended CPA", "$"),
        ("blendedCTR", "Blended CTR", "%"),
        ("blendedCVR", "Blended CVR", "%"),
        ("blendedCPC", "Blended CPC", "$"),
        ("blendedCPM", "Blended CPM", "$"),
        ("blendedROAS", "Blended ROAS", ""),
    ]
    for key, label, unit in metrics:
        value = scorecards.get(key)
        value_str = "N/A" if value is None else f"{unit}{value}"
        delta_obj = deltas.get(key if key in deltas else key.replace("total", "").lower(), {})
        delta_str = delta_obj.get("value", "N/A") if isinstance(delta_obj, dict) else "N/A"
        conf_str = delta_obj.get("confidence", "N/A") if isinstance(delta_obj, dict) else "N/A"
        score_rows.append([label, value_str, delta_str, conf_str])
    lines.append(_md_table(score_rows))
    lines.append("")

    lines.append("## Platform Summary")
    platform_rows = [["Platform", "Spend", "Impressions", "Clicks", "Conversions", "CPA", "CTR", "CVR", "CPC", "ROAS", "Spend Share"]]
    for row in platform_summary:
        platform_rows.append([
            str(row.get("platform", "")),
            f"${row.get('spend', 0)}",
            str(row.get("impressions", 0)),
            str(row.get("clicks", 0)),
            str(row.get("conversions", 0)),
            f"${row.get('cpa', 0)}",
            f"{row.get('ctr', 0)}%",
            f"{row.get('cvr', 0)}%",
            f"${row.get('cpc', 0)}",
            str(row.get("roas", "N/A")),
            f"{row.get('spend_share', 0)}%",
        ])
    lines.append(_md_table(platform_rows))
    lines.append("")

    for level_key, level_title in [("campaign", "Campaign"), ("adGroup", "Ad Set / Ad Group"), ("adAsset", "Ad / Asset")]:
        level_rows = hierarchy_summary.get(level_key, [])
        lines.append(f"## {level_title} Detail by Platform")
        if not level_rows:
            lines.append("No data available at this level.")
            lines.append("")
            continue

        grouped = {}
        for row in level_rows:
            grouped.setdefault(row.get("platform", "unknown"), []).append(row)

        for platform, rows in grouped.items():
            lines.append(f"### {platform}")
            table_rows = [["Name", "Spend", "Impressions", "Clicks", "Conversions", "CPA", "CTR", "CVR", "CPC", "ROAS", "Spend Share"]]
            for r in sorted(rows, key=lambda x: x.get("spend", 0), reverse=True):
                table_rows.append([
                    str(r.get("name", "")),
                    f"${r.get('spend', 0)}",
                    str(r.get("impressions", 0)),
                    str(r.get("clicks", 0)),
                    str(r.get("conversions", 0)),
                    f"${r.get('cpa', 0)}",
                    f"{r.get('ctr', 0)}%",
                    f"{r.get('cvr', 0)}%",
                    f"${r.get('cpc', 0)}",
                    str(r.get("roas", "N/A")),
                    f"{r.get('spend_share', 0)}%",
                ])
            lines.append(_md_table(table_rows))
            lines.append("")

    lines.append("## AI Narrative")
    lines.append(report.gemini_analysis or "No AI analysis generated.")
    lines.append("")
    return "\n".join(lines)

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
        hierarchy_summary=aggregated["hierarchySummary"],
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
        "hierarchySummary":   aggregated["hierarchySummary"],
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
        hierarchy_summary=aggregated["hierarchySummary"],
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
        "hierarchySummary":   aggregated["hierarchySummary"],
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
        hierarchy_summary=aggregated["hierarchySummary"],
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
        "hierarchySummary":   aggregated["hierarchySummary"],
        "platformSummary":    aggregated["platformSummary"],
        "topPerformer":       aggregated["topPerformer"],
        "bottomPerformer":    aggregated["bottomPerformer"],
        "geminiAnalysis":     analysis
    }


@router.get("/reports/{report_id}/markdown")
async def download_report_markdown(
    report_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    report = db.query(models.Report).filter(
        models.Report.id == report_id,
        models.Report.user_id == user_id,
    ).first()

    if not report:
        return PlainTextResponse("Report not found", status_code=404)

    markdown = _build_report_markdown(report)
    filename = f"antigravity-report-{report_id}.md"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return PlainTextResponse(markdown, media_type="text/markdown", headers=headers)

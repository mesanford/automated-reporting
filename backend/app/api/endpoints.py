from fastapi import APIRouter, UploadFile, File, Depends, Body
from fastapi.responses import PlainTextResponse
from typing import Any, Dict, List
import os
import re
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.services import etl, gemini
from app.database import get_db
from app import models
from app.api.auth import get_current_user
from app.services.security import decrypt_token

router = APIRouter()


class AccountSelectionPayload(BaseModel):
    selected_account_ids: List[str]


class SyncRequestPayload(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    comparison_start_date: str | None = None
    comparison_end_date: str | None = None


def _try_decrypt_token(token: str) -> tuple[str, str | None]:
    if not token:
        return "", None
    try:
        return decrypt_token(token), None
    except Exception:
        return "", "Stored OAuth token can no longer be decrypted. Reconnect this platform account."


def _sanitize_error_message(message: str) -> str:
    text = str(message or "")
    text = re.sub(r"(access_token=)[^&\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s]+", r"\1[redacted]", text, flags=re.IGNORECASE)
    return text


def _format_exception_message(exc: Exception, fallback: str) -> str:
    raw = _sanitize_error_message(str(exc)).strip()
    if raw:
        return raw
    exc_name = type(exc).__name__
    if exc_name and exc_name != "Exception":
        return f"{fallback} ({exc_name})"
    return fallback


async def _hydrate_microsoft_customer_map(
    connection: models.Connection,
    accounts_to_check: List[str],
    access_token: str,
    refresh_token: str,
    db: Session,
) -> Dict[str, str]:
    # Keep current mappings first; only re-discover when selected accounts are missing customer IDs.
    existing_accounts = connection.available_accounts or []
    customer_map = {
        str(a.get("id")): str(a.get("customer_id", ""))
        for a in existing_accounts
    }

    unresolved = [aid for aid in accounts_to_check if not customer_map.get(str(aid), "").strip()]
    if connection.platform != "microsoft" or not unresolved:
        return customer_map

    from app.services import connectors

    refreshed_accounts: List[Dict[str, Any]] = []
    try:
        refreshed_accounts = await connectors.discover_ad_accounts(
            platform="microsoft",
            parent_account_id=connection.account_id,
            query="",
            access_token=access_token,
            refresh_token=refresh_token,
        )
    except Exception:
        # Continue with existing mappings + fallback inference even if discovery fails.
        refreshed_accounts = []

    merged_by_id: Dict[str, Dict[str, Any]] = {}
    for account in existing_accounts:
        account_id = str(account.get("id", ""))
        if account_id:
            merged_by_id[account_id] = account

    for account in refreshed_accounts:
        account_id = str(account.get("id", ""))
        if not account_id:
            continue
        current = merged_by_id.get(account_id, {})
        customer_id = str(account.get("customer_id", "") or current.get("customer_id", ""))
        merged_by_id[account_id] = {
            **current,
            **account,
            "customer_id": customer_id,
        }

    known_customer_ids = {
        str(acc.get("customer_id", "")).strip()
        for acc in merged_by_id.values()
        if str(acc.get("customer_id", "")).strip()
    }
    inferred_customer_id = next(iter(known_customer_ids)) if len(known_customer_ids) == 1 else ""
    fallback_customer_id = inferred_customer_id or os.getenv("MICROSOFT_CUSTOMER_ID", "").strip()
    if fallback_customer_id:
        for account_id in unresolved:
            existing = merged_by_id.get(str(account_id), {})
            merged_by_id[str(account_id)] = {
                **existing,
                "id": str(account_id),
                "name": str(existing.get("name", "") or f"Microsoft Ads {account_id}"),
                "status": str(existing.get("status", "ACTIVE") or "ACTIVE"),
                "currency": str(existing.get("currency", "USD") or "USD"),
                "customer_id": str(existing.get("customer_id", "") or fallback_customer_id),
            }

    merged_accounts = list(merged_by_id.values())
    connection.available_accounts = merged_accounts
    db.commit()

    return {
        str(a.get("id")): str(a.get("customer_id", ""))
        for a in merged_accounts
    }


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
    connections = db.query(models.Connection).filter(models.Connection.user_id == user_id).all()
    return [
        {
            "id": c.id,
            "platform": c.platform,
            "account_name": c.account_name,
            "account_id": c.account_id,
            "is_active": c.is_active,
            "available_accounts": c.available_accounts or [],
            "selected_account_ids": c.selected_account_ids or [],
            "expires_at": c.expires_at,
        }
        for c in connections
    ]

@router.post("/connections")
async def add_connection(
    platform: str, 
    account_name: str, 
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    return {
        "status": "error",
        "message": "Direct mock connection creation is disabled. Use OAuth login under /api/auth/{platform}/login.",
    }


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    connection = db.query(models.Connection).filter(
        models.Connection.id == connection_id,
        models.Connection.user_id == user_id,
    ).first()

    if not connection:
        return {"status": "error", "message": "Connection not found"}

    db.delete(connection)
    db.commit()
    return {"status": "success", "message": "Connection removed"}


@router.get("/connections/diagnostics")
async def connection_diagnostics(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    from app.services import connectors

    connections = db.query(models.Connection).filter(models.Connection.user_id == user_id).all()
    if not connections:
        return {
            "status": "success",
            "overall": "ok",
            "results": [],
        }

    results = []
    has_error = False
    has_warning = False

    for conn in connections:
        selected_account_ids = conn.selected_account_ids or []
        accounts_to_check = selected_account_ids if selected_account_ids else ([conn.account_id] if conn.account_id else [])
        microsoft_customer_map: Dict[str, str] = {
            str(a.get("id")): str(a.get("customer_id", ""))
            for a in (conn.available_accounts or [])
        }

        issues: List[str] = []
        level = "ok"

        access_token, access_err = _try_decrypt_token(conn.access_token or "")
        refresh_token, refresh_err = _try_decrypt_token(conn.refresh_token or "")
        decrypt_error = access_err or refresh_err
        if decrypt_error:
            issues.append(decrypt_error)
            level = "error"
        else:
            try:
                discovered = await connectors.discover_ad_accounts(
                    platform=conn.platform,
                    parent_account_id=conn.account_id,
                    query="",
                    access_token=access_token,
                    refresh_token=refresh_token,
                )
                if len(discovered) == 0:
                    issues.append("No ad accounts were returned by the platform API.")
                    level = "warning"
            except Exception as exc:
                issues.append(f"Account discovery check failed: {str(exc)}")
                level = "error"

            if conn.platform == "microsoft" and accounts_to_check:
                microsoft_customer_map = await _hydrate_microsoft_customer_map(
                    connection=conn,
                    accounts_to_check=[str(a) for a in accounts_to_check],
                    access_token=access_token,
                    refresh_token=refresh_token,
                    db=db,
                )
                missing_customer = [
                    aid for aid in accounts_to_check
                    if not microsoft_customer_map.get(str(aid))
                ]
                if missing_customer:
                    issues.append(
                        "Missing Microsoft customer_id for selected account(s): "
                        + ", ".join(str(x) for x in missing_customer)
                        + ". Re-discover and re-save account selection or set MICROSOFT_CUSTOMER_ID."
                    )
                    level = "warning" if level == "ok" else level

        if level == "error":
            has_error = True
        elif level == "warning":
            has_warning = True

        results.append({
            "connectionId": conn.id,
            "platform": conn.platform,
            "accountName": conn.account_name,
            "accountId": conn.account_id,
            "status": level,
            "selectedAdAccounts": len(accounts_to_check),
            "issues": issues,
        })

    overall = "error" if has_error else ("warning" if has_warning else "ok")
    return {
        "status": "success",
        "overall": overall,
        "results": results,
    }


@router.get("/connections/{connection_id}/accounts")
async def discover_connection_accounts(
    connection_id: int,
    query: str = "",
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    from app.services import connectors

    connection = db.query(models.Connection).filter(
        models.Connection.id == connection_id,
        models.Connection.user_id == user_id,
    ).first()

    if not connection:
        return {"status": "error", "message": "Connection not found"}

    try:
        access_token, access_err = _try_decrypt_token(connection.access_token or "")
        refresh_token, refresh_err = _try_decrypt_token(connection.refresh_token or "")
        decrypt_error = access_err or refresh_err
        if decrypt_error:
            return {
                "status": "error",
                "message": decrypt_error,
            }
        accounts = await connectors.discover_ad_accounts(
            platform=connection.platform,
            parent_account_id=connection.account_id,
            query=query,
            access_token=access_token,
            refresh_token=refresh_token,
        )
    except Exception as exc:
        return {
            "status": "error",
            "message": _format_exception_message(
                exc,
                f"Account discovery failed for platform '{connection.platform}'.",
            ),
        }

    existing_selected = connection.selected_account_ids or []
    selected_ids = [a for a in existing_selected if any(acc.get("id") == a for acc in accounts)]
    if not selected_ids and accounts:
        selected_ids = [accounts[0]["id"]]

    connection.available_accounts = accounts
    connection.selected_account_ids = selected_ids
    db.commit()

    return {
        "status": "success",
        "connectionId": connection.id,
        "platform": connection.platform,
        "accounts": accounts,
        "selectedAccountIds": selected_ids,
    }


@router.post("/connections/{connection_id}/accounts/select")
async def select_connection_accounts(
    connection_id: int,
    payload: AccountSelectionPayload = Body(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    connection = db.query(models.Connection).filter(
        models.Connection.id == connection_id,
        models.Connection.user_id == user_id,
    ).first()

    if not connection:
        return {"status": "error", "message": "Connection not found"}

    available = connection.available_accounts or []
    available_ids = {a.get("id") for a in available if a.get("id")}
    selected_unique = list(dict.fromkeys(payload.selected_account_ids))
    valid_selection = [a for a in selected_unique if a in available_ids]

    if available and not valid_selection:
        return {
            "status": "error",
            "message": "Select at least one available ad account.",
        }

    connection.selected_account_ids = valid_selection
    db.commit()
    return {
        "status": "success",
        "connectionId": connection.id,
        "selectedAccountIds": valid_selection,
    }

@router.post("/sync/{connection_id}")
async def sync_connection(
    connection_id: int, 
    payload: SyncRequestPayload | None = Body(default=None),
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

    sync_start_date = payload.start_date if payload else None
    sync_end_date = payload.end_date if payload else None
    comparison_start_date = payload.comparison_start_date if payload else None
    comparison_end_date = payload.comparison_end_date if payload else None
    comparison_requested = bool(comparison_start_date and comparison_end_date)
        
    selected_account_ids = connection.selected_account_ids or []
    accounts_to_sync = selected_account_ids if selected_account_ids else [connection.account_id]
    microsoft_customer_map: Dict[str, str] = {
        str(a.get("id")): str(a.get("customer_id", ""))
        for a in (connection.available_accounts or [])
    }

    dataframes = []
    comparison_dataframes = []
    access_token, access_err = _try_decrypt_token(connection.access_token or "")
    refresh_token, refresh_err = _try_decrypt_token(connection.refresh_token or "")
    decrypt_error = access_err or refresh_err
    if decrypt_error:
        return {
            "status": "error",
            "message": f"Sync failed for {connection.platform}: {decrypt_error}",
        }

    if connection.platform == "microsoft" and accounts_to_sync:
        microsoft_customer_map = await _hydrate_microsoft_customer_map(
            connection=connection,
            accounts_to_check=[str(a) for a in accounts_to_sync],
            access_token=access_token,
            refresh_token=refresh_token,
            db=db,
        )
        missing_customer_ids = [str(a) for a in accounts_to_sync if not microsoft_customer_map.get(str(a), "").strip()]
        if missing_customer_ids:
            return {
                "status": "error",
                "message": (
                    "Sync failed for microsoft "
                    f"(connection {connection_id}): missing customer_id for selected account(s) "
                    + ", ".join(missing_customer_ids)
                    + ". Re-discover Microsoft accounts and re-save the selection, or set MICROSOFT_CUSTOMER_ID for a single customer context."
                ),
            }

    try:
        for account_id in accounts_to_sync:
            df = await connectors.fetch_platform_data(
                connection.platform,
                account_id,
                access_token=access_token,
                refresh_token=refresh_token,
                microsoft_customer_id=microsoft_customer_map.get(str(account_id), ""),
                start_date=sync_start_date,
                end_date=sync_end_date,
            )
            if not df.empty:
                dataframes.append(df)

            if comparison_start_date and comparison_end_date:
                comparison_df = await connectors.fetch_platform_data(
                    connection.platform,
                    account_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    microsoft_customer_id=microsoft_customer_map.get(str(account_id), ""),
                    start_date=comparison_start_date,
                    end_date=comparison_end_date,
                )
                if not comparison_df.empty:
                    comparison_dataframes.append(comparison_df)
    except Exception as exc:
        error_msg = str(exc)
        return {
            "status": "error",
            "message": f"Sync failed for {connection.platform} (connection {connection_id}): {error_msg}",
        }

    if not dataframes:
        return {"status": "error", "message": "No ad account data returned for this connection."}

    aggregated = etl.aggregate_data(
        dataframes,
        comparison_dataframes=comparison_dataframes if comparison_requested else None,
        sync_start_date=sync_start_date,
        sync_end_date=sync_end_date,
        comparison_start_date=comparison_start_date,
        comparison_end_date=comparison_end_date,
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
        "syncedAdAccounts":   len(dataframes),
        "syncWindow": {
            "startDate": sync_start_date,
            "endDate": sync_end_date,
        },
        "comparisonWindow": {
            "startDate": comparison_start_date,
            "endDate": comparison_end_date,
        },
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
@router.post("/sync-all")
async def sync_all_connections(
    payload: SyncRequestPayload | None = Body(default=None),
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

    sync_start_date = payload.start_date if payload else None
    sync_end_date = payload.end_date if payload else None
    comparison_start_date = payload.comparison_start_date if payload else None
    comparison_end_date = payload.comparison_end_date if payload else None
    comparison_requested = bool(comparison_start_date and comparison_end_date)
        
    dataframes = []
    comparison_dataframes = []
    synced_connections = 0
    synced_ad_accounts = 0
    skipped_connections: List[Dict[str, Any]] = []
    synced_account_signatures = set()
    
    for conn in connections:
        selected_account_ids = conn.selected_account_ids or []
        accounts_to_sync = selected_account_ids if selected_account_ids else [conn.account_id]
        microsoft_customer_map: Dict[str, str] = {
            str(a.get("id")): str(a.get("customer_id", ""))
            for a in (conn.available_accounts or [])
        }
        access_token, access_err = _try_decrypt_token(conn.access_token or "")
        refresh_token, refresh_err = _try_decrypt_token(conn.refresh_token or "")
        decrypt_error = access_err or refresh_err
        if decrypt_error:
            return {
                "status": "error",
                "message": f"Sync failed for {conn.platform} (connection {conn.id}): {decrypt_error}",
            }

        if conn.platform == "microsoft" and accounts_to_sync:
            microsoft_customer_map = await _hydrate_microsoft_customer_map(
                connection=conn,
                accounts_to_check=[str(a) for a in accounts_to_sync],
                access_token=access_token,
                refresh_token=refresh_token,
                db=db,
            )
            missing_customer_ids = [str(a) for a in accounts_to_sync if not microsoft_customer_map.get(str(a), "").strip()]
            if missing_customer_ids:
                return {
                    "status": "error",
                    "message": (
                        "Sync failed for microsoft "
                        f"(connection {conn.id}): missing customer_id for selected account(s) "
                        + ", ".join(missing_customer_ids)
                        + ". Re-discover Microsoft accounts and re-save the selection, or set MICROSOFT_CUSTOMER_ID for a single customer context."
                    ),
                }

        connection_had_data = False
        try:
            for account_id in accounts_to_sync:
                signature = (conn.platform.lower(), str(account_id))
                if signature in synced_account_signatures:
                    continue
                synced_account_signatures.add(signature)
                
                df = await connectors.fetch_platform_data(
                    conn.platform,
                    account_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    microsoft_customer_id=microsoft_customer_map.get(str(account_id), ""),
                    start_date=sync_start_date,
                    end_date=sync_end_date,
                )
                if not df.empty:
                    dataframes.append(df)
                    synced_ad_accounts += 1
                    connection_had_data = True

                if comparison_start_date and comparison_end_date:
                    comparison_df = await connectors.fetch_platform_data(
                        conn.platform,
                        account_id,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        microsoft_customer_id=microsoft_customer_map.get(str(account_id), ""),
                        start_date=comparison_start_date,
                        end_date=comparison_end_date,
                    )
                    if not comparison_df.empty:
                        comparison_dataframes.append(comparison_df)
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Sync failed for {conn.platform} (connection {conn.id}): {str(exc)}",
            }

        if not connection_had_data:
            skipped_connections.append({
                "connectionId": conn.id,
                "platform": conn.platform,
                "reason": "No data returned for the selected date window/accounts.",
            })
            continue

        synced_connections += 1
            
    if not dataframes:
        return {"status": "error", "message": "Could not fetch data from any connection."}
        
    aggregated = etl.aggregate_data(
        dataframes,
        comparison_dataframes=comparison_dataframes if comparison_requested else None,
        sync_start_date=sync_start_date,
        sync_end_date=sync_end_date,
        comparison_start_date=comparison_start_date,
        comparison_end_date=comparison_end_date,
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
        "totalActiveConnections": len(connections),
        "syncedConnections":  synced_connections,
        "syncedAdAccounts":   synced_ad_accounts,
        "skippedConnections": skipped_connections,
        "syncWindow": {
            "startDate": sync_start_date,
            "endDate": sync_end_date,
        },
        "comparisonWindow": {
            "startDate": comparison_start_date,
            "endDate": comparison_end_date,
        },
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


@router.get("/sync/{connection_id}/status")
async def get_sync_status(
    connection_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Get real-time status of most recent sync job for a connection"""
    job = db.query(models.SyncJob).filter(
        models.SyncJob.connection_id == connection_id,
        models.SyncJob.user_id == user_id,
    ).order_by(models.SyncJob.created_at.desc()).first()

    if not job:
        return {
            "status": "idle",
            "message": "No sync job found for this connection"
        }

    return {
        "status": job.status,
        "progress_percent": job.progress_percent,
        "current_step": job.current_step or "initializing",
        "total_steps": job.total_steps,
        "accounts_synced": job.accounts_synced,
        "total_accounts": job.total_accounts,
        "error_message": job.error_message,
        "recent_logs": (job.logs or "").split("\n")[-20:],  # Last 20 log lines
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
    }


@router.get("/sync-jobs")
async def list_sync_jobs(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    limit: int = 20,
):
    """List recent sync jobs for this user"""
    jobs = db.query(models.SyncJob).filter(
        models.SyncJob.user_id == user_id,
    ).order_by(models.SyncJob.created_at.desc()).limit(limit).all()

    return [
        {
            "id": j.id,
            "connection_id": j.connection_id,
            "status": j.status,
            "progress_percent": j.progress_percent,
            "accounts_synced": j.accounts_synced,
            "total_accounts": j.total_accounts,
            "error_message": j.error_message,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        }
        for j in jobs
    ]

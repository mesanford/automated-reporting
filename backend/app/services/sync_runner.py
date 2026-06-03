"""Pure-function sync executor, shared by the synchronous endpoint and the
Cloud Tasks handler.

The original `endpoints.sync_connection` returned the full report payload
inline — useful when the dashboard awaits the response. Splitting the work
into `run_sync(...)` lets the same logic run from a background worker
without a request context, so the endpoint can enqueue and return the
SyncJob id immediately while the heavy fetch+aggregate+Gemini work
happens on the queue.

Both `run_sync()` and `run_sync_for_job()` raise on hard errors so the
queue retry policy can apply; the legacy endpoint catches and returns a
JSON envelope to stay backward compatible.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import models
from app.services import etl, gemini


class SyncError(Exception):
    """Anything that should surface as a `{status: error}` to the caller."""


def _try_decrypt(token: str) -> str:
    if not token:
        return ""
    from app.services.security import decrypt_token

    try:
        return decrypt_token(token)
    except Exception as exc:  # noqa: BLE001
        raise SyncError(f"Stored OAuth token can no longer be decrypted ({exc}).")


async def run_sync(
    *,
    db: Session,
    workspace_id: int,
    user_id: str,
    connection_id: int,
    sync_start_date: Optional[str] = None,
    sync_end_date: Optional[str] = None,
    comparison_start_date: Optional[str] = None,
    comparison_end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the full sync → ETL → Gemini → Report pipeline.

    Returns the same payload shape `sync_connection` has historically
    returned (modulo a few outer wrappers the endpoint adds). Raises
    `SyncError` for user-visible failures.
    """
    # Lazy imports so this module stays importable in tests that stub the
    # heavy platform SDKs.
    from app.api.endpoints import _hydrate_microsoft_customer_map
    from app.services import connectors

    connection = (
        db.query(models.Connection)
        .filter(
            models.Connection.id == connection_id,
            models.Connection.workspace_id == workspace_id,
        )
        .first()
    )
    if not connection:
        raise SyncError("Connection not found")

    comparison_requested = bool(comparison_start_date and comparison_end_date)

    selected_account_ids = connection.selected_account_ids or []
    accounts_to_sync = (
        selected_account_ids if selected_account_ids else [connection.account_id]
    )

    microsoft_customer_map: Dict[str, str] = {
        str(a.get("id")): str(a.get("customer_id", ""))
        for a in (connection.available_accounts or [])
    }

    access_token = _try_decrypt(connection.access_token or "")
    refresh_token = _try_decrypt(connection.refresh_token or "")

    if connection.platform == "microsoft" and accounts_to_sync:
        microsoft_customer_map = await _hydrate_microsoft_customer_map(
            connection=connection,
            accounts_to_check=[str(a) for a in accounts_to_sync],
            access_token=access_token,
            refresh_token=refresh_token,
            db=db,
        )
        missing = [
            str(a)
            for a in accounts_to_sync
            if not microsoft_customer_map.get(str(a), "").strip()
        ]
        if missing:
            raise SyncError(
                "Sync failed for microsoft "
                f"(connection {connection_id}): missing customer_id for selected account(s) "
                + ", ".join(missing)
                + ". Re-discover Microsoft accounts and re-save the selection, "
                "or set MICROSOFT_CUSTOMER_ID for a single customer context."
            )

    dataframes: List[Any] = []
    comparison_dataframes: List[Any] = []
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

            if comparison_requested:
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
    except SyncError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SyncError(
            f"Sync failed for {connection.platform} (connection {connection_id}): {exc}"
        )

    if not dataframes:
        raise SyncError("No ad account data returned for this connection.")

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
        workspace_id=workspace_id,
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
        gemini_analysis=analysis,
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    # Fan out alert rules + budget pacing checks immediately after the
    # Report lands so users get alerted on this sync, not the next one.
    try:
        from app.services.alerts import evaluate_rules_for_report
        from app.services.budgets import evaluate_budget_alerts

        evaluate_rules_for_report(db, workspace_id=workspace_id, report=new_report)
        evaluate_budget_alerts(db, workspace_id=workspace_id)
    except Exception:  # noqa: BLE001
        logger = __import__("logging").getLogger(__name__)
        logger.exception("Alert/budget evaluation failed (sync continues)")

    return {
        "id": new_report.id,
        "syncedAdAccounts": len(dataframes),
        "syncWindow": {"startDate": sync_start_date, "endDate": sync_end_date},
        "comparisonWindow": {
            "startDate": comparison_start_date,
            "endDate": comparison_end_date,
        },
        "chartData": aggregated["chartData"],
        "scorecards": aggregated["scorecards"],
        "scorecardDeltas": aggregated["scorecardDeltas"],
        "platformDeltas": aggregated["platformDeltas"],
        "comparisonType": aggregated["comparisonType"],
        "currentPeriodLabel": aggregated["currentPeriodLabel"],
        "priorPeriodLabel": aggregated["priorPeriodLabel"],
        "campaignSummary": aggregated["campaignSummary"],
        "hierarchySummary": aggregated["hierarchySummary"],
        "platformSummary": aggregated["platformSummary"],
        "topPerformer": aggregated["topPerformer"],
        "bottomPerformer": aggregated["bottomPerformer"],
        "geminiAnalysis": analysis,
    }


async def run_sync_all(
    *,
    db: Session,
    workspace_id: int,
    user_id: str,
    sync_start_date: Optional[str] = None,
    sync_end_date: Optional[str] = None,
    comparison_start_date: Optional[str] = None,
    comparison_end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Combine data from every active workspace connection into one Report.

    Returns the same envelope shape `sync_all_connections` historically
    returned (minus the outer `status` key, which the endpoint adds).
    Raises `SyncError` on user-visible failures.
    """
    from app.api.endpoints import _hydrate_microsoft_customer_map
    from app.services import connectors

    connections = (
        db.query(models.Connection)
        .filter(
            models.Connection.workspace_id == workspace_id,
            models.Connection.is_active == 1,
        )
        .all()
    )
    if not connections:
        raise SyncError("No active connections found.")

    comparison_requested = bool(comparison_start_date and comparison_end_date)

    dataframes: List[Any] = []
    comparison_dataframes: List[Any] = []
    synced_connections = 0
    synced_ad_accounts = 0
    skipped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for conn in connections:
        selected_account_ids = conn.selected_account_ids or []
        accounts_to_sync = (
            selected_account_ids if selected_account_ids else [conn.account_id]
        )
        microsoft_customer_map: Dict[str, str] = {
            str(a.get("id")): str(a.get("customer_id", ""))
            for a in (conn.available_accounts or [])
        }
        access_token = _try_decrypt(conn.access_token or "")
        refresh_token = _try_decrypt(conn.refresh_token or "")

        if conn.platform == "microsoft" and accounts_to_sync:
            microsoft_customer_map = await _hydrate_microsoft_customer_map(
                connection=conn,
                accounts_to_check=[str(a) for a in accounts_to_sync],
                access_token=access_token,
                refresh_token=refresh_token,
                db=db,
            )
            missing = [
                str(a)
                for a in accounts_to_sync
                if not microsoft_customer_map.get(str(a), "").strip()
            ]
            if missing:
                raise SyncError(
                    "Sync failed for microsoft "
                    f"(connection {conn.id}): missing customer_id for selected account(s) "
                    + ", ".join(missing)
                    + ". Re-discover Microsoft accounts and re-save the selection, "
                    "or set MICROSOFT_CUSTOMER_ID for a single customer context."
                )

        connection_had_data = False
        try:
            for account_id in accounts_to_sync:
                signature = (conn.platform.lower(), str(account_id))
                if signature in seen:
                    continue
                seen.add(signature)

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

                if comparison_requested:
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
        except SyncError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SyncError(
                f"Sync failed for {conn.platform} (connection {conn.id}): {exc}"
            )

        if not connection_had_data:
            skipped.append({
                "connectionId": conn.id,
                "platform": conn.platform,
                "reason": "No data returned for the selected date window/accounts.",
            })
            continue
        synced_connections += 1

    if not dataframes:
        raise SyncError("Could not fetch data from any connection.")

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
        workspace_id=workspace_id,
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
        gemini_analysis=analysis,
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)

    try:
        from app.services.alerts import evaluate_rules_for_report
        from app.services.budgets import evaluate_budget_alerts

        evaluate_rules_for_report(db, workspace_id=workspace_id, report=new_report)
        evaluate_budget_alerts(db, workspace_id=workspace_id)
    except Exception:  # noqa: BLE001
        logger = __import__("logging").getLogger(__name__)
        logger.exception("Alert/budget evaluation failed (sync-all continues)")

    return {
        "id": new_report.id,
        "totalActiveConnections": len(connections),
        "syncedConnections": synced_connections,
        "syncedAdAccounts": synced_ad_accounts,
        "skippedConnections": skipped,
        "syncWindow": {"startDate": sync_start_date, "endDate": sync_end_date},
        "comparisonWindow": {
            "startDate": comparison_start_date,
            "endDate": comparison_end_date,
        },
        "chartData": aggregated["chartData"],
        "scorecards": aggregated["scorecards"],
        "scorecardDeltas": aggregated["scorecardDeltas"],
        "platformDeltas": aggregated["platformDeltas"],
        "comparisonType": aggregated["comparisonType"],
        "currentPeriodLabel": aggregated["currentPeriodLabel"],
        "priorPeriodLabel": aggregated["priorPeriodLabel"],
        "campaignSummary": aggregated["campaignSummary"],
        "hierarchySummary": aggregated["hierarchySummary"],
        "platformSummary": aggregated["platformSummary"],
        "topPerformer": aggregated["topPerformer"],
        "bottomPerformer": aggregated["bottomPerformer"],
        "geminiAnalysis": analysis,
    }


async def run_sync_all_for_job(
    job_id: int,
    *,
    sync_start_date: Optional[str] = None,
    sync_end_date: Optional[str] = None,
    comparison_start_date: Optional[str] = None,
    comparison_end_date: Optional[str] = None,
) -> None:
    """Task-handler entrypoint for sync-all. Mirrors `run_sync_for_job`."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        job.current_step = "fetching-all"
        db.commit()

        try:
            result = await run_sync_all(
                db=db,
                workspace_id=job.workspace_id,
                user_id=job.user_id,
                sync_start_date=sync_start_date,
                sync_end_date=sync_end_date,
                comparison_start_date=comparison_start_date,
                comparison_end_date=comparison_end_date,
            )
            job.status = "completed"
            job.report_id = result["id"]
            job.completed_at = datetime.utcnow()
            job.progress_percent = 100
            job.current_step = "done"
            db.commit()
        except SyncError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error_message = f"Internal error: {exc}"
            job.completed_at = datetime.utcnow()
            db.commit()
            raise
    finally:
        db.close()


async def run_sync_for_job(
    job_id: int,
    *,
    sync_start_date: Optional[str] = None,
    sync_end_date: Optional[str] = None,
    comparison_start_date: Optional[str] = None,
    comparison_end_date: Optional[str] = None,
) -> None:
    """Task-handler entrypoint. Reads the SyncJob, runs the sync, updates
    status. Opens its own DB session because it runs outside any request."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(models.SyncJob).filter(models.SyncJob.id == job_id).first()
        if not job:
            return

        job.status = "running"
        job.started_at = datetime.utcnow()
        job.current_step = "fetching"
        db.commit()

        try:
            result = await run_sync(
                db=db,
                workspace_id=job.workspace_id,
                user_id=job.user_id,
                connection_id=job.connection_id,
                sync_start_date=sync_start_date,
                sync_end_date=sync_end_date,
                comparison_start_date=comparison_start_date,
                comparison_end_date=comparison_end_date,
            )
            job.status = "completed"
            job.report_id = result["id"]
            job.completed_at = datetime.utcnow()
            job.progress_percent = 100
            job.current_step = "done"
            db.commit()
        except SyncError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            job.error_message = f"Internal error: {exc}"
            job.completed_at = datetime.utcnow()
            db.commit()
            raise  # let the queue surface this for retry
    finally:
        db.close()

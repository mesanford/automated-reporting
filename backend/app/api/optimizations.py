from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.database import get_db
from app.models import OptimizationPlan, OptimizationRule, UserSettings, Connection
from app.services.optimizer import generate_and_store_optimizations
from app.services.notifications import notify_new_optimizations
import app.services.connectors as connectors
from app.api.auth import (
    get_current_user,
    get_current_workspace_id,
    record_audit,
    require_role,
)
from app.api.endpoints import _try_decrypt_token, _hydrate_microsoft_customer_map
from app.services.google_ads import execute_google_ads_optimization
from app.services.security import decrypt_token

router = APIRouter()

class GenerateRequest(BaseModel):
    connection_id: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class OptimizationResponse(BaseModel):
    id: int
    user_id: str
    connection_id: int
    platform: str
    campaign_name: str
    ad_group_name: Optional[str] = None
    change_type: str
    original_value: Optional[str] = None
    proposed_value: str
    status: str
    reasoning: Optional[str] = None
    is_automated: int
    
    class Config:
        from_attributes = True

class RuleCreate(BaseModel):
    platform: str
    change_type: str
    is_active: int = 1

class RuleResponse(BaseModel):
    id: int
    user_id: str
    platform: str
    change_type: str
    is_active: int

    class Config:
        from_attributes = True

class SettingsUpdate(BaseModel):
    google_chat_webhook_url: Optional[str] = None

class SettingsResponse(BaseModel):
    user_id: str
    google_chat_webhook_url: Optional[str] = None

    class Config:
        from_attributes = True

@router.post("/generate", response_model=List[OptimizationResponse])
async def generate_optimizations_route(
    req: GenerateRequest, 
    db: Session = Depends(get_db), 
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    connection = db.query(Connection).filter(
        Connection.id == req.connection_id, 
        Connection.workspace_id == workspace_id
    ).first()
    
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Decrypt tokens
    access_token, access_err = _try_decrypt_token(connection.access_token or "")
    refresh_token, refresh_err = _try_decrypt_token(connection.refresh_token or "")
    decrypt_error = access_err or refresh_err
    if decrypt_error:
        raise HTTPException(status_code=400, detail=f"Token decryption failed: {decrypt_error}")

    selected_account_ids = connection.selected_account_ids or []
    accounts_to_sync = selected_account_ids if selected_account_ids else [connection.account_id]
    
    microsoft_customer_map = {}
    if connection.platform == "microsoft":
        microsoft_customer_map = await _hydrate_microsoft_customer_map(
            connection=connection,
            accounts_to_check=[str(a) for a in accounts_to_sync],
            access_token=access_token,
            refresh_token=refresh_token,
            db=db,
        )

    all_data = []
    for account_id in accounts_to_sync:
        df = await connectors.fetch_platform_data(
            connection.platform,
            account_id,
            access_token=access_token,
            refresh_token=refresh_token,
            microsoft_customer_id=microsoft_customer_map.get(str(account_id), ""),
            start_date=req.start_date,
            end_date=req.end_date,
        )
        if not df.empty:
            all_data.append(df.to_dict(orient="records"))
    
    if not all_data:
        raise HTTPException(status_code=400, detail="No performance data retrieved for optimization")

    # Combine data from all accounts
    combined_data = []
    for data in all_data:
        combined_data.extend(data)

    perf_data_dict = {"platform": connection.platform, "data": combined_data}
    
    new_plans = generate_and_store_optimizations(
        db=db,
        user_id=user_id,
        workspace_id=workspace_id,
        connection_id=connection.id,
        platform=connection.platform,
        performance_data=perf_data_dict
    )
    
    notify_new_optimizations(new_plans)
    
    return new_plans

@router.get("", response_model=List[OptimizationResponse])
def get_optimizations(
    status: Optional[str] = None, 
    db: Session = Depends(get_db), 
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    query = db.query(OptimizationPlan).filter(OptimizationPlan.workspace_id == workspace_id)
    if status:
        query = query.filter(OptimizationPlan.status == status)
    return query.all()

@router.post("/{plan_id}/approve", response_model=OptimizationResponse)
def approve_optimization(
    plan_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    _role: str = Depends(require_role(["owner", "admin"])),
):
    plan = db.query(OptimizationPlan).filter(
        OptimizationPlan.id == plan_id,
        OptimizationPlan.workspace_id == workspace_id,
    ).first()

    if not plan:
        raise HTTPException(status_code=404, detail="Optimization plan not found")

    plan.status = "approved"
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="optimization.approve",
        target_type="optimization_plan",
        target_id=plan.id,
        payload={"campaign": plan.campaign_name, "change_type": plan.change_type},
    )
    db.commit()
    db.refresh(plan)
    return plan

@router.post("/{plan_id}/execute", response_model=OptimizationResponse)
def execute_optimization(
    plan_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
    _role: str = Depends(require_role(["owner", "admin"])),
):
    plan = db.query(OptimizationPlan).filter(
        OptimizationPlan.id == plan_id,
        OptimizationPlan.workspace_id == workspace_id
    ).first()
    
    if not plan:
        raise HTTPException(status_code=404, detail="Optimization plan not found")
        
    if plan.status != "approved":
        raise HTTPException(status_code=400, detail="Optimization plan must be approved before execution")

    # Fetch connection to get tokens
    connection = db.query(Connection).filter(
        Connection.id == plan.connection_id,
        Connection.workspace_id == workspace_id
    ).first()

    if not connection:
        raise HTTPException(status_code=404, detail="Associated connection not found")

    if connection.platform == "google":
        # Decrypt refresh token
        refresh_token, refresh_err = _try_decrypt_token(connection.refresh_token or "")
        if refresh_err:
            raise HTTPException(status_code=400, detail=f"Token decryption failed: {refresh_err}")

        # Ensure proposed_value is passed as a string
        proposed_val_str = str(plan.proposed_value)
        
        success = execute_google_ads_optimization(
            customer_id=connection.account_id,
            refresh_token=refresh_token,
            campaign_name=plan.campaign_name,
            ad_group_name=plan.ad_group_name,
            change_type=plan.change_type,
            proposed_value=proposed_val_str
        )
        
        if success:
            plan.status = "executed"
        else:
            plan.status = "failed"
    else:
        # Note: logic for other platforms not implemented
        plan.status = "failed"

    record_audit(
        db,
        workspace_id=workspace_id,
        actor_subject=user_id,
        action="optimization.execute",
        target_type="optimization_plan",
        target_id=plan.id,
        payload={
            "platform": connection.platform,
            "campaign": plan.campaign_name,
            "change_type": plan.change_type,
            "outcome": plan.status,
            "proposed_value": plan.proposed_value,
        },
    )
    db.commit()
    db.refresh(plan)
    return plan

@router.post("/rules", response_model=RuleResponse)
def create_optimization_rule(
    rule: RuleCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    new_rule = OptimizationRule(
        workspace_id=workspace_id,
        user_id=user_id,
        platform=rule.platform,
        change_type=rule.change_type,
        is_active=rule.is_active
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule

@router.get("/rules", response_model=List[RuleResponse])
def list_optimization_rules(
    db: Session = Depends(get_db), 
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    return db.query(OptimizationRule).filter(OptimizationRule.workspace_id == workspace_id).all()

# --- Settings ---
@router.get("/settings", response_model=SettingsResponse)
def get_user_settings(
    db: Session = Depends(get_db), 
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    settings = db.query(UserSettings).filter(UserSettings.workspace_id == workspace_id).first()
    if not settings:
        return {"user_id": user_id, "google_chat_webhook_url": None}
    return settings

@router.put("/settings", response_model=SettingsResponse)
def update_user_settings(
    settings_update: SettingsUpdate, 
    db: Session = Depends(get_db), 
    user_id: str = Depends(get_current_user),
    workspace_id: int = Depends(get_current_workspace_id),
):
    settings = db.query(UserSettings).filter(UserSettings.workspace_id == workspace_id).first()
    if not settings:
        settings = UserSettings(
            workspace_id=workspace_id,
            user_id=user_id,
            google_chat_webhook_url=settings_update.google_chat_webhook_url,
        )
        db.add(settings)
    else:
        if settings_update.google_chat_webhook_url is not None:
            settings.google_chat_webhook_url = settings_update.google_chat_webhook_url
    
    db.commit()
    db.refresh(settings)
    
    # Also update the env var for notifications (though normally you'd read from DB, since the notification
    # function right now reads from env, we can pass it, or we should update notifications.py to read from DB)
    return settings

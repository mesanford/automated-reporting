import json
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from app.models import OptimizationPlan, OptimizationRule
from app.services.gemini import generate_optimizations
import logging

logger = logging.getLogger(__name__)

def parse_optimizations(optimizations_json: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(optimizations_json)
        if isinstance(data, dict) and "error" in data:
            logger.error(f"Gemini returned an error: {data['error']}")
            return []
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse optimizations JSON: {str(e)}")
        # Attempt to clean up markdown blocks if any
        if "```json" in optimizations_json:
            clean_json = optimizations_json.split("```json")[1].split("```")[0].strip()
            try:
                return json.loads(clean_json)
            except Exception:
                pass
        return []

def generate_and_store_optimizations(
    db: Session,
    user_id: str,
    workspace_id: int,
    connection_id: int,
    platform: str,
    performance_data: Dict[str, Any]
) -> List[OptimizationPlan]:
    """
    Calls Gemini to analyze performance data and suggests optimizations.
    Stores the proposed optimizations as OptimizationPlan records.
    If a matching OptimizationRule exists, it marks the plan for auto-execution.
    """
    
    # 1. Fetch JSON from Gemini
    optimizations_json_str = generate_optimizations(performance_data)
    recommendations = parse_optimizations(optimizations_json_str)

    if not recommendations:
        logger.warning("No recommendations returned or failed to parse.")
        return []

    # 2. Fetch active rules for this workspace & platform
    active_rules = db.query(OptimizationRule).filter(
        OptimizationRule.workspace_id == workspace_id,
        OptimizationRule.platform == platform,
        OptimizationRule.is_active == 1
    ).all()
    auto_approve_types = {rule.change_type for rule in active_rules}

    # 3. Create OptimizationPlan records
    new_plans = []
    for rec in recommendations:
        change_type = rec.get("change_type")
        
        # User requested to start with all fixes needing human approval.
        # So we bypass auto-approval rule logic completely for now.
        is_automated = 0
        status = "pending"

        plan = OptimizationPlan(
            workspace_id=workspace_id,
            user_id=user_id,
            connection_id=connection_id,
            platform=platform,
            campaign_name=rec.get("campaign_name"),
            ad_group_name=rec.get("ad_group_name"),
            change_type=change_type,
            original_value=rec.get("original_value"),
            proposed_value=rec.get("proposed_value"),
            status=status,
            reasoning=rec.get("reasoning"),
            is_automated=is_automated
        )
        db.add(plan)
        new_plans.append(plan)
    
    db.commit()
    for plan in new_plans:
        db.refresh(plan)
        
    return new_plans

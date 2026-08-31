import os
import httpx
import logging
from typing import Dict, Any, List, Optional
from app.models import OptimizationPlan

logger = logging.getLogger(__name__)

# To use this, you would set GOOGLE_CHAT_WEBHOOK_URL in .env
GOOGLE_CHAT_WEBHOOK_URL = os.getenv("GOOGLE_CHAT_WEBHOOK_URL")

def send_google_chat_message(webhook_url: str, text: str, cards: Optional[List[Dict[str, Any]]] = None) -> bool:
    """
    Sends a message to a Google Chat webhook.
    """
    if not webhook_url:
        logger.warning("Google Chat webhook URL not provided. Skipping notification.")
        return False
        
    payload: Dict[str, Any] = {"text": text}
    if cards:
        payload["cards"] = cards

    try:
        response = httpx.post(webhook_url, json=payload)
        response.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error(f"Failed to send Google Chat message: {e}")
        return False

def notify_new_optimizations(
    plans: List[OptimizationPlan],
    frontend_base_url: str = "http://localhost:5173",
    webhook_url: Optional[str] = None,
) -> None:
    """
    Sends a Google Chat notification about new optimization plans.

    `webhook_url` should be the workspace's `UserSettings.google_chat_webhook`
    value (per-workspace, DB-persisted via PUT /settings). Falls back to the
    global GOOGLE_CHAT_WEBHOOK_URL env var only if no per-workspace webhook
    is configured, for backward compatibility with single-tenant deployments.
    """
    resolved_webhook_url = webhook_url or GOOGLE_CHAT_WEBHOOK_URL
    if not resolved_webhook_url:
        logger.info("No Google Chat webhook configured (workspace settings or env var), skipping new optimization notifications.")
        return

    if not plans:
        return

    # Group by status
    pending = [p for p in plans if p.status == "pending"]
    auto_approved = [p for p in plans if p.status == "approved" and p.is_automated]

    text_lines = ["🤖 *Automated Reporting: New Optimizations Generated*"]
    
    if auto_approved:
        text_lines.append(f"\n✅ *{len(auto_approved)} Auto-approved Actions:*")
        for p in auto_approved:
            text_lines.append(f"- {p.change_type} for Campaign: `{p.campaign_name}`")

    if pending:
        text_lines.append(f"\n⚠️ *{len(pending)} Pending Actions Require Approval:*")
        for p in pending:
            text_lines.append(f"- {p.change_type} for Campaign: `{p.campaign_name}`")
        
        # Add deep link to review
        review_url = f"{frontend_base_url}/optimizations"
        text_lines.append(f"\n👉 [Review Optimizations]({review_url})")

    message_text = "\n".join(text_lines)
    send_google_chat_message(resolved_webhook_url, message_text)

def send_to_channel(channel: Dict[str, Any], subject: str, body: str) -> Dict[str, Any]:
    """Dispatch one notification to one channel. Returns delivery envelope.

    Supported channel types: `google_chat` (incoming webhook), `slack`
    (incoming webhook), `email` (SendGrid via email_service).
    """
    ctype = (channel.get("type") or "").lower()
    if ctype == "google_chat":
        ok = send_google_chat_message(channel.get("url", ""), f"*{subject}*\n{body}")
        return {"delivered": ok, "channel": "google_chat", "error": None if ok else "send failed"}
    if ctype == "slack":
        url = channel.get("url", "")
        if not url:
            return {"delivered": False, "channel": "slack", "error": "missing url"}
        try:
            r = httpx.post(url, json={"text": f"*{subject}*\n{body}"}, timeout=10.0)
            ok = r.status_code < 300
            return {"delivered": ok, "channel": "slack", "error": None if ok else r.text[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"delivered": False, "channel": "slack", "error": str(exc)}
    if ctype == "email":
        from app.services.email_service import _send_sendgrid  # reuse
        from app.services.secrets_manager import get_secret

        to = channel.get("to", "")
        api_key = get_secret("SENDGRID_API_KEY")
        if not to:
            return {"delivered": False, "channel": "email", "error": "missing to"}
        if not api_key:
            return {"delivered": False, "channel": "email", "error": "SENDGRID_API_KEY not set"}
        return {**_send_sendgrid(api_key, to, subject, body), "channel": "email"}
    return {"delivered": False, "channel": ctype or "unknown", "error": "unsupported channel"}


def notify_optimization_execution(plan: OptimizationPlan, success: bool, details: str = "") -> None:
    """
    Sends a notification about the execution outcome of an optimization.
    """
    if not GOOGLE_CHAT_WEBHOOK_URL:
        return

    icon = "✅" if success else "❌"
    status_str = "SUCCESS" if success else "FAILED"
    
    text = f"{icon} *Optimization Execution {status_str}*\n"
    text += f"Campaign: `{plan.campaign_name}`\n"
    text += f"Action: {plan.change_type}\n"
    
    if details:
        text += f"Details: {details}\n"

    send_google_chat_message(GOOGLE_CHAT_WEBHOOK_URL, text)

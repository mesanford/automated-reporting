from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
import os
import httpx
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.api.auth import get_current_user
from app.services.security import encrypt_token, decrypt_token
from datetime import datetime, timedelta

router = APIRouter()


def _platform_client_id(platform: str) -> str:
    direct = os.getenv(f"{platform.upper()}_CLIENT_ID")
    if direct:
        return direct
    if platform == "google":
        return os.getenv("GOOGLE_ADS_CLIENT_ID", "")
    return ""


def _platform_client_secret(platform: str) -> str:
    direct = os.getenv(f"{platform.upper()}_CLIENT_SECRET")
    if direct:
        return direct
    if platform == "google":
        return os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")
    return ""

# Platform OAuth Config (In production, these come from environment variables)
PLATFORM_CONFIG = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": "https://www.googleapis.com/auth/adwords",
        "extra_params": "&access_type=offline&prompt=consent&include_granted_scopes=true",
    },
    "meta": {
        "auth_url": "https://www.facebook.com/v18.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v18.0/oauth/access_token",
        "scopes": "ads_read,ads_management",
    },
    "linkedin": {
        "auth_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "scopes": "r_ads,r_ads_reporting",
    },
    "tiktok": {
        "auth_url": "https://ads.tiktok.com/marketing_api/auth",
        "token_url": "https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/",
        "scopes": "ad.read ad.manage",
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": "openid offline_access https://ads.microsoft.com/msads.manage",
        "extra_params": "&response_mode=query"
    }
}

@router.get("/{platform}/login")
async def login(platform: str, connection_id: int | None = None):
    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    
    config = PLATFORM_CONFIG[platform]
    
    client_id = _platform_client_id(platform)
    if not client_id:
        raise HTTPException(status_code=500, detail=f"Missing OAuth client ID for platform: {platform}")

    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
    
    # URL encode the redirect_uri and scopes
    import urllib.parse
    encoded_redirect = urllib.parse.quote(redirect_uri, safe='')
    encoded_scopes = urllib.parse.quote(config['scopes'], safe='')
    
    extra = config.get("extra_params", "")
    
    state_value = f"{platform}:{connection_id}" if connection_id is not None else platform

    auth_url = (
        f"{config['auth_url']}?client_id={client_id}"
        f"&redirect_uri={encoded_redirect}&response_type=code"
        f"&scope={encoded_scopes}&state={state_value}{extra}"
    )
    
    return RedirectResponse(auth_url)

@router.get("/callback")
async def callback(
    request: Request, 
    code: str, 
    state: str, 
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    state_parts = (state or "").split(":", 1)
    platform = state_parts[0]
    reconnect_connection_id = int(state_parts[1]) if len(state_parts) > 1 and state_parts[1].isdigit() else None
    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid state")
        
    config = PLATFORM_CONFIG[platform]
    client_id = _platform_client_id(platform)
    client_secret = _platform_client_secret(platform)
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail=f"Missing OAuth credentials for platform: {platform}")
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")

    # 1. Exchange code for tokens
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config["token_url"],
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        tokens = response.json()

    if "access_token" not in tokens:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {tokens}")

    from app.services import connectors

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")

    # Google may omit refresh_token on subsequent auth unless consent is forced.
    # When reconnecting an existing connection, keep prior refresh token if omitted.
    if reconnect_connection_id is not None and not refresh_token:
        prior = db.query(models.Connection).filter(
            models.Connection.id == reconnect_connection_id,
            models.Connection.user_id == user_id,
        ).first()
        if prior and prior.refresh_token:
            try:
                refresh_token = decrypt_token(prior.refresh_token)
            except Exception:
                refresh_token = ""

    discovered_accounts = []
    try:
        discovered_accounts = await connectors.discover_ad_accounts(
            platform=platform,
            parent_account_id="",
            query="",
            access_token=access_token,
            refresh_token=refresh_token,
        )
    except Exception:
        discovered_accounts = []

    primary_account = discovered_accounts[0] if discovered_accounts else None
    account_id = str(primary_account.get("id")) if primary_account else ""
    account_name = str(primary_account.get("name")) if primary_account else f"{platform.capitalize()} Account"

    expires_at = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))

    # 2. Save to DB (update existing connection on reconnect, otherwise create new)
    if reconnect_connection_id is not None:
        existing = db.query(models.Connection).filter(
            models.Connection.id == reconnect_connection_id,
            models.Connection.user_id == user_id,
        ).first()
    else:
        existing = None

    if existing:
        existing.platform = platform
        existing.account_name = account_name
        existing.account_id = account_id
        existing.access_token = encrypt_token(access_token)
        existing.refresh_token = encrypt_token(refresh_token)
        existing.available_accounts = discovered_accounts
        existing.selected_account_ids = [account_id] if account_id else []
        existing.expires_at = expires_at
    else:
        new_conn = models.Connection(
            user_id=user_id,
            platform=platform,
            account_name=account_name,
            account_id=account_id,
            access_token=encrypt_token(access_token),
            refresh_token=encrypt_token(refresh_token),
            available_accounts=discovered_accounts,
            selected_account_ids=[account_id] if account_id else [],
            expires_at=expires_at,
        )
        db.add(new_conn)

    db.commit()
    
    # Redirect back to frontend
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend_url}?auth_success=true")

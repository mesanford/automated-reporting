from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
import os
import httpx
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.api.auth import get_current_user
from app.services.security import encrypt_token
from datetime import datetime, timedelta

router = APIRouter()

# Platform OAuth Config (In production, these come from environment variables)
PLATFORM_CONFIG = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": "https://www.googleapis.com/auth/adwords",
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
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": "openid offline_access https://ads.microsoft.com/msads.manage",
        "extra_params": "&response_mode=query"
    }
}

@router.get("/{platform}/login")
async def login(platform: str):
    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    
    config = PLATFORM_CONFIG[platform]
    
    # Specific client ID for Microsoft, otherwise use environment variable
    if platform == "microsoft":
        client_id = "469f01bc-5257-4b33-a73c-37a09260b14a"
    else:
        client_id = os.getenv(f"{platform.upper()}_CLIENT_ID")

    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
    
    # URL encode the redirect_uri and scopes
    import urllib.parse
    encoded_redirect = urllib.parse.quote(redirect_uri, safe='')
    encoded_scopes = urllib.parse.quote(config['scopes'], safe='')
    
    extra = config.get("extra_params", "")
    
    auth_url = (
        f"{config['auth_url']}?client_id={client_id}"
        f"&redirect_uri={encoded_redirect}&response_type=code"
        f"&scope={encoded_scopes}&state={platform}{extra}"
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
    platform = state # We passed the platform in the state field
    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid state")
        
    config = PLATFORM_CONFIG[platform]
    client_id = os.getenv(f"{platform.upper()}_CLIENT_ID")
    client_secret = os.getenv(f"{platform.upper()}_CLIENT_SECRET")
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
            }
        )
        tokens = response.json()

    if "access_token" not in tokens:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {tokens}")

    # 2. Encrypt and Save to DB
    # Note: In a real flow, you'd also fetch the account_name/id from the platform API here
    new_conn = models.Connection(
        user_id=user_id,
        platform=platform,
        account_name=f"{platform.capitalize()} Account", # Placeholder
        account_id="FETCHING...", # Placeholder
        access_token=encrypt_token(tokens["access_token"]),
        refresh_token=encrypt_token(tokens.get("refresh_token", "")),
        expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))
    )
    
    db.add(new_conn)
    db.commit()
    
    # Redirect back to frontend
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend_url}?auth_success=true")

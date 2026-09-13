from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.api.auth import get_current_user, _ensure_personal_workspace
from app.services.security import encrypt_token, decrypt_token
from app.services.secrets_manager import get_secret
from datetime import datetime, timedelta

router = APIRouter()


# ── Signed OAuth state (v3) ─────────────────────────────────────────────────
#
# Problem: the OAuth provider redirects the browser to `/api/auth/callback`
# as a top-level navigation. There's no way to attach a Firebase Bearer
# token to that request, so the callback can't authenticate the user the
# usual way.
#
# Fix: the frontend POSTs to `/api/auth/oauth/start` *with* its Bearer; the
# server bakes the user's identity into an HMAC-signed `state` parameter,
# returns the full provider URL, and the frontend navigates. The callback
# verifies the signature instead of needing a header. State expires after
# 10 minutes, so a leaked URL doesn't become a permanent capability.

STATE_TTL_SECONDS = 600


def _signing_key() -> bytes:
    """HMAC key for signed state. Reuses the existing Fernet key as the
    secret source — it's already required, persistent across restarts,
    and rotated through the same operational path. Domain-separated by
    HKDF-style hash so it can't be confused with a Fernet key."""
    raw = get_secret("OAUTH_STATE_SECRET") or get_secret("ENCRYPTION_KEY") or ""
    if not raw:
        # Fall back to the local dev key file so the noop path works in tests.
        from app.services.security import _load_or_create_local_key

        raw = _load_or_create_local_key()
    return hashlib.sha256(b"oauth-state:" + raw.encode()).digest()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + pad).encode("ascii"))


def sign_state(
    *,
    platform: str,
    workspace_id: int | None,
    connection_id: int | None,
    user_subject: str,
) -> str:
    """Pack identity + targets into a tamper-evident, expiring state."""
    payload = {
        "p": platform,
        "w": workspace_id,
        "c": connection_id,
        "u": user_subject,
        "e": int(time.time()) + STATE_TTL_SECONDS,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64e(hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"v3.{body}.{sig}"


class SignedState(BaseModel):
    platform: str
    workspace_id: int | None
    connection_id: int | None
    user_subject: str


def verify_state(state: str) -> SignedState | None:
    """Validate a v3 state. Returns the payload or None if invalid/expired."""
    if not state or not state.startswith("v3."):
        return None
    try:
        _, body, sig = state.split(".", 2)
    except ValueError:
        return None

    expected_sig = _b64e(
        hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None

    if int(payload.get("e", 0)) < int(time.time()):
        return None
    if not payload.get("u") or not payload.get("p"):
        return None

    return SignedState(
        platform=payload["p"],
        workspace_id=payload.get("w"),
        connection_id=payload.get("c"),
        user_subject=payload["u"],
    )


def _platform_client_id(platform: str) -> str:
    direct = get_secret(f"{platform.upper()}_CLIENT_ID")
    if direct:
        return direct
    if platform == "google":
        return get_secret("GOOGLE_ADS_CLIENT_ID")
    if platform == "google_analytics":
        # Same Google Cloud OAuth client can request additional scopes —
        # reuse the Ads client if a dedicated GA4 one isn't configured.
        return get_secret("GOOGLE_ADS_CLIENT_ID")
    if platform in ("facebook_organic", "instagram_organic"):
        return get_secret("META_CLIENT_ID")
    if platform == "linkedin_organic":
        return get_secret("LINKEDIN_CLIENT_ID")
    return ""


def _platform_client_secret(platform: str) -> str:
    direct = get_secret(f"{platform.upper()}_CLIENT_SECRET")
    if direct:
        return direct
    if platform == "google":
        return get_secret("GOOGLE_ADS_CLIENT_SECRET")
    if platform == "google_analytics":
        return get_secret("GOOGLE_ADS_CLIENT_SECRET")
    if platform in ("facebook_organic", "instagram_organic"):
        return get_secret("META_CLIENT_SECRET")
    if platform == "linkedin_organic":
        return get_secret("LINKEDIN_CLIENT_SECRET")
    return ""

# Platform OAuth Config (In production, these come from environment variables)
PLATFORM_CONFIG = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": "https://www.googleapis.com/auth/adwords",
        "extra_params": "&access_type=offline&prompt=consent&include_granted_scopes=true",
    },
    "google_analytics": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # Read-only GA4 scope — deliberately separate from the Ads scope
        # ("google" platform above) so a workspace can connect GA4 without
        # granting Google Ads write access, and vice versa.
        "scopes": "https://www.googleapis.com/auth/analytics.readonly",
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
    },
    "facebook_organic": {
        "auth_url": "https://www.facebook.com/v18.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v18.0/oauth/access_token",
        "scopes": "pages_read_engagement,pages_show_list,public_profile",
    },
    "instagram_organic": {
        "auth_url": "https://www.facebook.com/v18.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v18.0/oauth/access_token",
        "scopes": "instagram_basic,instagram_manage_insights,pages_read_engagement,pages_show_list,public_profile",
    },
    "linkedin_organic": {
        "auth_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        # Dropped w_organization_social (write scope, not needed for read-only
        # reporting) to reduce the permission ask during OAuth consent.
        # NOTE: reading organizationAcls/organizationalEntityShareStatistics
        # may require LinkedIn's Marketing Developer Platform / Community
        # Management API product access beyond just these scopes — verify
        # against a real LinkedIn developer app before relying on this.
        "scopes": "r_organization_social,r_basicprofile",
    }
}

def _encode_state(
    platform: str,
    workspace_id: int | None,
    connection_id: int | None,
) -> str:
    """Pack platform + workspace + reconnect target into the OAuth `state`.

    v2 format: `v2:{platform}:{workspace_id}[:{connection_id}]`. Legacy
    format (`{platform}[:{connection_id}]`) is still accepted on decode for
    callbacks already in flight at deploy time — it falls back to the
    user's Personal workspace.
    """
    if workspace_id is not None:
        base = f"v2:{platform}:{workspace_id}"
        return f"{base}:{connection_id}" if connection_id is not None else base
    # Legacy fallback (no workspace context available at login start).
    return f"{platform}:{connection_id}" if connection_id is not None else platform


def _decode_state(state: str) -> tuple[str, int | None, int | None]:
    """Inverse of `_encode_state`. Returns (platform, workspace_id, connection_id)."""
    parts = (state or "").split(":")
    if len(parts) >= 2 and parts[0] == "v2":
        platform = parts[1]
        workspace_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        connection_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
        return platform, workspace_id, connection_id

    platform = parts[0]
    connection_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return platform, None, connection_id


@router.get("/{platform}/login")
async def login(
    platform: str,
    workspace_id: int | None = None,
    connection_id: int | None = None,
):
    """DEPRECATED — kept alive only when `ALLOW_DEV_AUTH=1` is set.

    In production, browsers can't attach a Bearer token to the OAuth
    callback (it's a top-level navigation), so this endpoint can't safely
    identify the user on the way back. The signed-state flow at
    `POST /api/auth/oauth/start` is the supported path: the frontend
    fetches it with its Firebase token and gets a redirect URL whose
    `state` parameter carries an HMAC-signed identity.

    Removed entirely outside dev to prevent confused-deputy attacks
    where an attacker links a victim's browser at this endpoint and the
    callback attaches the resulting connection to whichever account the
    victim happens to be signed into."""
    if os.getenv("ALLOW_DEV_AUTH", "0") != "1":
        raise HTTPException(
            status_code=410,
            detail={
                "message": (
                    "GET /api/auth/{platform}/login is retired. "
                    "Use POST /api/auth/oauth/start with your Bearer token "
                    "to receive a signed redirect URL."
                ),
                "replacement": "/api/auth/oauth/start",
            },
        )

    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    config = PLATFORM_CONFIG[platform]
    client_id = _platform_client_id(platform)
    if not client_id:
        raise HTTPException(status_code=500, detail=f"Missing OAuth client ID for platform: {platform}")

    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
    encoded_redirect = urllib.parse.quote(redirect_uri, safe='')
    encoded_scopes = urllib.parse.quote(config['scopes'], safe='')
    extra = config.get("extra_params", "")

    # Dev path: legacy unsigned state. The callback's dev-auth fallback
    # picks up the user from X-User-Id.
    state_value = _encode_state(platform, workspace_id, connection_id)

    auth_url = (
        f"{config['auth_url']}?client_id={client_id}"
        f"&redirect_uri={encoded_redirect}&response_type=code"
        f"&scope={encoded_scopes}&state={state_value}{extra}"
    )
    return RedirectResponse(auth_url)


class OAuthStartBody(BaseModel):
    platform: str
    workspace_id: int | None = None
    connection_id: int | None = None


@router.post("/oauth/start")
async def oauth_start(
    body: OAuthStartBody,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Build the provider redirect URL with HMAC-signed state.

    Authenticated via Bearer (or X-User-Id in dev). The frontend calls
    this with its Firebase token, gets back a URL, and navigates the
    browser to it. The callback then verifies the signed state and
    doesn't need a header on the redirect.
    """
    if body.platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    config = PLATFORM_CONFIG[body.platform]
    client_id = _platform_client_id(body.platform)
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail=f"Missing OAuth client ID for platform: {body.platform}",
        )

    # If a workspace was specified, verify membership before we sign it
    # into state. Anything in state should reflect a fact we already
    # verified server-side at sign time.
    if body.workspace_id is not None:
        membership = (
            db.query(models.Membership)
            .filter(
                models.Membership.workspace_id == body.workspace_id,
                models.Membership.user_subject == user_id,
            )
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=403,
                detail="Not a member of the target workspace.",
            )

    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback")
    encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
    encoded_scopes = urllib.parse.quote(config["scopes"], safe="")
    extra = config.get("extra_params", "")

    state_value = sign_state(
        platform=body.platform,
        workspace_id=body.workspace_id,
        connection_id=body.connection_id,
        user_subject=user_id,
    )

    auth_url = (
        f"{config['auth_url']}?client_id={client_id}"
        f"&redirect_uri={encoded_redirect}&response_type=code"
        f"&scope={encoded_scopes}&state={state_value}{extra}"
    )
    return {"redirect_url": auth_url}


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """OAuth provider redirects the browser here. We cannot rely on a
    Bearer header on this navigation, so the user identity is recovered
    from a signed `state` (v3) instead. Legacy v1/v2 state falls back to
    the X-User-Id / dev-auth path for backward compatibility — that path
    is only useful in dev because production browsers won't attach the
    header on a top-level navigation."""
    signed = verify_state(state)
    if signed is not None:
        # Signed-state path: trust the embedded subject.
        platform = signed.platform
        state_workspace_id = signed.workspace_id
        reconnect_connection_id = signed.connection_id
        user_id = signed.user_subject
    else:
        # Legacy path: decode unsigned state, fall back to header-based auth.
        platform, state_workspace_id, reconnect_connection_id = _decode_state(state)
        from app.api.auth import get_current_identity

        identity = await get_current_identity(
            authorization=request.headers.get("authorization"),
            x_user_id=request.headers.get("x-user-id"),
        )
        user_id = identity.uid

    if platform not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid state")

    # Verify workspace membership server-side even for signed state —
    # memberships can change between login start and callback.
    if state_workspace_id is not None:
        membership = (
            db.query(models.Membership)
            .filter(
                models.Membership.workspace_id == state_workspace_id,
                models.Membership.user_subject == user_id,
            )
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=403,
                detail="Not a member of the workspace this OAuth flow targeted.",
            )
        workspace_id = state_workspace_id
    else:
        workspace_id = _ensure_personal_workspace(db, user_id)
        
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
            models.Connection.workspace_id == workspace_id,
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

    # Capture the manager context this connection reaches its accounts through,
    # so syncs never have to guess it from the environment. Empty means the
    # account is directly accessible and needs no manager header.
    login_customer_id = ""
    for acct in discovered_accounts:
        candidate = str(acct.get("login_customer_id") or "").strip()
        if candidate:
            login_customer_id = candidate
            break

    expires_at = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600))

    # 2. Save to DB (update existing connection on reconnect, otherwise create new)
    if reconnect_connection_id is not None:
        existing = db.query(models.Connection).filter(
            models.Connection.id == reconnect_connection_id,
            models.Connection.workspace_id == workspace_id,
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
        existing.login_customer_id = login_customer_id or None
        existing.expires_at = expires_at
    else:
        new_conn = models.Connection(
            workspace_id=workspace_id,
            user_id=user_id,
            platform=platform,
            account_name=account_name,
            account_id=account_id,
            access_token=encrypt_token(access_token),
            refresh_token=encrypt_token(refresh_token),
            available_accounts=discovered_accounts,
            selected_account_ids=[account_id] if account_id else [],
            login_customer_id=login_customer_id or None,
            expires_at=expires_at,
        )
        db.add(new_conn)

    db.commit()
    
    # Redirect back to frontend
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend_url}?auth_success=true")

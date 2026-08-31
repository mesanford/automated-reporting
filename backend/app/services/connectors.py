import pandas as pd
from datetime import date, datetime, timedelta
import os
import asyncio
import json
import re
import hmac
import hashlib
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
import csv
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from google.ads.googleads.client import GoogleAdsClient
from bingads.authorization import AuthorizationData, OAuthDesktopMobileAuthCodeGrant, OAuthTokens
try:
    from bingads.authorization import OAuthWebAuthCodeGrant
except Exception:
    OAuthWebAuthCodeGrant = None  # type: ignore[assignment]
from bingads.v13.reporting import ReportingServiceManager, ReportingDownloadParameters
from bingads.service_client import ServiceClient


def _demo_mode_allowed() -> bool:
    """Whether fabricated (mock) connector data is allowed at all.

    Requires an explicit `DEMO_MODE=1` opt-in, and is hard-refused in
    production regardless of that flag — mirrors app.preflight's fail-loud
    pattern for other unsafe-in-prod settings. There is no silent fallback:
    callers that hit this returning False must raise, not substitute fake
    data quietly.
    """
    from app.preflight import is_production

    if is_production():
        return False
    return os.environ.get("DEMO_MODE", "0") == "1"


class ConnectorError(Exception):
    pass


class ConnectorConfigError(ConnectorError):
    pass


DEFAULT_SYNC_WINDOW_DAYS = 14
MAX_SYNC_WINDOW_DAYS = 90

DEFAULT_META_CONVERSION_ACTION_TYPES = [
    "purchase",
    "lead",
    "subscribe",
    "complete_registration",
    "offsite_conversion.purchase",
    "offsite_conversion.fb_pixel_purchase",
    "offsite_conversion.lead",
    "offsite_conversion.fb_pixel_lead",
    "offsite_conversion.complete_registration",
    "offsite_conversion.fb_pixel_complete_registration",
    "offsite_conversion.subscribe",
    "offsite_conversion.fb_pixel_subscribe",
    "onsite_conversion.purchase",
    "onsite_conversion.lead_grouped",
    "onsite_conversion.complete_registration",
]

DEFAULT_META_REVENUE_ACTION_TYPES = [
    "purchase",
    "omni_purchase",
    "offsite_conversion.purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
]


def _resolve_sync_window(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[date, date]:
    today = datetime.utcnow().date()

    if bool(start_date) != bool(end_date):
        raise ConnectorConfigError("Provide both start_date and end_date when using a custom sync range.")

    if start_date and end_date:
        try:
            resolved_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            resolved_end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ConnectorConfigError("Sync dates must use YYYY-MM-DD format.") from exc
    else:
        resolved_end = today
        resolved_start = today - timedelta(days=DEFAULT_SYNC_WINDOW_DAYS - 1)

    if resolved_start > resolved_end:
        raise ConnectorConfigError("start_date must be on or before end_date.")

    window_days = (resolved_end - resolved_start).days + 1
    if window_days > MAX_SYNC_WINDOW_DAYS:
        raise ConnectorConfigError(
            f"Sync range cannot exceed {MAX_SYNC_WINDOW_DAYS} days."
        )

    return resolved_start, resolved_end


def _filter_accounts(accounts: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return accounts
    return [
        a for a in accounts
        if q in str(a.get("id", "")).lower() or q in str(a.get("name", "")).lower()
    ]


def _required_env(name: str) -> str:
    from app.services.secrets_manager import get_secret

    value = (get_secret(name) or "").strip()
    if not value:
        raise ConnectorConfigError(f"Missing required configuration: {name}")
    return value


def _env_csv(name: str) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _build_microsoft_oauth(
    client_id: str,
    access_token: str,
    refresh_token: Optional[str],
) -> Any:
    oauth_tokens = OAuthTokens(
        access_token=access_token,
        refresh_token=(refresh_token or "") or None,
    )
    from app.services.secrets_manager import get_secret

    client_secret = get_secret("MICROSOFT_CLIENT_SECRET").strip()
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/callback").strip()
    if not redirect_uri:
        raise ConnectorConfigError("Missing OAUTH_REDIRECT_URI for Microsoft OAuth.")

    if client_secret:
        if OAuthWebAuthCodeGrant is None:
            raise ConnectorConfigError(
                "MICROSOFT_CLIENT_SECRET is configured, but this bingads SDK build does not support "
                "OAuthWebAuthCodeGrant. Upgrade the bingads package or remove MICROSOFT_CLIENT_SECRET "
                "to use desktop/mobile OAuth."
            )
        return OAuthWebAuthCodeGrant(
            client_id=client_id,
            client_secret=client_secret,
            redirection_uri=redirect_uri,
            oauth_tokens=oauth_tokens,
        )

    return OAuthDesktopMobileAuthCodeGrant(
        client_id=client_id,
        oauth_tokens=oauth_tokens,
    )


def _refresh_microsoft_oauth_if_possible(oauth: Any, refresh_token: Optional[str]) -> bool:
    token = (refresh_token or "").strip()
    if not token:
        return False
    try:
        oauth.request_oauth_tokens_by_refresh_token(token)
        return True
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise ConnectorError(
            "Microsoft OAuth token refresh failed. Reconnect Microsoft Ads to continue syncing. "
            f"Details: {detail}"
        ) from exc


def _strip_google_customer_id(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _linkedin_version_candidates() -> List[str]:
    configured = os.getenv("LINKEDIN_API_VERSION", "").strip()
    if configured:
        return [configured]

    # Strategy: Use the previous calendar month.
    now = datetime.utcnow()
    prev_year = now.year if now.month > 1 else now.year - 1
    prev_month = now.month - 1 if now.month > 1 else 12
    return [f"{prev_year}{prev_month:02d}"]


def _parse_linkedin_error(response: httpx.Response, fallback: str) -> str:
    message = fallback
    try:
        payload = response.json()
        msg = str(payload.get("message") or payload.get("error") or "").strip()
        if msg:
            message = msg
        # Include errorDetails if present (LinkedIn includes detailed validation info)
        if "errorDetails" in payload:
            details = payload.get("errorDetails", {})
            if isinstance(details, dict):
                detail_msgs = [f"{k}: {v}" for k, v in details.items()]
                if detail_msgs:
                    message = f"{message} | Details: {'; '.join(detail_msgs)}"
            elif isinstance(details, list) and details:
                message = f"{message} | Details: {details[0]}"
        # Also include the raw payload for comprehensive debugging
        message = f"{message} | Raw response: {str(payload)[:200]}"
    except Exception:
        body = (response.text or "").strip()
        if body:
            message = f"{fallback} | Raw response: {body[:300]}"
    return message


def _linkedin_version_unsupported(response: httpx.Response) -> bool:
    if response.status_code == 426:
        return True

    try:
        payload = response.json()
    except Exception:
        return False

    message = str(payload.get("message") or payload.get("error") or "").lower()
    if "linkedin api version" in message and "no longer supported" in message:
        return True

    details = payload.get("errorDetails")
    if isinstance(details, dict):
        details_text = str(details).lower()
        if "no longer supported" in details_text and "version" in details_text:
            return True

    return False


def _linkedin_headers(access_token: str, version: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if version:
        headers["LinkedIn-Version"] = version
    return headers


def _linkedin_query_string(params: Dict[str, Any]) -> str:
    # LinkedIn's projection parser is sensitive to encoded separators.
    # Keep Rest.li delimiters ((),: and commas) unescaped in values.
    parts: List[str] = []
    for key, value in params.items():
        # Preserve dot-notation keys such as dateRange.start.year.
        encoded_key = quote(str(key), safe="._-")
        # Keep projection/list delimiters but encode colons for URNs/date objects.
        encoded_value = quote(str(value), safe="(),")
        parts.append(f"{encoded_key}={encoded_value}")
    return "&".join(parts)


def _linkedin_encode_urn(urn: str) -> str:
    return quote(urn, safe="")


def _linkedin_disallows_date_range(response: httpx.Response) -> bool:
    try:
        payload = response.json()
        details = payload.get("errorDetails", {})
        input_errors = details.get("inputErrors", []) if isinstance(details, dict) else []
        if not isinstance(input_errors, list):
            return False

        for item in input_errors:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", "")).strip().upper()
            field_path = str(
                item.get("input", {})
                .get("inputPath", {})
                .get("fieldPath", "")
            )
            if code in {"QUERY_PARAM_NOT_ALLOWED", "PARAM_INVALID"} and field_path.startswith("dateRange"):
                return True
    except Exception:
        return False

    return False


def _linkedin_date_range_related_error(response: httpx.Response) -> bool:
    try:
        payload = response.json()
        message = str(payload.get("message", "")).lower()
        if "daterange" in message:
            return True

        details = payload.get("errorDetails", {})
        input_errors = details.get("inputErrors", []) if isinstance(details, dict) else []
        if not isinstance(input_errors, list):
            return False

        for item in input_errors:
            if not isinstance(item, dict):
                continue
            field_path = str(
                item.get("input", {})
                .get("inputPath", {})
                .get("fieldPath", "")
            )
            if field_path.startswith("dateRange"):
                return True
    except Exception:
        return False

    return False


def _linkedin_invalid_query_params_error(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except Exception:
        return False

    code = str(payload.get("code", "")).upper()
    message = str(payload.get("message", "")).lower()
    return code == "ILLEGAL_ARGUMENT" or "invalid query parameters" in message


def _meta_appsecret_proof(access_token: str) -> str:
    from app.services.secrets_manager import get_secret

    app_secret = (get_secret("META_CLIENT_SECRET") or get_secret("FACEBOOK_APP_SECRET")).strip()
    if not app_secret:
        raise ConnectorConfigError("Missing Meta app secret (META_CLIENT_SECRET) required for appsecret_proof.")
    return hmac.new(
        app_secret.encode("utf-8"),
        msg=access_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


async def _discover_meta_accounts(access_token: str) -> List[Dict[str, Any]]:
    url = "https://graph.facebook.com/v20.0/me/adaccounts"
    headers = {
        "Authorization": f"Bearer {access_token}",
    }
    appsecret_proof = _meta_appsecret_proof(access_token)
    params = {
        "fields": "id,name,account_status,currency",
        "limit": 200,
        "appsecret_proof": appsecret_proof,
    }
    accounts = []
    after_cursor: Optional[str] = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            request_params: Dict[str, Any] = dict(params)
            if after_cursor:
                request_params["after"] = after_cursor

            response = await client.get(url, headers=headers, params=request_params)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                message = "Meta account discovery failed"
                try:
                    err = exc.response.json().get("error", {})
                    err_msg = str(err.get("message", "")).strip()
                    err_code = err.get("code")
                    if err_msg:
                        message = err_msg
                    if err_code is not None:
                        message = f"{message} (code {err_code})"
                except Exception:
                    pass
                raise ConnectorError(message) from exc

            payload = response.json()
            for row in payload.get("data", []):
                account_id = str(row.get("id", ""))
                accounts.append({
                    "id": account_id,
                    "name": row.get("name") or f"Meta Account {account_id}",
                    "status": str(row.get("account_status", "unknown")),
                    "currency": row.get("currency", "USD"),
                })

            after_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after_cursor:
                break

    return accounts


async def _discover_linkedin_accounts(access_token: str) -> List[Dict[str, Any]]:
    # LinkedIn Marketing API (REST) account discovery.
    url = "https://api.linkedin.com/rest/adAccounts"
    params: Dict[str, Any] = {
        "q": "search",
        "count": 200,
    }
    payload: Dict[str, Any] = {}
    last_error = "LinkedIn account discovery failed"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for version in _linkedin_version_candidates():
            headers = _linkedin_headers(access_token, version)
            response = await client.get(url, headers=headers, params=params)
            if _linkedin_version_unsupported(response):
                last_error = f"LinkedIn API version {version} is no longer supported"
                continue
            if response.status_code >= 400:
                message = _parse_linkedin_error(response, "LinkedIn account discovery failed")
                raise ConnectorError(f"{message} (code {response.status_code})")
            payload = response.json()
            break

    if not payload:
        raise ConnectorError(last_error)

    accounts = []
    for row in payload.get("elements", []):
        raw_id = str(row.get("id", ""))
        account_id = raw_id.replace("urn:li:sponsoredAccount:", "")
        accounts.append({
            "id": account_id,
            "name": row.get("name") or f"LinkedIn Account {account_id}",
            "status": str(row.get("status", "ACTIVE")),
            "currency": row.get("currency", "USD"),
        })
    return accounts


async def _discover_tiktok_accounts(access_token: str) -> List[Dict[str, Any]]:
    # TikTok Marketing API advertiser discovery.
    url = "https://business-api.tiktok.com/open_api/v1.3/oauth2/advertiser/get/"
    headers = {
        "Access-Token": access_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()

    if payload.get("code") not in (0, "0"):
        raise ConnectorError(f"TikTok advertiser discovery failed: {payload.get('message', 'unknown error')}")

    accounts = []
    for row in payload.get("data", {}).get("list", []):
        account_id = str(row.get("advertiser_id", ""))
        accounts.append({
            "id": account_id,
            "name": row.get("advertiser_name") or f"TikTok Account {account_id}",
            "status": "ACTIVE",
            "currency": row.get("currency", "USD"),
        })
    return accounts


async def _discover_google_accounts(refresh_token: Optional[str]) -> List[Dict[str, Any]]:
    developer_token = _required_env("GOOGLE_ADS_DEVELOPER_TOKEN")
    client_id = _required_env("GOOGLE_ADS_CLIENT_ID")
    client_secret = _required_env("GOOGLE_ADS_CLIENT_SECRET")
    final_refresh_token = str(refresh_token or os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")).strip()
    if not final_refresh_token:
        raise ConnectorConfigError("Missing Google Ads refresh token for this connection.")

    credentials = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": final_refresh_token,
        "use_proto_plus": True,
    }

    login_customer_id = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
    if login_customer_id:
        credentials["login_customer_id"] = _strip_google_customer_id(login_customer_id)

    client = GoogleAdsClient.load_from_dict(credentials)
    customer_service = client.get_service("CustomerService")
    google_ads_service = client.get_service("GoogleAdsService")
    resources = customer_service.list_accessible_customers().resource_names
    accessible_ids = [_strip_google_customer_id(r.split("/")[-1]) for r in resources]

    client_name_map: Dict[str, Dict[str, Any]] = {}
    client_query = """
        SELECT
          customer_client.id,
          customer_client.descriptive_name,
          customer_client.manager,
          customer_client.currency_code
        FROM customer_client
    """

    manager_seed_ids: List[str] = []
    configured_login_id = _strip_google_customer_id(login_customer_id)
    if configured_login_id:
        manager_seed_ids.append(configured_login_id)
    for cid in accessible_ids:
        if cid and cid not in manager_seed_ids:
            manager_seed_ids.append(cid)

    for parent_id in manager_seed_ids:
        try:
            resp = google_ads_service.search(customer_id=parent_id, query=client_query)
            for row in resp:
                cid = str(getattr(row.customer_client, "id", "") or "")
                if not cid:
                    continue
                client_name_map[cid] = {
                    "name": (row.customer_client.descriptive_name or "").strip(),
                    "manager": bool(row.customer_client.manager),
                    "currency": (row.customer_client.currency_code or "USD").strip() or "USD",
                }
        except Exception:
            continue

    # Backfill each seed account's own customer metadata, useful when customer_client rows are sparse.
    self_query = """
        SELECT
          customer.id,
          customer.descriptive_name,
          customer.manager,
          customer.currency_code
        FROM customer
        LIMIT 1
    """
    for parent_id in manager_seed_ids:
        try:
            resp = google_ads_service.search(customer_id=parent_id, query=self_query)
            for row in resp:
                cid = str(getattr(row.customer, "id", "") or "")
                if not cid:
                    continue
                existing = client_name_map.get(cid, {})
                name = (row.customer.descriptive_name or "").strip() or str(existing.get("name", ""))
                client_name_map[cid] = {
                    "name": name,
                    "manager": bool(row.customer.manager) if name else bool(existing.get("manager", False)),
                    "currency": (row.customer.currency_code or "USD").strip() or str(existing.get("currency", "USD")),
                }
        except Exception:
            continue

    accounts = []
    for resource in resources:
        raw_customer_id = resource.split("/")[-1]
        customer_id = _strip_google_customer_id(raw_customer_id)
        resource_name = f"customers/{customer_id}"
        name = ""
        currency = "USD"
        status = "ACTIVE"
        is_manager = False

        try:
            # Most reliable metadata lookup for a specific customer resource.
            customer_obj = customer_service.get_customer(resource_name=resource_name)
            name = (customer_obj.descriptive_name or "").strip()
            currency = (customer_obj.currency_code or "USD").strip() or "USD"
            is_manager = bool(customer_obj.manager)
        except Exception:
            try:
                # Fallback query path for environments where get_customer may fail.
                q = """
                    SELECT
                      customer.id,
                      customer.descriptive_name,
                      customer.manager,
                      customer.currency_code
                    FROM customer
                    LIMIT 1
                """
                resp = google_ads_service.search(customer_id=customer_id, query=q)
                for row in resp:
                    name = (row.customer.descriptive_name or "").strip()
                    currency = (row.customer.currency_code or "USD").strip() or "USD"
                    is_manager = bool(row.customer.manager)
                    break
            except Exception:
                # Keep discovery resilient and surface account id even if metadata lookup fails.
                pass

        if not name:
            mapped = client_name_map.get(customer_id, {})
            if mapped.get("name"):
                name = str(mapped.get("name"))
                currency = str(mapped.get("currency", currency))
                is_manager = bool(mapped.get("manager", is_manager))

        if not name:
            name = f"Google Ads {customer_id}"
        if is_manager and "manager" not in name.lower():
            name = f"{name} (Manager)"

        accounts.append({
            "id": customer_id,
            "name": name,
            "status": status,
            "currency": currency,
        })
    return accounts


async def _discover_google_analytics_properties(refresh_token: Optional[str]) -> List[Dict[str, Any]]:
    """List GA4 properties the connected user can access, via the Admin API
    (a separate API surface from the Data API used for reporting)."""
    from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

    credentials = _google_analytics_credentials(refresh_token)

    def _list_properties() -> List[Dict[str, Any]]:
        client = AnalyticsAdminServiceClient(credentials=credentials)
        accounts: List[Dict[str, Any]] = []
        for summary in client.list_account_summaries():
            for prop_summary in summary.property_summaries:
                # property_summary.property is "properties/{id}"
                prop_id = prop_summary.property.split("/")[-1]
                accounts.append({
                    "id": prop_id,
                    "name": prop_summary.display_name or f"GA4 Property {prop_id}",
                    "status": "ACTIVE",
                    "currency": "N/A",  # GA4 properties don't carry a currency at this level.
                })
        return accounts

    return await asyncio.to_thread(_list_properties)


def _normalize_ms_accounts_payload(raw_accounts: Any) -> List[Dict[str, Any]]:
    if raw_accounts is None:
        return []

    items: Any = raw_accounts
    if hasattr(raw_accounts, "AdvertiserAccount"):
        items = raw_accounts.AdvertiserAccount
    elif hasattr(raw_accounts, "AccountInfoWithCustomerData"):
        items = raw_accounts.AccountInfoWithCustomerData
    elif hasattr(raw_accounts, "AccountsInfo"):
        items = raw_accounts.AccountsInfo

    if not isinstance(items, list):
        items = [items]

    accounts: List[Dict[str, Any]] = []
    for account in items:
        if account is None:
            continue
        account_id = str(getattr(account, "Id", "") or "").strip()
        if not account_id:
            continue
        account_name = str(getattr(account, "Name", "") or "").strip()
        status = str(getattr(account, "AccountLifeCycleStatus", getattr(account, "Status", "ACTIVE")) or "ACTIVE")
        currency = str(getattr(account, "CurrencyCode", getattr(account, "CurrencyType", "USD")) or "USD")
        parent_customer_id = str(getattr(account, "ParentCustomerId", "") or "").strip()
        accounts.append({
            "id": account_id,
            "name": account_name or f"Microsoft Ads {account_id}",
            "status": status,
            "currency": currency,
            "customer_id": parent_customer_id,
        })
    return accounts


def _normalize_ms_customer_payload(raw_customers: Any) -> List[Dict[str, str]]:
    if raw_customers is None:
        return []

    items: Any = raw_customers
    if hasattr(raw_customers, "CustomerInfo"):
        items = raw_customers.CustomerInfo

    if not isinstance(items, list):
        items = [items]

    customers: List[Dict[str, str]] = []
    for customer in items:
        if customer is None:
            continue
        customer_id = str(getattr(customer, "Id", "") or "").strip()
        if not customer_id:
            continue
        customer_name = str(getattr(customer, "Name", "") or "").strip()
        customers.append({
            "id": customer_id,
            "name": customer_name or f"Microsoft Customer {customer_id}",
        })
    return customers


def _discover_microsoft_accounts_sdk_sync(
    access_token: str,
    refresh_token: Optional[str],
) -> List[Dict[str, Any]]:
    developer_token = _required_env("MICROSOFT_DEVELOPER_TOKEN")
    client_id = _required_env("MICROSOFT_CLIENT_ID")
    configured_customer = os.getenv("MICROSOFT_CUSTOMER_ID", "").strip()

    oauth = _build_microsoft_oauth(client_id, access_token, refresh_token)
    _refresh_microsoft_oauth_if_possible(oauth, refresh_token)

    customer_id_value = None
    if configured_customer:
        try:
            customer_id_value = int(configured_customer)
        except Exception:
            customer_id_value = None

    auth_data = AuthorizationData(
        account_id=None,
        customer_id=customer_id_value,
        developer_token=developer_token,
        authentication=oauth,
    )

    service = ServiceClient(
        service="CustomerManagementService",
        version=13,
        authorization_data=auth_data,
        environment="production",
    )

    def _call_operation(name: str, request: Any) -> Any:
        operation = getattr(service, name)
        try:
            return operation(**dict(request))
        except TypeError:
            return operation(request)

    def _fetch_accounts_for_customer(customer_id: int) -> List[Dict[str, Any]]:
        request = service.factory.create("GetAccountsInfoRequest")
        request.CustomerId = customer_id
        request.OnlyParentAccounts = False
        response = _call_operation("GetAccountsInfo", request)
        return _normalize_ms_accounts_payload(
            getattr(response, "AccountsInfo", None) or getattr(response, "AccountInfo", None)
        )

    configured_accounts: List[Dict[str, Any]] = []
    primary_error = ""

    if customer_id_value is not None:
        try:
            configured_accounts = _fetch_accounts_for_customer(customer_id_value)
            if configured_accounts:
                configured_customer_id = str(customer_id_value)
                for account in configured_accounts:
                    if not account.get("customer_id"):
                        account["customer_id"] = configured_customer_id
                return configured_accounts
        except Exception as exc:
            primary_error = ""
            if type(exc).__name__ == "WebFault" and hasattr(exc, "fault") and hasattr(exc.fault, "detail"):
                if hasattr(exc.fault.detail, "AdApiFaultDetail") and hasattr(exc.fault.detail.AdApiFaultDetail, "Errors") and hasattr(exc.fault.detail.AdApiFaultDetail.Errors, "AdApiError"):
                    errors = exc.fault.detail.AdApiFaultDetail.Errors.AdApiError
                    if not isinstance(errors, list):
                        errors = [errors]
                    primary_error = "; ".join([f"{getattr(err, 'Message', '')} (code {getattr(err, 'Code', '')})" for err in errors])
                elif hasattr(exc.fault.detail, "ApiFaultDetail") and hasattr(exc.fault.detail.ApiFaultDetail, "OperationErrors") and hasattr(exc.fault.detail.ApiFaultDetail.OperationErrors, "OperationError"):
                    errors = exc.fault.detail.ApiFaultDetail.OperationErrors.OperationError
                    if not isinstance(errors, list):
                        errors = [errors]
                    primary_error = "; ".join([f"{getattr(err, 'Message', '')} (code {getattr(err, 'Code', '')})" for err in errors])
            if not primary_error:
                primary_error = str(exc).strip() or type(exc).__name__

    try:
        accessible_request = service.factory.create("GetAccessibleCustomerRequest")
        if customer_id_value is not None:
            accessible_request.CustomerId = customer_id_value
        else:
            accessible_request.CustomerId = 0
        accessible_response = _call_operation("GetAccessibleCustomer", accessible_request)
        accessible_customer = getattr(accessible_response, "AccessibleCustomer", None)
        discovered_customer_id = str(getattr(accessible_customer, "Id", "") or "").strip()

        if discovered_customer_id:
            discovered_accounts = _fetch_accounts_for_customer(int(discovered_customer_id))
            if discovered_accounts:
                for account in discovered_accounts:
                    if not account.get("customer_id"):
                        account["customer_id"] = discovered_customer_id
                return discovered_accounts
    except Exception as exc:
        search_error = ""
        if type(exc).__name__ == "WebFault" and hasattr(exc, "fault") and hasattr(exc.fault, "detail"):
            if hasattr(exc.fault.detail, "AdApiFaultDetail") and hasattr(exc.fault.detail.AdApiFaultDetail, "Errors") and hasattr(exc.fault.detail.AdApiFaultDetail.Errors, "AdApiError"):
                errors = exc.fault.detail.AdApiFaultDetail.Errors.AdApiError
                if not isinstance(errors, list):
                    errors = [errors]
                search_error = "; ".join([f"{getattr(err, 'Message', '')} (code {getattr(err, 'Code', '')})" for err in errors])
            elif hasattr(exc.fault.detail, "ApiFaultDetail") and hasattr(exc.fault.detail.ApiFaultDetail, "OperationErrors") and hasattr(exc.fault.detail.ApiFaultDetail.OperationErrors, "OperationError"):
                errors = exc.fault.detail.ApiFaultDetail.OperationErrors.OperationError
                if not isinstance(errors, list):
                    errors = [errors]
                search_error = "; ".join([f"{getattr(err, 'Message', '')} (code {getattr(err, 'Code', '')})" for err in errors])
        if not search_error:
            search_error = str(exc).strip() or type(exc).__name__
        if primary_error:
            primary_error = f"{primary_error}; GetAccessibleCustomer: {search_error}"
        else:
            primary_error = f"GetAccessibleCustomer: {search_error}"

    if customer_id_value is None:
        raise ConnectorError(
            f"Microsoft account discovery failed or returned no accessible customers/accounts: {primary_error or 'no accounts found'}."
        )
    raise ConnectorError(
        f"Microsoft account discovery failed or returned no accounts for the configured customer context: {primary_error or 'no accounts found'}."
    )


async def _discover_microsoft_accounts_soap(access_token: str) -> List[Dict[str, Any]]:
    # Microsoft Ads CustomerManagement SOAP SearchAccounts.
    developer_token = _required_env("MICROSOFT_DEVELOPER_TOKEN")
    endpoint = "https://clientcenter.api.bingads.microsoft.com/Api/CustomerManagement/v13/CustomerManagementService.svc"
    soap_action = "SearchAccounts"

    envelope = f"""<?xml version=\"1.0\" encoding=\"utf-8\"?>
<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\">
  <s:Header xmlns=\"https://bingads.microsoft.com/Customer/v13\">
    <Action mustUnderstand=\"1\" xmlns=\"http://schemas.microsoft.com/ws/2005/05/addressing/none\">{soap_action}</Action>
    <AuthenticationToken i:nil=\"false\" xmlns:i=\"http://www.w3.org/2001/XMLSchema-instance\">{access_token}</AuthenticationToken>
    <DeveloperToken i:nil=\"false\" xmlns:i=\"http://www.w3.org/2001/XMLSchema-instance\">{developer_token}</DeveloperToken>
  </s:Header>
  <s:Body>
    <SearchAccountsRequest xmlns=\"https://bingads.microsoft.com/Customer/v13\">
      <Predicates xmlns:a=\"http://schemas.datacontract.org/2004/07/System.Collections.Generic\" xmlns:i=\"http://www.w3.org/2001/XMLSchema-instance\" i:nil=\"true\"/>
      <Ordering i:nil=\"true\"/>
      <PageInfo>
        <Index>0</Index>
        <Size>100</Size>
      </PageInfo>
    </SearchAccountsRequest>
  </s:Body>
</s:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": soap_action,
    }

    def _extract_ms_fault(xml_text: str) -> str:
        try:
            fault_root = ET.fromstring(xml_text)
        except Exception:
            return ""

        ns = {
            "s": "http://schemas.xmlsoap.org/soap/envelope/",
            "cm": "https://bingads.microsoft.com/Customer/v13",
        }

        fault = fault_root.find(".//s:Fault", ns)
        if fault is None:
            return ""

        fault_string = (fault.findtext("faultstring") or "").strip()
        detail_msg = ""

        # Microsoft often includes rich fault details under ApiFault/OperationErrors.
        op_error = fault.find(".//cm:OperationError", ns)
        if op_error is not None:
            code = (op_error.findtext("cm:Code", default="", namespaces=ns) or "").strip()
            msg = (op_error.findtext("cm:Message", default="", namespaces=ns) or "").strip()
            if code and msg:
                detail_msg = f"{msg} (code {code})"
            elif msg:
                detail_msg = msg

        if detail_msg and fault_string:
            return f"{detail_msg} - {fault_string}"
        if detail_msg:
            return detail_msg
        return fault_string or "Microsoft Ads SOAP fault"

    xml_body = ""
    network_error = ""
    for attempt in range(1, 4):
        try:
            timeout = httpx.Timeout(connect=20.0, read=60.0, write=30.0, pool=20.0)
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=2)
            async with httpx.AsyncClient(timeout=timeout, http2=False, limits=limits) as client:
                response = await client.post(endpoint, headers=headers, content=envelope)
                if response.status_code >= 400:
                    fault_message = _extract_ms_fault(response.text)
                    if fault_message:
                        raise ConnectorError(f"Microsoft Ads account discovery failed: {fault_message}")
                    raise ConnectorError(
                        f"Microsoft Ads account discovery failed with HTTP {response.status_code}. "
                        "Verify MICROSOFT_DEVELOPER_TOKEN and reconnect Microsoft OAuth."
                    )
                xml_body = response.text
                break
        except ConnectorError:
            raise
        except (httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            network_error = f"{type(exc).__name__}: {str(exc).strip() or 'connection dropped by remote host'}"
            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)
                continue
            raise ConnectorError(
                "Microsoft Ads account discovery failed due to network read/connect errors after retries. "
                f"Last error: {network_error}."
            ) from exc

    if not xml_body:
        raise ConnectorError(
            "Microsoft Ads account discovery failed: empty response after retries. "
            f"Last network error: {network_error or 'unknown'}"
        )

    fault_message = _extract_ms_fault(xml_body)
    if fault_message:
        raise ConnectorError(f"Microsoft Ads account discovery failed: {fault_message}")

    try:
        root = ET.fromstring(xml_body)
    except Exception as exc:
        raise ConnectorError("Microsoft Ads account discovery returned malformed XML response.") from exc
    ns = {
        "s": "http://schemas.xmlsoap.org/soap/envelope/",
        "cm": "https://bingads.microsoft.com/Customer/v13",
    }

    accounts = []
    for account in root.findall(".//cm:AdvertiserAccount", ns):
        account_id = account.findtext("cm:Id", default="", namespaces=ns)
        account_name = account.findtext("cm:Name", default="", namespaces=ns)
        status = account.findtext("cm:AccountLifeCycleStatus", default="ACTIVE", namespaces=ns)
        currency = account.findtext("cm:CurrencyCode", default="USD", namespaces=ns)
        parent_customer_id = account.findtext("cm:ParentCustomerId", default="", namespaces=ns)
        if account_id:
            accounts.append({
                "id": str(account_id),
                "name": account_name or f"Microsoft Ads {account_id}",
                "status": status,
                "currency": currency,
                "customer_id": str(parent_customer_id) if parent_customer_id else "",
            })

    if not accounts:
        raise ConnectorError(
            "Microsoft Ads account discovery returned no accessible advertiser accounts for this user. "
            "Confirm this Microsoft user has Ads account access and reconnect if needed."
        )

    return accounts


async def _discover_microsoft_accounts(access_token: str, refresh_token: Optional[str]) -> List[Dict[str, Any]]:
    sdk_error = ""
    try:
        accounts = await asyncio.to_thread(_discover_microsoft_accounts_sdk_sync, access_token, refresh_token)
        if accounts:
            return accounts
    except Exception as exc:
        sdk_error = str(exc).strip() or type(exc).__name__

    try:
        accounts = await _discover_microsoft_accounts_soap(access_token)
        if accounts:
            return accounts
    except Exception as exc:
        soap_error = str(exc).strip() or type(exc).__name__
        if sdk_error:
            raise ConnectorError(
                f"Microsoft Ads account discovery failed via SDK ({sdk_error}) and SOAP fallback ({soap_error})."
            ) from exc
        raise

    if sdk_error:
        raise ConnectorError(
            f"Microsoft Ads account discovery returned no accounts via SDK ({sdk_error}) or SOAP fallback."
        )
    raise ConnectorError("Microsoft Ads account discovery returned no accessible advertiser accounts.")


async def discover_ad_accounts(
    platform: str,
    parent_account_id: str,
    query: str = "",
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    platform_key = (platform or "").strip().lower()

    if platform_key == "meta":
        if not access_token:
            raise ConnectorConfigError("Missing Meta access token for this connection.")
        accounts = await _discover_meta_accounts(access_token)
    elif platform_key == "linkedin":
        if not access_token:
            raise ConnectorConfigError("Missing LinkedIn access token for this connection.")
        accounts = await _discover_linkedin_accounts(access_token)
    elif platform_key == "facebook_organic":
        if not access_token:
            raise ConnectorConfigError("Missing Facebook access token for this connection.")
        accounts = await _discover_facebook_pages(access_token)
    elif platform_key == "instagram_organic":
        if not access_token:
            raise ConnectorConfigError("Missing Instagram access token for this connection.")
        accounts = await _discover_instagram_accounts(access_token)
    elif platform_key == "linkedin_organic":
        if not access_token:
            raise ConnectorConfigError("Missing LinkedIn access token for this connection.")
        accounts = await _discover_linkedin_organizations(access_token)
    elif platform_key == "tiktok":
        if not access_token:
            raise ConnectorConfigError("Missing TikTok access token for this connection.")
        accounts = await _discover_tiktok_accounts(access_token)
    elif platform_key == "google":
        accounts = await _discover_google_accounts(refresh_token)
    elif platform_key == "google_analytics":
        accounts = await _discover_google_analytics_properties(refresh_token)
    elif platform_key == "microsoft":
        if not access_token:
            raise ConnectorConfigError("Missing Microsoft access token for this connection.")
        try:
            accounts = await _discover_microsoft_accounts(access_token, refresh_token)
        except ConnectorError:
            raise
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise ConnectorError(f"Microsoft Ads account discovery failed: {detail}") from exc
    else:
        raise ConnectorConfigError(f"Unsupported platform: {platform_key}")

    # Keep deterministic fallback if provider does not return accounts but a parent account exists.
    if not accounts and parent_account_id:
        accounts = [{
            "id": parent_account_id,
            "name": f"{platform_key.title()} Account {parent_account_id}",
            "status": "ACTIVE",
            "currency": "USD",
        }]

    return _filter_accounts(accounts, query)

def _to_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _to_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except Exception:
        return 0


def _build_dataframe(platform: str, account_id: str, rows: List[Dict[str, Any]]) -> pd.DataFrame:
    # NOTE: organic_reach/organic_engagements/organic_shares/ga_sessions and
    # _is_mock_data used to be silently dropped here — every connector's
    # output funnels through this normalizer, but it only ever kept the
    # paid-ads column set. That meant organic metrics never reached a
    # report at all, for any organic connector, mock or real. Fixed by
    # passing these through (defaulting to 0/False for platforms that don't
    # produce them, matching etl.py's UNIVERSAL_COLUMNS fill-with-0 pattern).
    required_cols = [
        "date", "platform", "campaign", "ad_group", "ad_asset",
        "spend", "impressions", "clicks", "conversions", "revenue", "source_account_id",
        "organic_reach", "organic_engagements", "organic_shares", "ga_sessions",
        "_is_mock_data",
    ]
    if not rows:
        return pd.DataFrame(columns=required_cols)

    normalized = []
    for row in rows:
        normalized.append({
            "date": str(row.get("date", "")),
            "platform": platform,
            "campaign": str(row.get("campaign", "Unknown Campaign")),
            "ad_group": str(row.get("ad_group", "Unknown Group")),
            "ad_asset": str(row.get("ad_asset", "Unknown Ad")),
            "spend": _to_float(row.get("spend")),
            "impressions": _to_int(row.get("impressions")),
            "clicks": _to_int(row.get("clicks")),
            "conversions": _to_float(row.get("conversions")),
            "revenue": _to_float(row.get("revenue")),
            "source_account_id": account_id,
            "organic_reach": _to_int(row.get("organic_reach")),
            "organic_engagements": _to_int(row.get("organic_engagements")),
            "organic_shares": _to_int(row.get("organic_shares")),
            "ga_sessions": _to_int(row.get("ga_sessions")),
            "_is_mock_data": bool(row.get("_is_mock_data", False)),
        })

    return pd.DataFrame(normalized, columns=required_cols)


async def _fetch_google_performance(
    account_id: str,
    refresh_token: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    developer_token = _required_env("GOOGLE_ADS_DEVELOPER_TOKEN")
    client_id = _required_env("GOOGLE_ADS_CLIENT_ID")
    client_secret = _required_env("GOOGLE_ADS_CLIENT_SECRET")
    final_refresh_token = str(refresh_token or os.getenv("GOOGLE_ADS_REFRESH_TOKEN", "")).strip()
    if not final_refresh_token:
        raise ConnectorConfigError("Missing Google Ads refresh token for this connection.")

    credentials = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": final_refresh_token,
        "use_proto_plus": True,
    }
    login_customer_id = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").strip()
    if login_customer_id:
        credentials["login_customer_id"] = _strip_google_customer_id(login_customer_id)

    customer_id = _strip_google_customer_id(account_id)
    if not customer_id:
        raise ConnectorConfigError(f"Invalid Google account/customer ID: {account_id}")

    window_start, window_end = _resolve_sync_window(start_date, end_date)

    def _run_query() -> List[Dict[str, Any]]:
        client = GoogleAdsClient.load_from_dict(credentials)
        service = client.get_service("GoogleAdsService")
        query = """
            SELECT
              segments.date,
              campaign.name,
              ad_group.name,
              metrics.cost_micros,
              metrics.impressions,
              metrics.clicks,
              metrics.conversions,
              metrics.conversions_value
            FROM ad_group
            WHERE segments.date BETWEEN '{start}' AND '{end}'
                """.format(start=window_start.isoformat(), end=window_end.isoformat())
        records: List[Dict[str, Any]] = []
        for batch in service.search_stream(customer_id=customer_id, query=query):
            for row in batch.results:
                records.append({
                    "date": str(row.segments.date),
                    "campaign": row.campaign.name or "Unknown Campaign",
                    "ad_group": row.ad_group.name or "Unknown Ad Group",
                    "ad_asset": "N/A",
                    "spend": float(row.metrics.cost_micros or 0) / 1_000_000,
                    "impressions": int(row.metrics.impressions or 0),
                    "clicks": int(row.metrics.clicks or 0),
                    "conversions": float(row.metrics.conversions or 0),
                    "revenue": float(row.metrics.conversions_value or 0),
                })
        return records

    rows = await asyncio.to_thread(_run_query)
    return _build_dataframe("google", account_id, rows)


def _google_analytics_credentials(refresh_token: Optional[str]):
    """Build google.oauth2.credentials.Credentials for the GA4 Data/Admin
    API clients. Reuses the same Google Ads OAuth client (see
    app/api/oauth.py `_platform_client_id`) — one Google Cloud OAuth client
    can hold multiple incrementally-granted scopes."""
    from google.oauth2.credentials import Credentials

    client_id = _required_env("GOOGLE_ADS_CLIENT_ID")
    client_secret = _required_env("GOOGLE_ADS_CLIENT_SECRET")
    final_refresh_token = (refresh_token or "").strip()
    if not final_refresh_token:
        raise ConnectorConfigError("Missing Google Analytics refresh token for this connection.")

    return Credentials(
        token=None,
        refresh_token=final_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )


# GA4's channel grouping buckets all traffic (paid, organic, direct,
# referral, etc.) into one dimension. This app's Universal Schema treats
# GA4 as an organic-only source (see docs/organic-and-ga4-integration-plan.md
# Part B4) — filtering to these channel groups is what keeps GA4 sessions
# out of the "paid" bucket alongside Google/Meta/LinkedIn/TikTok Ads, which
# would otherwise double-count traffic those platforms already report spend
# for. Revisit this filter if GA4 is ever used for all-traffic reporting
# instead.
GA4_ORGANIC_CHANNEL_GROUPS = [
    "Organic Search", "Organic Social", "Organic Video", "Organic Shopping",
]


async def _fetch_google_analytics(
    property_id: str,
    refresh_token: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest, FilterExpression, Filter,
    )

    credentials = _google_analytics_credentials(refresh_token)
    window_start, window_end = _resolve_sync_window(start_date, end_date)
    prop = property_id if str(property_id).startswith("properties/") else f"properties/{property_id}"

    def _run_report() -> List[Dict[str, Any]]:
        client = BetaAnalyticsDataClient(credentials=credentials)
        request = RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date=window_start.isoformat(), end_date=window_end.isoformat())],
            dimensions=[Dimension(name="date"), Dimension(name="sessionDefaultChannelGroup")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="engagedSessions"),
                Metric(name="conversions"),
                Metric(name="totalRevenue"),
            ],
            dimension_filter=FilterExpression(
                filter=Filter(
                    field_name="sessionDefaultChannelGroup",
                    in_list_filter=Filter.InListFilter(values=GA4_ORGANIC_CHANNEL_GROUPS),
                )
            ),
        )
        response = client.run_report(request)
        records: List[Dict[str, Any]] = []
        for row in response.rows:
            raw_date = row.dimension_values[0].value  # YYYYMMDD
            date_str = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else raw_date
            channel_group = row.dimension_values[1].value
            sessions = int(float(row.metric_values[0].value or 0))
            engaged_sessions = int(float(row.metric_values[1].value or 0))
            conversions = float(row.metric_values[2].value or 0)
            revenue = float(row.metric_values[3].value or 0)
            records.append({
                "date": date_str,
                "campaign": channel_group,  # No campaign concept for organic GA4 traffic — group by channel instead.
                "ad_group": "N/A",
                "ad_asset": "N/A",
                "spend": 0.0,  # GA4 has no spend concept; that lives in each platform's own Ads connector.
                "impressions": 0,  # GA4 has no impressions concept for organic sessions.
                "clicks": engaged_sessions,
                "conversions": conversions,
                "revenue": revenue,
                "organic_reach": sessions,
                "organic_engagements": engaged_sessions,
                "organic_shares": 0,  # Not tracked by GA4.
                "ga_sessions": sessions,
            })
        return records

    rows = await asyncio.to_thread(_run_report)
    return _build_dataframe("google_analytics", property_id, rows)


def _action_matches(action_type: str, accepted: List[str]) -> bool:
    for target in accepted:
        normalized = (target or "").strip().lower()
        if not normalized:
            continue
        if normalized.endswith("*"):
            if action_type.startswith(normalized[:-1]):
                return True
            continue
        if action_type == normalized:
            return True
    return False


def _sum_actions(actions: List[Dict[str, Any]], accepted: List[str]) -> float:
    total = 0.0
    accepted_lower = [a.lower() for a in accepted]
    for action in actions or []:
        action_type = str(action.get("action_type", "")).lower()
        if _action_matches(action_type, accepted_lower):
            total += _to_float(action.get("value"))
    return total


async def _fetch_meta_performance(
    account_id: str,
    access_token: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    if not access_token:
        raise ConnectorConfigError("Missing Meta access token for this connection.")

    window_start, window_end = _resolve_sync_window(start_date, end_date)

    act_id = account_id if str(account_id).startswith("act_") else f"act_{account_id}"
    base_url = f"https://graph.facebook.com/v20.0/{act_id}/insights"
    headers = {
        "Authorization": f"Bearer {access_token}",
    }
    appsecret_proof = _meta_appsecret_proof(access_token)
    params = {
        "fields": "date_start,campaign_name,adset_name,ad_name,spend,impressions,clicks,actions,action_values",
        "level": "ad",
        "time_increment": "1",
        "time_range": json.dumps({
            "since": window_start.isoformat(),
            "until": window_end.isoformat(),
        }),
        "limit": 500,
        "appsecret_proof": appsecret_proof,
    }

    conversion_action_types = _env_csv("META_CONVERSION_ACTION_TYPES") or DEFAULT_META_CONVERSION_ACTION_TYPES
    revenue_action_types = _env_csv("META_REVENUE_ACTION_TYPES") or DEFAULT_META_REVENUE_ACTION_TYPES

    rows: List[Dict[str, Any]] = []
    after_cursor: Optional[str] = None
    async with httpx.AsyncClient(timeout=45.0) as client:
        while True:
            request_params: Dict[str, Any] = dict(params)
            if after_cursor:
                request_params["after"] = after_cursor

            resp = await client.get(base_url, headers=headers, params=request_params)
            resp.raise_for_status()
            payload = resp.json()
            for rec in payload.get("data", []):
                actions = rec.get("actions", [])
                action_values = rec.get("action_values", [])
                rows.append({
                    "date": rec.get("date_start"),
                    "campaign": rec.get("campaign_name") or "Unknown Campaign",
                    "ad_group": rec.get("adset_name") or "Unknown Group",
                    "ad_asset": rec.get("ad_name") or "Unknown Ad",
                    "spend": _to_float(rec.get("spend")),
                    "impressions": _to_int(rec.get("impressions")),
                    "clicks": _to_int(rec.get("clicks")),
                    "conversions": _sum_actions(actions, conversion_action_types),
                    "revenue": _sum_actions(action_values, revenue_action_types),
                })

            after_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after_cursor:
                break

    return _build_dataframe("meta", account_id, rows)


async def _fetch_linkedin_performance(
    account_id: str,
    access_token: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    if not access_token:
        raise ConnectorConfigError("Missing LinkedIn access token for this connection.")

    window_start, window_end = _resolve_sync_window(start_date, end_date)
    
    # LinkedIn adAnalytics dateRange format can vary by API version/account.
    # Try multiple encodings before failing.
    account_urn = f"urn:li:sponsoredAccount:{account_id}"
    accounts_param = f"List({_linkedin_encode_urn(account_urn)})"
    date_range_param = (
        "(start:(year:{sy},month:{sm},day:{sd}),"
        "end:(year:{ey},month:{em},day:{ed}))"
    ).format(
        sy=window_start.year,
        sm=window_start.month,
        sd=window_start.day,
        ey=window_end.year,
        em=window_end.month,
        ed=window_end.day,
    )
    
    query = (
        "q=analytics"
        f"&dateRange={date_range_param}"
        "&timeGranularity=DAILY"
        f"&accounts={accounts_param}"
        "&pivot=CAMPAIGN"
        "&fields=dateRange,pivotValues,costInLocalCurrency,impressions,clicks,externalWebsiteConversions"
    )

    url = "https://api.linkedin.com/rest/adAnalytics"
    payload: Dict[str, Any] = {}
    last_error = "LinkedIn analytics fetch failed"

    async with httpx.AsyncClient(timeout=45.0) as client:
        for version in _linkedin_version_candidates():
            headers = _linkedin_headers(access_token, version)
            resp = await client.get(f"{url}?{query}", headers=headers)
            if _linkedin_version_unsupported(resp):
                last_error = f"LinkedIn API version {version} is no longer supported"
                continue
            if resp.status_code >= 400:
                message = _parse_linkedin_error(resp, "LinkedIn analytics fetch failed")
                raise ConnectorError(f"{message} (code {resp.status_code})")
            payload = resp.json()
            if payload:
                break

    if not payload:
        raise ConnectorError(last_error)

    rows = []
    for rec in payload.get("elements", []):
        date_range = rec.get("dateRange", {})
        end_date_parts: Dict[str, Any] = date_range.get("end", {}) if isinstance(date_range, dict) else {}
        date_str = f"{end_date_parts.get('year', window_end.year):04d}-{end_date_parts.get('month', window_end.month):02d}-{end_date_parts.get('day', window_end.day):02d}"
        pivot_values = rec.get("pivotValues", [])
        campaign_urn = str(pivot_values[0] if isinstance(pivot_values, list) and pivot_values else rec.get("campaign", ""))
        rows.append({
            "date": date_str,
            "campaign": campaign_urn.replace("urn:li:sponsoredCampaign:", "") or "LinkedIn Campaign",
            "ad_group": "LinkedIn Group",
            "ad_asset": "LinkedIn Ad",
            "spend": _to_float(rec.get("costInLocalCurrency", rec.get("costInUsd", 0))),
            "impressions": _to_int(rec.get("impressions", 0)),
            "clicks": _to_int(rec.get("clicks", 0)),
            "conversions": _to_float(rec.get("externalWebsiteConversions", rec.get("conversions", 0))),
            "revenue": _to_float(rec.get("revenueInLocalCurrency", rec.get("revenueInUsd", 0))),
        })

    return _build_dataframe("linkedin", account_id, rows)


async def _fetch_tiktok_performance(
    account_id: str,
    access_token: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    if not access_token:
        raise ConnectorConfigError("Missing TikTok access token for this connection.")

    window_start, window_end = _resolve_sync_window(start_date, end_date)

    url = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
    headers = {
        "Access-Token": access_token,
        "Content-Type": "application/json",
    }
    body = {
        "advertiser_id": str(account_id),
        "report_type": "BASIC",
        "data_level": "AUCTION_AD",
        "dimensions": ["stat_time_day", "campaign_name", "adgroup_name", "ad_name"],
        "metrics": ["spend", "impressions", "clicks", "conversion", "total_purchase_value"],
        "start_date": window_start.isoformat(),
        "end_date": window_end.isoformat(),
        "page": 1,
        "page_size": 1000,
    }

    rows = []
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        payload = resp.json()

    if payload.get("code") not in (0, "0"):
        raise ConnectorError(f"TikTok reporting failed: {payload.get('message', 'unknown error')}")

    for rec in payload.get("data", {}).get("list", []):
        dimensions = rec.get("dimensions", {})
        metrics = rec.get("metrics", {})
        rows.append({
            "date": dimensions.get("stat_time_day"),
            "campaign": dimensions.get("campaign_name") or "TikTok Campaign",
            "ad_group": dimensions.get("adgroup_name") or "TikTok Group",
            "ad_asset": dimensions.get("ad_name") or "TikTok Ad",
            "spend": _to_float(metrics.get("spend", 0)),
            "impressions": _to_int(metrics.get("impressions", 0)),
            "clicks": _to_int(metrics.get("clicks", 0)),
            "conversions": _to_float(metrics.get("conversion", 0)),
            "revenue": _to_float(metrics.get("total_purchase_value", 0)),
        })

    return _build_dataframe("tiktok", account_id, rows)


async def _fetch_microsoft_performance(account_id: str, access_token: str) -> pd.DataFrame:
    return _build_dataframe("microsoft", account_id, [])


def _normalize_ms_date(raw: str) -> str:
    value = (raw or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return value


def _read_ms_report_rows(path: str) -> List[Dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "TimePeriod" in line and "CampaignName" in line:
            header_idx = i
            break
    if header_idx is None:
        return []

    records: List[Dict[str, Any]] = []
    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        time_period = (row.get("TimePeriod") or "").strip()
        campaign = (row.get("CampaignName") or "").strip()
        if not time_period or not campaign:
            continue
        records.append({
            "date": _normalize_ms_date(time_period),
            "campaign": campaign,
            "ad_group": "Microsoft Ad Group",
            "ad_asset": "Microsoft Ad",
            "spend": _to_float((row.get("Spend") or "").replace(",", "")),
            "impressions": _to_int((row.get("Impressions") or "").replace(",", "")),
            "clicks": _to_int((row.get("Clicks") or "").replace(",", "")),
            "conversions": _to_float((row.get("Conversions") or row.get("AllConversions") or "0").replace(",", "")),
            "revenue": _to_float((row.get("Revenue") or row.get("AllRevenue") or "0").replace(",", "")),
        })
    return records


def _extract_microsoft_fault_detail(exc: Exception) -> str:
    tracking_id = ""
    messages: List[str] = []

    fault = getattr(exc, "fault", None)
    if fault is not None:
        tracking_id = str(getattr(fault, "trackingId", "") or getattr(fault, "TrackingId", "") or "").strip()
        detail = getattr(fault, "detail", None)
        if detail is not None:
            for detail_name, collection_name, item_name in [
                ("AdApiFaultDetail", "Errors", "AdApiError"),
                ("ApiFaultDetail", "OperationErrors", "OperationError"),
                ("ApiFaultDetail", "BatchErrors", "BatchError"),
            ]:
                detail_obj = getattr(detail, detail_name, None)
                if detail_obj is None:
                    continue
                tracking_id = tracking_id or str(getattr(detail_obj, "TrackingId", "") or "").strip()
                error_collection = getattr(detail_obj, collection_name, None)
                if error_collection is None:
                    continue
                errors = getattr(error_collection, item_name, None)
                if errors is None:
                    continue
                if not isinstance(errors, list):
                    errors = [errors]
                for err in errors:
                    code = str(getattr(err, "Code", "") or "").strip()
                    message = str(getattr(err, "Message", "") or "").strip()
                    if code and message:
                        messages.append(f"{message} (code {code})")
                    elif message:
                        messages.append(message)
                    elif code:
                        messages.append(f"Error code {code}")

    if not messages:
        raw_message = str(exc).strip()
        if raw_message:
            messages.append(raw_message)

    detail_message = "; ".join(dict.fromkeys(msg for msg in messages if msg))
    if tracking_id:
        return f"{detail_message} TrackingId: {tracking_id}.".strip()
    return detail_message or type(exc).__name__


def _create_microsoft_reporting_array(service: Any, type_names: List[str], item_attr: str, values: List[Any]) -> Any:
    for type_name in type_names:
        try:
            arr = service.factory.create(type_name)
        except Exception:
            continue

        target = getattr(arr, item_attr, None)
        if target is None:
            continue

        for value in values:
            target.append(value)
        return arr

    return {item_attr: values}


def _fetch_microsoft_performance_sync(
    account_id: str,
    access_token: str,
    refresh_token: Optional[str],
    microsoft_customer_id: Optional[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    if not access_token:
        raise ConnectorConfigError("Missing Microsoft access token for this connection.")

    developer_token = _required_env("MICROSOFT_DEVELOPER_TOKEN")
    client_id = _required_env("MICROSOFT_CLIENT_ID")
    customer_id = (microsoft_customer_id or "").strip() or os.getenv("MICROSOFT_CUSTOMER_ID", "").strip()
    if not customer_id:
        raise ConnectorConfigError(
            "Missing Microsoft customer ID for this account. Re-discover the Microsoft ad accounts "
            "and re-save the account selection, or set MICROSOFT_CUSTOMER_ID if you intend to use a single customer context."
        )

    try:
        account_id_int = int(str(account_id).strip())
        customer_id_int = int(customer_id)
    except Exception as exc:
        raise ConnectorConfigError(f"Invalid Microsoft account/customer ID configuration: {exc}")

    window_start, window_end = _resolve_sync_window(start_date, end_date)

    oauth = _build_microsoft_oauth(client_id, access_token, refresh_token)
    _refresh_microsoft_oauth_if_possible(oauth, refresh_token)

    auth_data = AuthorizationData(
        account_id=account_id_int,
        customer_id=customer_id_int,
        developer_token=developer_token,
        authentication=oauth,
    )

    manager = ReportingServiceManager(
        authorization_data=auth_data,
        poll_interval_in_milliseconds=5000,
        environment="production",
    )
    service = manager.service_client

    request = service.factory.create("CampaignPerformanceReportRequest")
    request.Aggregation = "Daily"
    request.ExcludeColumnHeaders = False
    request.ExcludeReportFooter = True
    request.ExcludeReportHeader = True
    request.Format = "Csv"
    request.FormatVersion = "2.0"
    request.ReportName = f"Campaign Performance Report {account_id}"
    request.ReturnOnlyCompleteData = False

    scope = service.factory.create("AccountThroughCampaignReportScope")
    scope.AccountIds = _create_microsoft_reporting_array(
        service,
        ["ArrayOflong", "ArrayOfLong"],
        "long",
        [account_id_int],
    )
    scope.Campaigns = None
    request.Scope = scope

    report_time = service.factory.create("ReportTime")
    report_time.ReportTimeZone = "PacificTimeUSCanadaTijuana"
    report_time.PredefinedTime = None
    custom_start = service.factory.create("Date")
    custom_start.Day = window_start.day
    custom_start.Month = window_start.month
    custom_start.Year = window_start.year
    custom_end = service.factory.create("Date")
    custom_end.Day = window_end.day
    custom_end.Month = window_end.month
    custom_end.Year = window_end.year
    report_time.CustomDateRangeStart = custom_start
    report_time.CustomDateRangeEnd = custom_end
    request.Time = report_time

    columns = _create_microsoft_reporting_array(
        service,
        ["ArrayOfCampaignPerformanceReportColumn"],
        "CampaignPerformanceReportColumn",
        [
        "TimePeriod",
        "CampaignName",
        "Impressions",
        "Clicks",
        "Spend",
        "Conversions",
        "Revenue",
        ],
    )
    request.Columns = columns

    out_dir = tempfile.gettempdir()
    filename = f"msads-campaign-performance-{account_id}.csv"
    params = ReportingDownloadParameters(
        report_request=request,
        result_file_directory=out_dir,
        result_file_name=filename,
        overwrite_result_file=True,
        timeout_in_milliseconds=120000,
    )
    try:
        report_path = manager.download_file(params)
    except Exception as exc:
        detail = _extract_microsoft_fault_detail(exc)
        auth_expired = ("code 109" in detail.lower()) or ("authentication token expired" in detail.lower())
        if auth_expired and _refresh_microsoft_oauth_if_possible(oauth, refresh_token):
            try:
                report_path = manager.download_file(params)
            except Exception as retry_exc:
                retry_detail = _extract_microsoft_fault_detail(retry_exc)
                raise ConnectorError(
                    "Microsoft reporting request failed after token refresh for account "
                    f"{account_id_int} under customer {customer_id_int}: {retry_detail}"
                ) from retry_exc
        else:
            raise ConnectorError(
                f"Microsoft reporting request failed for account {account_id_int} under customer {customer_id_int}: {detail}"
            ) from exc
    if not report_path:
        raise ConnectorError("Microsoft reporting returned no downloadable file.")

    rows = _read_ms_report_rows(report_path)
    return _build_dataframe("microsoft", str(account_id_int), rows)


async def fetch_platform_data(
    platform: str,
    account_id: str,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    microsoft_customer_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    platform_key = (platform or "").strip().lower()
    if platform_key == "google":
        return await _fetch_google_performance(account_id, refresh_token, start_date, end_date)
    if platform_key == "google_analytics":
        return await _fetch_google_analytics(account_id, refresh_token, start_date, end_date)
    if platform_key == "meta":
        return await _fetch_meta_performance(account_id, access_token or "", start_date, end_date)
    if platform_key == "linkedin":
        return await _fetch_linkedin_performance(account_id, access_token or "", start_date, end_date)
    if platform_key == "tiktok":
        return await _fetch_tiktok_performance(account_id, access_token or "", start_date, end_date)
    if platform_key == "microsoft":
        return await asyncio.to_thread(
            _fetch_microsoft_performance_sync,
            account_id,
            access_token or "",
            refresh_token,
            microsoft_customer_id,
            start_date,
            end_date,
        )
    if platform_key == "facebook_organic":
        return await _fetch_facebook_organic(account_id, access_token or "", start_date, end_date)
    if platform_key == "instagram_organic":
        return await _fetch_instagram_organic(account_id, access_token or "", start_date, end_date)
    if platform_key == "linkedin_organic":
        return await _fetch_linkedin_organic(account_id, access_token or "", start_date, end_date)
    raise ConnectorConfigError(f"Unsupported platform: {platform_key}")


# ── Organic connectors ───────────────────────────────────────────────────────
#
# Real API calls below follow documented Meta Graph API / LinkedIn Marketing
# API shapes as of this writing. Both platforms version and reshape these
# endpoints periodically — verify against current provider docs before
# relying on this in production, and watch for HTTP 4xx responses citing a
# deprecated field/metric name. Falls back to fabricated demo data ONLY when
# there's no access token AND _demo_mode_allowed() explicitly permits it —
# never silently, never in production. A real token with a real API failure
# raises ConnectorError; it does not silently degrade to fake numbers.

async def _get_meta_page_access_token(page_id: str, user_access_token: str) -> str:
    """Page-level API calls (feed, insights) require a Page access token,
    not the user's own token. Exchange for one via the Page's own /me-style
    lookup, using the same appsecret_proof scheme as the rest of the Meta
    connector."""
    url = f"https://graph.facebook.com/v20.0/{page_id}"
    params = {
        "fields": "access_token",
        "appsecret_proof": _meta_appsecret_proof(user_access_token),
    }
    headers = {"Authorization": f"Bearer {user_access_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(f"Could not resolve Page access token for {page_id}: {exc}") from exc
    page_token = resp.json().get("access_token")
    if not page_token:
        raise ConnectorError(
            f"Page {page_id} did not return an access_token — the connected "
            "user may no longer be an admin of this Page, or the "
            "pages_show_list/pages_read_engagement scopes were revoked."
        )
    return page_token


async def _discover_facebook_pages(access_token: str) -> List[Dict[str, Any]]:
    """List Facebook Pages the connected user administers. Used for both
    Facebook Organic and (as the parent lookup) Instagram Organic."""
    url = "https://graph.facebook.com/v20.0/me/accounts"
    headers = {"Authorization": f"Bearer {access_token}"}
    appsecret_proof = _meta_appsecret_proof(access_token)
    params = {
        "fields": "id,name,category",
        "limit": 200,
        "appsecret_proof": appsecret_proof,
    }
    accounts: List[Dict[str, Any]] = []
    after_cursor: Optional[str] = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            request_params: Dict[str, Any] = dict(params)
            if after_cursor:
                request_params["after"] = after_cursor
            response = await client.get(url, headers=headers, params=request_params)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ConnectorError(f"Facebook Page discovery failed: {exc}") from exc
            payload = response.json()
            for row in payload.get("data", []):
                accounts.append({
                    "id": str(row.get("id", "")),
                    "name": row.get("name") or f"Page {row.get('id')}",
                    "status": "ACTIVE",
                    "currency": "N/A",  # Pages don't have a currency; kept for shape parity with ad accounts.
                })
            after_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after_cursor:
                break
    return accounts


async def _discover_instagram_accounts(access_token: str) -> List[Dict[str, Any]]:
    """Instagram Business accounts are discovered via their linked Facebook
    Page, not directly — list Pages, then resolve each Page's
    instagram_business_account."""
    pages = await _discover_facebook_pages(access_token)
    accounts: List[Dict[str, Any]] = []
    appsecret_proof = _meta_appsecret_proof(access_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in pages:
            url = f"https://graph.facebook.com/v20.0/{page['id']}"
            params = {"fields": "instagram_business_account{id,username}", "appsecret_proof": appsecret_proof}
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                continue  # Page has no linked IG account, or lacks permission — skip, don't fail discovery.
            ig = resp.json().get("instagram_business_account")
            if ig and ig.get("id"):
                accounts.append({
                    "id": str(ig["id"]),
                    "name": ig.get("username") or f"Instagram {ig['id']}",
                    "status": "ACTIVE",
                    "currency": "N/A",
                    # Instagram Insights calls need the *Page's* access token, keep the
                    # linkage so the fetch function doesn't need to re-discover it.
                    "_parent_page_id": page["id"],
                })
    return accounts


async def _fetch_facebook_organic(account_id: str, access_token: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    if not access_token or access_token.strip() == "":
        if not _demo_mode_allowed():
            raise ConnectorConfigError(
                "Missing Facebook Organic access token. Reconnect the Page, or set "
                "DEMO_MODE=1 in a non-production environment to use demo data."
            )
        return _generate_mock_organic_data("facebook_organic", account_id, start_date, end_date)

    window_start, window_end = _resolve_sync_window(start_date, end_date)
    page_token = await _get_meta_page_access_token(account_id, access_token)
    page_appsecret_proof = _meta_appsecret_proof(page_token)

    rows: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=45.0) as client:
        feed_url = f"https://graph.facebook.com/v20.0/{account_id}/feed"
        feed_params = {
            "fields": "id,message,created_time",
            "since": int(datetime.combine(window_start, datetime.min.time()).timestamp()),
            "until": int(datetime.combine(window_end, datetime.min.time()).timestamp()) + 86400,
            "limit": 100,
            "access_token": page_token,
            "appsecret_proof": page_appsecret_proof,
        }
        after_cursor: Optional[str] = None
        while True:
            request_params: Dict[str, Any] = dict(feed_params)
            if after_cursor:
                request_params["after"] = after_cursor
            resp = await client.get(feed_url, params=request_params)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ConnectorError(f"Facebook Organic feed fetch failed: {exc}") from exc
            payload = resp.json()
            posts = payload.get("data", [])

            for post in posts:
                post_id = post.get("id")
                if not post_id:
                    continue
                insights_url = f"https://graph.facebook.com/v20.0/{post_id}/insights"
                insights_params = {
                    "metric": "post_impressions_unique,post_engaged_users,post_clicks",
                    "access_token": page_token,
                    "appsecret_proof": page_appsecret_proof,
                }
                insights_resp = await client.get(insights_url, params=insights_params)
                metrics: Dict[str, int] = {}
                if insights_resp.status_code < 400:
                    for m in insights_resp.json().get("data", []):
                        values = m.get("values", [])
                        metrics[m.get("name", "")] = _to_int(values[0].get("value")) if values else 0
                # else: insights can 400 for posts with too little engagement to
                # report on, or non-organic (boosted) posts — treat as zero rather
                # than failing the whole sync.

                shares_resp = await client.get(
                    f"https://graph.facebook.com/v20.0/{post_id}",
                    params={"fields": "shares", "access_token": page_token, "appsecret_proof": page_appsecret_proof},
                )
                share_count = 0
                if shares_resp.status_code < 400:
                    share_count = _to_int(shares_resp.json().get("shares", {}).get("count"))

                created = str(post.get("created_time", ""))[:10]
                rows.append({
                    "date": created,
                    "campaign": (post.get("message") or "Untitled post")[:120],
                    "ad_group": "N/A",
                    "ad_asset": "N/A",
                    "spend": 0.0,
                    "impressions": metrics.get("post_impressions_unique", 0),
                    "clicks": metrics.get("post_clicks", 0),
                    "conversions": 0,
                    "revenue": 0.0,
                    "organic_reach": metrics.get("post_impressions_unique", 0),
                    "organic_engagements": metrics.get("post_engaged_users", 0),
                    "organic_shares": share_count,
                    "ga_sessions": 0,
                })

            after_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after_cursor:
                break

    return _build_dataframe("facebook_organic", account_id, rows)


async def _fetch_instagram_organic(account_id: str, access_token: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    if not access_token or access_token.strip() == "":
        if not _demo_mode_allowed():
            raise ConnectorConfigError(
                "Missing Instagram Organic access token. Reconnect the account, or set "
                "DEMO_MODE=1 in a non-production environment to use demo data."
            )
        return _generate_mock_organic_data("instagram_organic", account_id, start_date, end_date)

    # account_id here is the IG Business Account id (see _discover_instagram_accounts).
    # Resolving the linked Page's access token requires re-discovering the
    # parent Page — account selection doesn't currently persist the
    # `_parent_page_id` hint past the discovery response, so callers that
    # cache `available_accounts` (Connection.available_accounts) should read
    # it from there instead of re-running discovery on every sync.
    window_start, window_end = _resolve_sync_window(start_date, end_date)

    pages = await _discover_facebook_pages(access_token)
    parent_page_id: Optional[str] = None
    appsecret_proof = _meta_appsecret_proof(access_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in pages:
            resp = await client.get(
                f"https://graph.facebook.com/v20.0/{page['id']}",
                headers=headers,
                params={"fields": "instagram_business_account", "appsecret_proof": appsecret_proof},
            )
            if resp.status_code >= 400:
                continue
            ig = resp.json().get("instagram_business_account") or {}
            if str(ig.get("id", "")) == str(account_id):
                parent_page_id = page["id"]
                break

    if not parent_page_id:
        raise ConnectorError(
            f"Could not find a Facebook Page linked to Instagram account {account_id} — "
            "it may have been unlinked, or permissions were revoked."
        )

    page_token = await _get_meta_page_access_token(parent_page_id, access_token)
    page_appsecret_proof = _meta_appsecret_proof(page_token)

    rows: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=45.0) as client:
        media_url = f"https://graph.facebook.com/v20.0/{account_id}/media"
        media_params = {
            "fields": "id,caption,timestamp",
            "since": int(datetime.combine(window_start, datetime.min.time()).timestamp()),
            "until": int(datetime.combine(window_end, datetime.min.time()).timestamp()) + 86400,
            "limit": 100,
            "access_token": page_token,
            "appsecret_proof": page_appsecret_proof,
        }
        after_cursor: Optional[str] = None
        while True:
            request_params: Dict[str, Any] = dict(media_params)
            if after_cursor:
                request_params["after"] = after_cursor
            resp = await client.get(media_url, params=request_params)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ConnectorError(f"Instagram Organic media fetch failed: {exc}") from exc
            payload = resp.json()
            media_items = payload.get("data", [])

            for media in media_items:
                media_id = media.get("id")
                if not media_id:
                    continue
                insights_url = f"https://graph.facebook.com/v20.0/{media_id}/insights"
                insights_params = {
                    # `impressions` was deprecated for IG API v22+ in favor of `views`
                    # for some media types — verify current metric availability
                    # against Meta's changelog before relying on this in production.
                    "metric": "reach,engagement,saved,shares",
                    "access_token": page_token,
                    "appsecret_proof": page_appsecret_proof,
                }
                insights_resp = await client.get(insights_url, params=insights_params)
                metrics: Dict[str, int] = {}
                if insights_resp.status_code < 400:
                    for m in insights_resp.json().get("data", []):
                        values = m.get("values", [])
                        metrics[m.get("name", "")] = _to_int(values[0].get("value")) if values else 0

                created = str(media.get("timestamp", ""))[:10]
                rows.append({
                    "date": created,
                    "campaign": (media.get("caption") or "Untitled post")[:120],
                    "ad_group": "N/A",
                    "ad_asset": "N/A",
                    "spend": 0.0,
                    "impressions": metrics.get("reach", 0),
                    "clicks": 0,  # IG has no direct link-click metric on organic posts.
                    "conversions": 0,
                    "revenue": 0.0,
                    "organic_reach": metrics.get("reach", 0),
                    "organic_engagements": metrics.get("engagement", 0),
                    "organic_shares": metrics.get("shares", 0),
                    "ga_sessions": 0,
                })

            after_cursor = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after_cursor:
                break

    return _build_dataframe("instagram_organic", account_id, rows)


async def _discover_linkedin_organizations(access_token: str) -> List[Dict[str, Any]]:
    """List LinkedIn Organization Pages the connected user administers."""
    url = "https://api.linkedin.com/rest/organizationAcls"
    params = {"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"}
    payload: Dict[str, Any] = {}
    last_error = "LinkedIn organization discovery failed"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for version in _linkedin_version_candidates():
            headers = _linkedin_headers(access_token, version)
            response = await client.get(url, headers=headers, params=params)
            if _linkedin_version_unsupported(response):
                last_error = f"LinkedIn API version {version} is no longer supported"
                continue
            if response.status_code >= 400:
                message = _parse_linkedin_error(response, "LinkedIn organization discovery failed")
                raise ConnectorError(f"{message} (code {response.status_code})")
            payload = response.json()
            break

        if not payload:
            raise ConnectorError(last_error)

        org_ids = []
        for row in payload.get("elements", []):
            urn = str(row.get("organization", ""))
            org_id = urn.replace("urn:li:organization:", "")
            if org_id:
                org_ids.append(org_id)

        accounts: List[Dict[str, Any]] = []
        for org_id in org_ids:
            name = f"LinkedIn Organization {org_id}"
            for version in _linkedin_version_candidates():
                headers = _linkedin_headers(access_token, version)
                resp = await client.get(
                    f"https://api.linkedin.com/rest/organizations/{org_id}",
                    headers=headers,
                    params={"projection": "(id,localizedName)"},
                )
                if _linkedin_version_unsupported(resp):
                    continue
                if resp.status_code < 400:
                    name = resp.json().get("localizedName") or name
                break
            accounts.append({"id": org_id, "name": name, "status": "ACTIVE", "currency": "N/A"})
    return accounts


async def _fetch_linkedin_organic(account_id: str, access_token: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    if not access_token or access_token.strip() == "":
        if not _demo_mode_allowed():
            raise ConnectorConfigError(
                "Missing LinkedIn Organic access token. Reconnect the organization, or set "
                "DEMO_MODE=1 in a non-production environment to use demo data."
            )
        return _generate_mock_organic_data("linkedin_organic", account_id, start_date, end_date)

    window_start, window_end = _resolve_sync_window(start_date, end_date)
    org_urn = f"urn:li:organization:{account_id}"

    # organizationalEntityShareStatistics returns one aggregate bucket per
    # call unless timeIntervals with DAY granularity is supplied — daily
    # granularity is what the Universal Schema's per-date rows need.
    params: Dict[str, Any] = {
        "q": "organizationalEntity",
        "organizationalEntity": org_urn,
        "timeIntervals.timeGranularityType": "DAY",
        "timeIntervals.timeRange.start": int(datetime.combine(window_start, datetime.min.time()).timestamp() * 1000),
        "timeIntervals.timeRange.end": int(datetime.combine(window_end, datetime.min.time()).timestamp() * 1000) + 86400000,
    }
    url = "https://api.linkedin.com/rest/organizationalEntityShareStatistics"
    payload: Dict[str, Any] = {}
    last_error = "LinkedIn share statistics fetch failed"

    async with httpx.AsyncClient(timeout=45.0) as client:
        for version in _linkedin_version_candidates():
            headers = _linkedin_headers(access_token, version)
            response = await client.get(url, headers=headers, params=params)
            if _linkedin_version_unsupported(response):
                last_error = f"LinkedIn API version {version} is no longer supported"
                continue
            if response.status_code >= 400:
                message = _parse_linkedin_error(response, "LinkedIn share statistics fetch failed")
                raise ConnectorError(f"{message} (code {response.status_code})")
            payload = response.json()
            break

    if not payload:
        raise ConnectorError(last_error)

    rows: List[Dict[str, Any]] = []
    for element in payload.get("elements", []):
        stats = element.get("totalShareStatistics", {})
        time_range = element.get("timeRange", {})
        start_ms = time_range.get("start")
        bucket_date = (
            datetime.utcfromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
            if start_ms else window_start.strftime("%Y-%m-%d")
        )
        impressions = _to_int(stats.get("impressionCount"))
        engagements = (
            _to_int(stats.get("likeCount"))
            + _to_int(stats.get("commentCount"))
            + _to_int(stats.get("clickCount"))
        )
        rows.append({
            "date": bucket_date,
            "campaign": "LinkedIn Organization Posts",
            "ad_group": "N/A",
            "ad_asset": "N/A",
            "spend": 0.0,
            "impressions": impressions,
            "clicks": _to_int(stats.get("clickCount")),
            "conversions": 0,
            "revenue": 0.0,
            "organic_reach": _to_int(stats.get("uniqueImpressionsCount")) or impressions,
            "organic_engagements": engagements,
            "organic_shares": _to_int(stats.get("shareCount")),
            "ga_sessions": 0,
        })

    return _build_dataframe("linkedin_organic", account_id, rows)


def _generate_mock_organic_data(platform: str, account_id: str, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    import random
    from datetime import timedelta
    
    window_start, window_end = _resolve_sync_window(start_date, end_date)
    records = []
    
    # Pre-defined high-quality posts
    post_options = {
        "facebook_organic": [
            "We just rolled out a new visual analytics feature to our reporting tool. Check it out!",
            "How digital agencies can save up to 20 hours a week by automating cross-channel dashboards.",
            "Paid Ads vs Organic Social: Where should you focus your marketing budgets in 2026?",
            "Customer spotlight: how Antigravity helped a digital agency scale their marketing analysis.",
            "Quick tip: use custom KPIs to track blended CPA and Total ROAS across all your active channels.",
        ],
        "instagram_organic": [
            "Behind the scenes at Antigravity: designing our new premium dashboard! 🎬✨",
            "Say goodbye to manual spreadsheet reporting! 📊 Automate in seconds. Link in bio.",
            "Client testimonial: 'Antigravity completely changed how we report performance to CMOs.' ⭐⭐⭐⭐⭐",
            "Why agencies love our custom KPIs and scheduled email digests. 💌",
            "Spotting budget-shift opportunities is easy with Gemini. 🚀✨",
        ],
        "linkedin_organic": [
            "Excited to share our technical integration plan for GA4 and Organic Social channels. 🚀",
            "AI-powered digital advertising reporting: removing manual bottlenecks for marketers.",
            "We are hiring a Senior Product Engineer to join our decentralized development team. 💼",
            "How envelope encryption and KMS keyring storage secure customer OAuth credentials at rest.",
            "Agile data analytics: mapping disparate CSV schemas automatically into a Universal Schema.",
        ]
    }
    
    posts = post_options.get(platform, ["New update and dashboard tips."])
    
    current = window_start
    while current <= window_end:
        # Every 1-2 days we publish a post
        if random.random() > 0.4:
            campaign_name = random.choice(posts)
            
            if platform == "facebook_social" or platform == "facebook_organic":
                reach = random.randint(600, 4800)
                engagement = int(reach * random.uniform(0.03, 0.09))
                shares = int(engagement * random.uniform(0.04, 0.12))
                sessions = int(engagement * random.uniform(0.25, 0.55))
                conversions = int(sessions * random.uniform(0.015, 0.045))
                revenue = round(conversions * random.uniform(60, 140), 2)
            elif platform == "instagram_organic":
                reach = random.randint(1200, 7500)
                engagement = int(reach * random.uniform(0.05, 0.14))
                shares = int(engagement * random.uniform(0.08, 0.22))
                sessions = int(engagement * random.uniform(0.12, 0.32))
                conversions = int(sessions * random.uniform(0.01, 0.035))
                revenue = round(conversions * random.uniform(50, 110), 2)
            else:  # linkedin_organic
                reach = random.randint(800, 5500)
                engagement = int(reach * random.uniform(0.04, 0.11))
                shares = int(engagement * random.uniform(0.06, 0.18))
                sessions = int(engagement * random.uniform(0.2, 0.5))
                conversions = int(sessions * random.uniform(0.02, 0.055))
                revenue = round(conversions * random.uniform(70, 180), 2)
                
            records.append({
                "date": current.strftime('%Y-%m-%d'),
                "campaign": campaign_name,
                "ad_group": "N/A",
                "ad_asset": "N/A",
                "spend": 0.0,
                "impressions": reach,
                "clicks": engagement,
                "conversions": conversions,
                "revenue": revenue,
                "organic_reach": reach,
                "organic_engagements": engagement,
                "organic_shares": shares,
                "ga_sessions": sessions,
                # Flags this row as fabricated so etl.aggregate_data can
                # surface `usedMockData` to callers instead of presenting
                # it as real. Never remove without also removing the
                # DEMO_MODE gate in _fetch_*_organic below.
                "_is_mock_data": True,
            })
            
        current += timedelta(days=1)
        
    return _build_dataframe(platform, account_id, records)

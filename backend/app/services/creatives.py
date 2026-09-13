"""Ad-creative extraction for Meta, Google Ads and Microsoft Ads.

Ported from the standalone gallery's three Cloud Functions
(`meta_creatives_gallery.py`, `gads_creatives_gallery.py`,
`bing_creatives_gallery.py`). The *extraction* logic is carried over
faithfully — the same fields, the same Google GAQL queries, the same Bing bulk
columns and RSA/ETA fallbacks, the same Meta rate-limit backoff, the same
"skip creatives with no media, no headline and no text" rule.

What is NOT carried over is everything around it. Those pipelines read
account-scoped credentials from Secret Manager for a single hardcoded
advertiser. Here, credentials come from the workspace's `Connection` row, the
same source every other connector in this app uses, so the gallery is
multi-tenant for free and the six account-scoped secrets that migration
required (`META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `GOOGLE_ADS_REFRESH_TOKEN`,
`MICROSOFT_*`) are not needed at all.

Each fetcher returns a list of plain dicts. Asset mirroring and persistence are
someone else's job (`creative_assets.py` / `api/creatives.py`), so this module
stays testable without cloud storage or a database.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import httpx

from app.services.connectors import (
    ConnectorConfigError,
    ConnectorError,
    _build_microsoft_oauth,
    _meta_appsecret_proof,
    _refresh_microsoft_oauth_if_possible,
    _required_env,
    _strip_google_customer_id,
)

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = ("meta", "google", "microsoft")

# Meta error codes that mean "slow down", not "you did something wrong".
# Carried over verbatim from the source pipeline.
META_RATE_LIMIT_CODES = {17, 613, 80000, 80003, 80004, 80014}
META_MAX_RETRIES = 5
META_API_VERSION = "v20.0"


def _clean(value: Any) -> Optional[str]:
    """Normalise a platform field to a value or None.

    The source pipelines used the literal string 'N/A' as their empty marker,
    which then had to be special-cased in the UI and in every emptiness check.
    Here empty is NULL, and the 'N/A' sentinel is translated away at the edge.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "nan", "none"}:
        return None
    return text


def _is_empty_creative(record: Dict[str, Any]) -> bool:
    """The source pipelines' skip rule: no media, no headline, no text."""
    return not any((
        record.get("headline"),
        record.get("ad_text"),
        record.get("source_asset_url"),
        record.get("embed_url"),
    ))


# ── Meta ─────────────────────────────────────────────────────────────────────
#
# The source used the facebook_business SDK. This app talks to the Graph API
# over httpx everywhere else (see _fetch_meta_performance), including the
# appsecret_proof scheme, so the port follows suit rather than adding an SDK
# dependency for one module.

async def _meta_get(
    client: httpx.AsyncClient,
    url: str,
    params: Dict[str, Any],
    access_token: str,
) -> Dict[str, Any]:
    """One Graph GET with exponential backoff on rate-limit codes."""
    wait_seconds = 60
    for attempt in range(META_MAX_RETRIES):
        resp = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 200:
            return resp.json()

        try:
            error = resp.json().get("error", {})
        except Exception:  # noqa: BLE001
            error = {}
        code = error.get("code")
        if code in META_RATE_LIMIT_CODES and attempt < META_MAX_RETRIES - 1:
            logger.warning(
                "Meta rate limit (code %s) fetching creatives; waiting %ds before retry %d/%d.",
                code, wait_seconds, attempt + 2, META_MAX_RETRIES,
            )
            await asyncio.sleep(wait_seconds)
            wait_seconds *= 2
            continue
        raise ConnectorError(
            f"Meta creative fetch failed ({resp.status_code}): "
            f"{error.get('message') or resp.text[:300]}"
        )
    raise ConnectorError(f"Meta creative fetch failed after {META_MAX_RETRIES} rate-limited attempts.")


async def _meta_paginate(
    client: httpx.AsyncClient,
    url: str,
    params: Dict[str, Any],
    access_token: str,
    max_pages: int = 200,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    next_url: Optional[str] = url
    next_params: Optional[Dict[str, Any]] = params
    pages = 0
    while next_url and pages < max_pages:
        payload = await _meta_get(client, next_url, next_params or {}, access_token)
        items.extend(payload.get("data") or [])
        # Graph cursor URLs already carry every parameter, including the proof.
        next_url = (payload.get("paging") or {}).get("next")
        next_params = None
        pages += 1
    return items


def _meta_final_url(creative: Dict[str, Any]) -> Optional[str]:
    """object_url, else the link buried in object_story_spec — as in the source."""
    direct = _clean(creative.get("object_url"))
    if direct:
        return direct
    spec = creative.get("object_story_spec") or {}
    if "link_data" in spec:
        return _clean((spec.get("link_data") or {}).get("link"))
    if "video_data" in spec:
        cta = ((spec.get("video_data") or {}).get("call_to_action") or {})
        return _clean((cta.get("value") or {}).get("link"))
    return None


async def fetch_meta_creatives(account_id: str, access_token: str) -> List[Dict[str, Any]]:
    if not access_token:
        raise ConnectorConfigError("Missing Meta access token for this connection.")

    act_id = account_id if str(account_id).startswith("act_") else f"act_{account_id}"
    proof = _meta_appsecret_proof(access_token)
    base = f"https://graph.facebook.com/{META_API_VERSION}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Two passes, as in the source: creatives carry the media and copy, ads
        # carry the ad_id the gallery is keyed on and the creative reference.
        creatives = await _meta_paginate(
            client,
            f"{base}/{act_id}/adcreatives",
            {
                "fields": "id,name,title,body,image_url,thumbnail_url,object_url,object_story_spec",
                "limit": 50,
                "appsecret_proof": proof,
            },
            access_token,
        )
        creative_lookup = {c.get("id"): c for c in creatives if c.get("id")}

        ads = await _meta_paginate(
            client,
            f"{base}/{act_id}/ads",
            {
                "fields": "id,name,creative{id},campaign{name},adset{name}",
                "limit": 50,
                "appsecret_proof": proof,
            },
            access_token,
        )

    records: List[Dict[str, Any]] = []
    for ad in ads:
        ad_id = _clean(ad.get("id"))
        if not ad_id:
            continue
        creative = creative_lookup.get((ad.get("creative") or {}).get("id"), {})
        record = {
            "platform": "meta",
            "ad_id": ad_id,
            "account_id": str(account_id),
            "ad_name": _clean(ad.get("name")),
            "headline": _clean(creative.get("title")),
            "ad_text": _clean(creative.get("body")),
            "campaign_name": _clean((ad.get("campaign") or {}).get("name")),
            "ad_group_name": _clean((ad.get("adset") or {}).get("name")),
            "creative_type": None,
            "group_type": None,
            "final_url": _meta_final_url(creative),
            "source_asset_url": _clean(creative.get("image_url")) or _clean(creative.get("thumbnail_url")),
            "embed_url": None,
        }
        if _is_empty_creative(record):
            continue
        records.append(record)
    return records


# ── Google Ads ───────────────────────────────────────────────────────────────

_GADS_AD_LEVEL_QUERY = """
    SELECT
        customer.id,
        campaign.id,
        campaign.name,
        ad_group.id,
        ad_group.name,
        ad_group_ad.ad.id,
        ad_group_ad.ad.name,
        ad_group_ad.ad.type,
        ad_group_ad.ad.final_urls,
        ad_group_ad.ad.responsive_search_ad.headlines,
        ad_group_ad.ad.responsive_search_ad.descriptions
    FROM ad_group_ad
    WHERE campaign.status = 'ENABLED'
      AND ad_group.status = 'ENABLED'
      AND ad_group_ad.status = 'ENABLED'
"""

_GADS_ASSET_GROUP_QUERY = """
    SELECT
        customer.id,
        campaign.id,
        campaign.name,
        asset_group.id,
        asset_group.name,
        asset_group_asset.field_type,
        asset.id,
        asset.name,
        asset.type,
        asset.image_asset.full_size.url,
        asset.youtube_video_asset.youtube_video_id,
        asset.text_asset.text
    FROM asset_group_asset
    WHERE campaign.status = 'ENABLED'
      AND asset_group.status = 'ENABLED'
      AND asset_group_asset.status = 'ENABLED'
      AND asset.type IN ('IMAGE', 'YOUTUBE_VIDEO', 'TEXT')
"""


def _google_credentials(
    refresh_token: Optional[str],
    login_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    token = str(refresh_token or "").strip()
    if not token:
        raise ConnectorConfigError("Missing Google Ads refresh token for this connection.")
    credentials: Dict[str, Any] = {
        "developer_token": _required_env("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": _required_env("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": _required_env("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": token,
        "use_proto_plus": True,
    }
    login_customer_id = str(login_customer_id or "").strip()
    if login_customer_id:
        credentials["login_customer_id"] = _strip_google_customer_id(login_customer_id)
    return credentials


def _fetch_google_creatives_sync(
    account_id: str,
    refresh_token: Optional[str],
    login_customer_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException

    customer_id = _strip_google_customer_id(account_id)
    if not customer_id:
        raise ConnectorConfigError(f"Invalid Google account/customer ID: {account_id}")

    client = GoogleAdsClient.load_from_dict(
        _google_credentials(refresh_token, login_customer_id)
    )
    service = client.get_service("GoogleAdsService")

    records: List[Dict[str, Any]] = []
    for group_type, query in (
        ("Ad Group", _GADS_AD_LEVEL_QUERY),
        ("Asset Group", _GADS_ASSET_GROUP_QUERY),
    ):
        try:
            stream = service.search_stream(customer_id=customer_id, query=query)
            for chunk in stream:
                for row in chunk.results:
                    record = _google_row_to_record(row, group_type, account_id)
                    if record and not _is_empty_creative(record):
                        records.append(record)
        except GoogleAdsException as exc:
            # The source pipeline logged and continued past a failing query so
            # one bad account or unsupported resource could not sink the run.
            logger.warning(
                "Google Ads creative query (%s) failed for customer %s: %s",
                group_type, customer_id, exc.error.code().name if exc.error else exc,
            )
    return records


def _google_row_to_record(row: Any, group_type: str, account_id: str) -> Optional[Dict[str, Any]]:
    headline: Optional[str] = None
    ad_text: Optional[str] = None
    final_url: Optional[str] = None
    source_asset_url: Optional[str] = None
    embed_url: Optional[str] = None

    if group_type == "Ad Group":
        ad = row.ad_group_ad.ad
        item_id = str(ad.id)
        item_name = _clean(getattr(ad, "name", None)) or "Unnamed Ad"
        item_type = ad.type_.name
        if ad.final_urls:
            final_url = _clean(ad.final_urls[0])
        if item_type == "RESPONSIVE_SEARCH_AD":
            headlines = [h.text for h in ad.responsive_search_ad.headlines if h.text]
            descriptions = [d.text for d in ad.responsive_search_ad.descriptions if d.text]
            headline = " | ".join(headlines) if headlines else None
            ad_text = " | ".join(descriptions) if descriptions else None
        ad_group_name = _clean(getattr(row.ad_group, "name", None))
    else:
        asset = row.asset
        item_id = str(asset.id)
        item_name = _clean(getattr(asset, "name", None)) or "Unnamed Asset"
        item_type = asset.type_.name
        ad_group_name = _clean(getattr(row.asset_group, "name", None))

        if item_type == "TEXT":
            # A loose headline/description fragment is not a standalone
            # creative; the source skipped these and so does this.
            return None
        if item_type == "IMAGE":
            source_asset_url = _clean(asset.image_asset.full_size.url)
        elif item_type == "YOUTUBE_VIDEO":
            video_id = _clean(asset.youtube_video_asset.youtube_video_id)
            if video_id:
                # YouTube assets cannot be mirrored as files — keep the embed.
                source_asset_url = f"https://www.youtube.com/watch?v={video_id}"
                embed_url = f"https://www.youtube.com/embed/{video_id}"

    return {
        "platform": "google",
        "ad_id": item_id,
        "account_id": str(row.customer.id) if row.customer.id else str(account_id),
        "ad_name": item_name,
        "headline": headline,
        "ad_text": ad_text,
        "campaign_name": _clean(row.campaign.name),
        "ad_group_name": ad_group_name,
        "creative_type": item_type,
        "group_type": group_type,
        "final_url": final_url,
        "source_asset_url": source_asset_url,
        "embed_url": embed_url,
    }


async def fetch_google_creatives(
    account_id: str,
    refresh_token: Optional[str],
    login_customer_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(
        _fetch_google_creatives_sync, account_id, refresh_token, login_customer_id
    )


# ── Microsoft Ads ────────────────────────────────────────────────────────────
#
# Bing has no creative-listing API; the source pipeline pulled a Bulk export
# and parsed the CSV. That approach is carried over, including the column
# fallbacks that make modern RSA ads work alongside legacy ETA ones.

_BULK_COLUMNS = [
    "Type", "Status", "Id", "Parent Id", "Campaign", "Ad Group", "Name",
    "Title", "Title Part 2", "Title Part 3",
    "Title 1", "Title 2", "Title 3",
    "Text", "Text Part 2",
    "Description 1", "Description 2",
    "Final Url", "Images", "Videos",
]


def _bulk_cell(row: Dict[str, Any], column: str) -> Optional[str]:
    return _clean(row.get(column))


def _bulk_first(row: Dict[str, Any], *columns: str) -> Optional[str]:
    for column in columns:
        value = _bulk_cell(row, column)
        if value:
            return value
    return None


def _fetch_microsoft_creatives_sync(
    account_id: str,
    access_token: str,
    refresh_token: Optional[str],
    microsoft_customer_id: Optional[str],
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Returns `(records, rotated_refresh_token)`.

    Microsoft hands back a new refresh token on nearly every exchange. The
    source pipeline wrote it into Secret Manager, which is why that secret had
    to stay exclusively the gallery's. Here it is returned to the caller and
    persisted on the workspace's Connection row instead, so rotation is
    per-workspace and cannot clobber another app's credential.
    """
    # Validate the per-connection inputs before importing the SDK, so a
    # misconfigured connection fails with a useful message rather than an
    # unrelated ImportError.
    if not access_token:
        raise ConnectorConfigError("Missing Microsoft access token for this connection.")

    developer_token = _required_env("MICROSOFT_DEVELOPER_TOKEN")
    client_id = _required_env("MICROSOFT_CLIENT_ID")
    customer_id = (microsoft_customer_id or "").strip()
    if not customer_id:
        raise ConnectorConfigError(
            "Missing Microsoft customer ID for this account. Re-discover the Microsoft ad "
            "accounts and re-save the account selection for this connection."
        )

    import pandas as pd
    from bingads.authorization import AuthorizationData
    from bingads.v13.bulk import BulkServiceManager, DownloadParameters
    try:
        account_id_int = int(str(account_id).strip())
        customer_id_int = int(customer_id)
    except Exception as exc:  # noqa: BLE001
        raise ConnectorConfigError(f"Invalid Microsoft account/customer ID configuration: {exc}")

    oauth = _build_microsoft_oauth(client_id, access_token, refresh_token)
    _refresh_microsoft_oauth_if_possible(oauth, refresh_token)

    rotated: Optional[str] = None
    new_refresh = getattr(getattr(oauth, "oauth_tokens", None), "refresh_token", None)
    if new_refresh and new_refresh != (refresh_token or ""):
        rotated = new_refresh

    auth_data = AuthorizationData(
        account_id=account_id_int,
        customer_id=customer_id_int,
        developer_token=developer_token,
        authentication=oauth,
    )

    with tempfile.TemporaryDirectory(prefix="bing-creatives-") as work_dir:
        manager = BulkServiceManager(
            authorization_data=auth_data,
            poll_interval_in_milliseconds=5000,
            environment="production",
        )
        parameters = DownloadParameters(
            campaign_ids=None,
            data_scope=["EntityData"],
            download_entities=["Ads", "AssetGroups", "Images", "Videos"],
            result_file_directory=work_dir,
            result_file_name="bing_creatives_raw.csv",
            overwrite_result_file=True,
            last_sync_time_in_utc=None,
        )
        bulk_file_path = manager.download_file(parameters)
        if not bulk_file_path:
            raise ConnectorError("Microsoft did not return a bulk export file for creatives.")

        raw = pd.read_csv(bulk_file_path, encoding="utf-8-sig", dtype=str)

    if raw.empty or "Type" not in raw.columns:
        return [], rotated

    # Images and Videos arrive as their own rows; ad rows reference them by id.
    media_lookup: Dict[str, str] = {}
    for _, row in raw[raw["Type"].isin(["Image", "Video"])].iterrows():
        media_id = _clean(row.get("Id"))
        url = _clean(row.get("Url"))
        if media_id and url:
            media_lookup[media_id] = url

    available = [c for c in _BULK_COLUMNS if c in raw.columns]
    frame = raw[available].copy()
    frame = frame[frame["Type"].str.contains("Ad|Asset Group", na=False, case=False)]

    records: List[Dict[str, Any]] = []
    for _, raw_row in frame.iterrows():
        row = raw_row.to_dict()
        ad_id = _clean(row.get("Id"))
        if not ad_id:
            continue
        record = {
            "platform": "microsoft",
            "ad_id": ad_id,
            "account_id": str(account_id),
            "ad_name": _bulk_first(row, "Name", "Title", "Title 1"),
            "headline": _bulk_first(row, "Title", "Title 1", "Title Part 2", "Title 2", "Name"),
            "ad_text": _bulk_first(row, "Text", "Description 1", "Text Part 2", "Description 2"),
            "campaign_name": _bulk_cell(row, "Campaign"),
            "ad_group_name": _bulk_cell(row, "Ad Group"),
            "creative_type": _bulk_cell(row, "Type"),
            "group_type": None,
            "final_url": _bulk_cell(row, "Final Url"),
            "source_asset_url": _bulk_media_url(row, media_lookup),
            "embed_url": None,
        }
        if _is_empty_creative(record):
            continue
        records.append(record)

    return records, rotated


def _bulk_media_url(row: Dict[str, Any], media_lookup: Dict[str, str]) -> Optional[str]:
    """Bing embeds media references as a JSON-ish blob in the Images/Videos
    cell. Pull the first id out and resolve it against the media rows."""
    for column in ("Images", "Videos"):
        match = re.search(r'"id":\s*"?(\d+)"?', str(row.get(column) or ""))
        if match:
            url = media_lookup.get(match.group(1))
            if url:
                return url
    return None


async def fetch_microsoft_creatives(
    account_id: str,
    access_token: str,
    refresh_token: Optional[str],
    microsoft_customer_id: Optional[str],
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    return await asyncio.to_thread(
        _fetch_microsoft_creatives_sync,
        account_id,
        access_token,
        refresh_token,
        microsoft_customer_id,
    )


# ── Dispatch ─────────────────────────────────────────────────────────────────

async def fetch_creatives(
    platform: str,
    account_id: str,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    microsoft_customer_id: Optional[str] = None,
    google_login_customer_id: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch creatives for one account.

    Mirrors `connectors.fetch_platform_data`'s shape. Returns
    `(records, rotated_refresh_token)`; the rotated token is only ever
    non-None for Microsoft.
    """
    key = (platform or "").strip().lower()
    if key == "meta":
        return await fetch_meta_creatives(account_id, access_token or ""), None
    if key == "google":
        return await fetch_google_creatives(
            account_id, refresh_token, google_login_customer_id
        ), None
    if key == "microsoft":
        return await fetch_microsoft_creatives(
            account_id, access_token or "", refresh_token, microsoft_customer_id
        )
    raise ConnectorConfigError(
        f"Creatives are not supported for platform '{key}'. "
        f"Supported: {', '.join(SUPPORTED_PLATFORMS)}."
    )

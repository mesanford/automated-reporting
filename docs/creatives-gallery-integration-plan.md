# Integrating the ad-creatives gallery into automated-reporting

Source: `~/Documents/ads-creatives-multi` — three GCP Cloud Functions (`meta-api/`,
`gads_backfill/`, `bing-ads/`) that pull ad creatives into Firestore + Firebase Storage, plus a
standalone Next.js gallery with a Keep/Remove/Change review workflow.

This is **not** a file copy. The two apps disagree on every layer except the platform-extraction
logic, which is the part actually worth porting.

| Concern | ads-creatives-multi | automated-reporting | Port strategy |
|---|---|---|---|
| Tenancy | single tenant, one hardcoded account per platform | multi-workspace, `Connection` rows | rewrite: creatives become workspace-scoped |
| Credentials | Secret Manager, account-scoped secrets | per-workspace OAuth tokens in `connections`, encrypted | **drop the 6 account-scoped secrets entirely** |
| Metadata store | Firestore `ad_creatives` | Postgres + Alembic | new `ad_creatives` table |
| Asset store | Firebase Storage, `make_public()` | none today | GCS bucket, private, served via backend |
| Progress | Firestore `pipeline_status` doc | `SyncJob` rows + `SyncMonitor` UI | reuse `SyncJob` |
| Trigger | unauthenticated HTTP Cloud Function | authed FastAPI + `task_queue` | reuse task queue |
| UI | standalone Next app, Firestore `onSnapshot` | Next app, REST via `apiFetch` | port components, swap data layer |

Net: the three `*_creatives_gallery.py` files contribute their **extraction** bodies (~120 lines
each). Their Firestore/Storage/Secret-Manager scaffolding (~180 lines each) is replaced, not moved.

## What gets built

**1. `ad_creatives` table** (new model + Alembic migration)

Workspace-scoped, unique on `(workspace_id, platform, ad_id)`. Sync fields are upserted; the
review fields (`review_status`, `review_comment`, `review_updated_at`, `review_updated_by`) are
written only by the review endpoint and deliberately excluded from the upsert's update set — this
is the Postgres equivalent of the gallery's `merge=True` fix, which is what stops a re-sync from
wiping reviews.

**2. `app/services/creative_assets.py`** — asset mirroring

Meta's creative URLs are signed and expire, so referencing them directly rots the gallery. Assets
are downloaded once and stored under `creatives/{platform}/{workspace_id}/{ad_id}.{ext}`, keyed by
content hash for skip-if-present. Two backends: GCS (`CREATIVES_BUCKET`, defaulting to the
project's existing `…firebasestorage.app` bucket, so no new infrastructure) and a local directory
for dev. Objects stay **private** — the source app's `make_public()` is not carried over; the
backend serves them workspace-scoped via a redirect to a signed URL. YouTube assets keep storing
the embed URL rather than a file, as today.

**3. `app/services/creatives.py`** — the three fetchers

`fetch_creatives(platform, account_id, access_token, refresh_token, microsoft_customer_id)`,
mirroring the shape of the existing `fetch_platform_data`. Returns plain dicts; no storage
coupling.

- **Meta** — ported to `httpx` against the Graph API, reusing this app's `_meta_appsecret_proof`
  and API version, rather than pulling in the `facebook_business` SDK the source used. Keeps the
  two-pass creative→ad-id join and the rate-limit backoff (codes 17/613/80000/80003/80004/80014).
- **Google Ads** — both GAQL queries (`ad_group_ad` and `asset_group_asset`) carried over intact;
  client comes from the existing `get_google_ads_client(refresh_token)`.
- **Microsoft** — Bulk API download + CSV parse carried over, including the RSA/ETA fallback column
  mapping. The refresh-token rotation writes back to the **`Connection` row**, not Secret Manager —
  per-workspace, and it removes the "must never be a shared secret" hazard the source app carries.

The "skip creatives with no media, no headline, and no text" rule is kept for all three.

**4. `app/api/creatives.py`** — `GET /api/creatives` (platform / review-status / search filters,
paginated), `PATCH /api/creatives/{id}/review`, `POST /api/creatives/sync`, `GET
/api/creatives/{id}/asset`. All workspace-membership gated like every other router here. The sync
endpoint creates a `SyncJob` and enqueues a `sync_creatives` task handler, so progress shows up in
the existing monitor instead of a bespoke status document.

**5. `frontend/src/app/creatives/page.tsx` + `components/CreativeCard.tsx`** — ported from the
gallery, keeping the card/compact/expanded view modes, platform filter, review filter and
hide-reviewed behaviour. Data layer swaps `onSnapshot` for `apiFetch` + the workspace context.

## Deliberately not ported

`firestore.rules`, `firestore.indexes.json`, `storage.rules`, the `deploy-pipelines.yml` workflow,
the standalone Next app shell and its Firebase web SDK config, and the six account-scoped Secret
Manager secrets (`META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `GOOGLE_ADS_REFRESH_TOKEN`,
`MICROSOFT_CUSTOMER_ID`, `MICROSOFT_ACCOUNT_ID`, `MICROSOFT_REFRESH_TOKEN`) — all of these are
superseded by workspace `Connection` rows.

Open items from the source app's `MIGRATION.md` that this integration closes by construction:
public reads on `ad_creatives`, unauthenticated sync functions, world-writable `pipeline_logs`.

## Status

Built and verified (2026-08-31). Backend: 212 pytest tests pass (16 new in
`backend/tests/test_creatives.py`), `mypy app main.py --ignore-missing-imports`
clean, `ruff check .` clean. Frontend: `tsc --noEmit` and `eslint` clean, and
`next build` emits the `/creatives` route.

Files added:

| Path | Role |
|---|---|
| `backend/app/models.py` → `AdCreative` | the table |
| `backend/migrations/versions/d2f3a4b5c6da_ad_creatives.py` | migration (revises `c1e2f3a4b5c9`) |
| `backend/app/services/creatives.py` | the three ported fetchers |
| `backend/app/services/creative_assets.py` | asset mirroring (GCS / local) |
| `backend/app/services/creative_sync.py` | sync orchestration + upsert |
| `backend/app/api/creatives.py` | list / summary / review / sync / asset |
| `backend/app/services/task_handlers.py` → `sync_creatives` | queue handler |
| `frontend/src/app/creatives/page.tsx` | the gallery |
| `frontend/src/components/CreativeCard.tsx` | card / compact / expanded views |

New env vars are documented in `OPERATIONS.md` (all `CREATIVES_*`, all optional).

Not yet exercised against live platform APIs — the fetchers are ported code
running against real credentials for the first time, so the first sync per
platform is where field-shape surprises will show up.

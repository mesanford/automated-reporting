# Organic Channels + GA4 Integration Plan

Status: **Parts A and B implemented as of 2026-07-02 — UNVERIFIED against live
accounts.** All four connectors (Facebook Organic, Instagram Organic,
LinkedIn Organic, GA4) now have real API code in
`app/services/connectors.py`, following the documented shapes below, with
unit tests mocking the HTTP/gRPC layer (`tests/test_organic_connectors_real.py`,
`tests/test_ga4_connector.py`). They're flagged "Beta" in the UI
(`ConnectionsManager.tsx`) until confirmed against real Meta/LinkedIn/Google
developer apps and real connected accounts — both platforms reshape these
API surfaces periodically, so treat this as a strong first draft, not a
guarantee of correctness. The mock-data fallback (`_generate_mock_organic_data`)
still exists behind `DEMO_MODE`, used only when no access token is present.

Originally written 2026-07-02 after discovering that the existing "organic"
connectors returned fabricated data unconditionally (see Finding 0).

## Finding 0: the existing organic connectors are fake, and users can't tell

`app/services/connectors.py` already has `facebook_organic`, `instagram_organic`,
and `linkedin_organic` as connectable platforms, complete with real OAuth
(`app/api/oauth.py` `PLATFORM_CONFIG`, real Meta/LinkedIn scopes, real token
exchange and encrypted storage). But the three fetch functions —
`_fetch_facebook_organic`, `_fetch_instagram_organic`, `_fetch_linkedin_organic`
(connectors.py:1612-1650) — call `_generate_mock_organic_data()`
**unconditionally**, whether or not a real access token is present. The "real
API" code path is a comment (`# Real Facebook Page Graph API query skeleton`)
followed immediately by the same mock call. `_generate_mock_organic_data`
(connectors.py:1652) produces `random.randint`/`random.uniform` reach,
engagement, shares, and sessions, plus canned fake post captions.

Nothing in the frontend (`ConnectionsManager.tsx`, `Dashboard.tsx`,
`CampaignTable.tsx`) labels this as demo/mock data. A user connects a real
Facebook Page via real OAuth and gets back fictional numbers with no
indication they're fictional. For a tool whose product is automated
reporting that clients use to make budget decisions, this is a data-integrity
issue, not just a missing feature — treat it as equally urgent as the GA4 gap
this plan was originally scoped to close.

This plan therefore covers **four** integrations, not one: GA4, and turning
the three existing organic connectors from fake to real.

## What's genuinely reusable (no architecture changes needed)

The connector architecture is already platform-agnostic and well-suited to
adding more sources:

- **`Connection` model** (`app/models.py:52`) has no platform-specific
  columns — `platform`, `account_id`, encrypted `access_token`/
  `refresh_token`, `available_accounts`/`selected_account_ids` (JSON) all
  generalize cleanly to GA4 properties or real organic page/profile IDs.
- **OAuth plumbing** (`app/api/oauth.py`) — signed, expiring, HMAC-verified
  state (`sign_state`/`verify_state`); envelope-encrypted token storage via
  `app/services/security.py`. GA4 needs a Google OAuth flow with a different
  scope (`https://www.googleapis.com/auth/analytics.readonly`) — same code
  path as the existing `google` (Ads) OAuth entry, just a second
  `PLATFORM_CONFIG` key, e.g. `google_analytics`, since GA4 and Google Ads
  use different scopes and most agencies will want to connect them
  independently.
- **Sync dispatch** (`app/services/sync_runner.py:114`) calls
  `connectors.fetch_platform_data(connection.platform, ...)` — purely a
  string dispatch, no per-platform special-casing except Microsoft's
  customer-ID hydration. Adding `ga4` as a platform key is additive.
- **Universal Schema already has the organic columns**: `organic_reach`,
  `organic_engagements`, `organic_shares`, `ga_sessions` all exist in
  `UNIVERSAL_COLUMNS` (etl.py:5-8) and in the `reports`/aggregation
  pipeline. This is presumably *why* the mock data function exists — someone
  built the schema and the UI for organic data, then stubbed the actual API
  calls and never came back to finish them.

## Part A — Fix the three existing organic connectors (fake → real)

### A1. Facebook Organic (Meta Graph API)
- Scopes already correct: `pages_read_engagement,pages_show_list,public_profile`.
- Real implementation: `GET /{page-id}/feed` for posts in the date window,
  then `GET /{post-id}/insights?metric=post_impressions,post_engaged_users,...`
  per post (or batch via Graph API batch requests to avoid N+1 calls).
  Reuse `_meta_appsecret_proof()` (connectors.py:327) — already used by the
  paid Meta connector for the same app.
- Account discovery: Meta Organic needs a **Page** selection, not an ad
  account. `available_accounts` discovery should call `GET /me/accounts` to
  list Pages the user manages, distinct from the ad-account discovery the
  paid Meta connector already does.

### A2. Instagram Organic (Meta Graph API, Instagram Business endpoints)
- Instagram Insights requires the IG account to be linked to a Facebook
  Page and fetched via `GET /{ig-user-id}/insights` (Meta merged the IG
  Graph API into the main Graph API in 2024) — confirm current endpoint
  shape against Meta's docs at implementation time, this changes yearly.
- Account discovery: `GET /{page-id}?fields=instagram_business_account`
  after the Page is selected in A1's flow — IG account discovery is
  downstream of Page discovery, so these two connectors likely share a
  single "connect Meta" OAuth grant with two account-selection steps.

### A3. LinkedIn Organic (LinkedIn Marketing API)
- Scopes already correct: `r_organization_social,w_organization_social,r_basicprofile`
  (note: `w_organization_social` is a write scope the app doesn't need for
  reporting — worth dropping to reduce the permission ask during OAuth
  consent, unless a future feature posts on the user's behalf).
- Real implementation: `GET /organizationalEntityShareStatistics` for
  aggregate metrics, `GET /shares` for individual post-level data. LinkedIn's
  API versioning is header-based (`LinkedIn-Version`) — the paid LinkedIn ads
  connector already has version-negotiation helpers
  (`_linkedin_version_candidates`, `_linkedin_version_unsupported`,
  connectors.py:177-234) that should be reused rather than re-implemented.
- Account discovery: `GET /organizationAcls?q=roleAssignee` to list
  Organization Pages the authenticated user administers.

### A4. Common follow-up for A1-A3
- Delete `_generate_mock_organic_data` once all three call sites use real
  API calls — or, if mock data is worth keeping for local dev/demos,
  gate it behind an explicit `DEMO_MODE` env var (never silently, never
  in an environment with `ENVIRONMENT=production`) and surface a visible
  "Demo data" badge in the UI wherever it's shown. Silent fallback like
  today's must not survive in any form.

## Part B — GA4 (new integration)

### B1. Auth
- New OAuth platform key: `google_analytics` (or `ga4`), scope
  `https://www.googleapis.com/auth/analytics.readonly`. Google allows
  incremental auth / multiple scopes on one Google account, but since this
  app already treats each `Connection` as a separate OAuth grant per
  platform, a second Google OAuth entry (distinct from `google` = Ads) is
  the pattern-consistent choice, matching how `meta` (ads) and
  `facebook_organic`/`instagram_organic` are already three separate
  Connections against the same underlying platform family.

### B2. Data fetch
- Google Analytics Data API v1 (`analyticsdata.googleapis.com`), package
  `google-analytics-data` (official Python client) — new dependency, not
  currently in `requirements.txt`.
- `runReport` with dimensions `date` (+ optionally `sessionDefaultChannelGroup`
  or `sessionSource`/`sessionMedium` to distinguish organic vs. paid/referral
  traffic within GA4 itself) and metrics `sessions`, `engagedSessions`,
  `conversions`, `totalRevenue` (if e-commerce tracking is set up on the
  client's property — many won't have it, so `revenue` should default to 0
  gracefully, matching the existing `_safe_divide` pattern in etl.py).
- Maps to Universal Schema: GA4 `sessions` → `ga_sessions` (already a
  column); GA4 doesn't have a direct "spend" or "impressions" equivalent —
  those stay 0 for GA4 rows, same treatment the mock organic data already
  gives `spend`.

### B3. Account discovery
- GA4 uses **Properties**, not accounts — `GET
  /v1beta/accountSummaries` (Admin API, a *different* API surface,
  `analyticsadmin.googleapis.com`) lists accessible GA4 properties. This
  means GA4 needs two Google API surfaces (Admin API for discovery, Data
  API for reporting), both under the same OAuth token.

### B4. Open question to resolve before implementation
GA4 sessions aren't inherently "organic" — a GA4 property reports all
traffic (paid, organic, direct, referral) unless filtered. Decide: does the
GA4 connector (a) pull only organic-channel-grouped sessions to slot into
the existing `ga_sessions` organic column, or (b) pull all traffic and
introduce a new non-organic bucket in the schema? Option (a) matches how
the UI presents "Organic" as a channel family today; option (b) is more
accurate to what GA4 actually measures but is a bigger schema/UI change.
Recommend (a) for a first pass, with a follow-up decision once real client
data is in front of you.

## Suggested build order

1. **A4 mitigation first** (small, hours not days): gate the mock fallback
   behind `DEMO_MODE`/non-prod check and add a UI badge, so nothing shipped
   today is silently deceptive while B and the rest of A are built out.
2. **A1 (Facebook Organic)** — smallest real lift, and A2 (Instagram)
   shares most of its OAuth/account-discovery plumbing.
3. **A2 (Instagram Organic)** — builds directly on A1.
4. **B (GA4)** — independent of A1-A3, can be parallelized with them if
   there's more than one person working on this.
5. **A3 (LinkedIn Organic)** — most API-version churn risk, do last so the
   version-negotiation helpers it reuses are already battle-tested by the
   paid LinkedIn connector's existing usage patterns.

## Not in scope for this plan
- Frontend chart/dashboard changes to visualize the new real data (assumed
  to work automatically once real numbers replace mock ones, since the UI
  already renders `organic_reach`/`ga_sessions` — but worth a UI pass to
  confirm labels like "Organic Reach" still make sense once real, sparser,
  possibly-zero data starts showing up instead of guaranteed daily mock posts).
- Rate limiting / API quota handling per platform (Meta, LinkedIn, and
  Google all have distinct rate-limit models) — needs its own design pass
  once real calls are being made and quotas are observed in practice.

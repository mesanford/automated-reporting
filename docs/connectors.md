# Connectors: setup and operation

What you need in place for ad-platform connections to work, and what to expect
once they do.

## The model

Every ad-platform API call carries three identities. Keeping them separate is
the whole game — conflating them is what caused the credential bug fixed in
`fix/per-connection-credentials`.

| | What it is | Scope | Lives in |
|---|---|---|---|
| **Application** | OAuth client ID/secret, developer token | One set, shared by all tenants | Env vars |
| **User grant** | Refresh/access token from *that tenant* connecting | One per connection | `connections` table, encrypted |
| **Account context** | Which manager/customer the call runs through | One per connection (often per account) | `connections.login_customer_id`, `available_accounts[].login_customer_id` |

Nothing in the second or third row may ever be read from the environment. If it
is, one tenant's sync runs against another tenant's account.

## Do this first

**Reconnect every existing Google Ads and Microsoft Ads connection.** Rows
created before this change have `login_customer_id` null and no per-account
manager attribution. Reconnecting re-runs discovery and populates both.

Google Ads connections under a manager account will fail with an auth error
until you do. Directly-accessible accounts keep working (they need no manager
header), which makes this easy to miss.

Run the migration first:

```bash
cd backend && alembic upgrade head
```

## Environment variables

Only application-level credentials. Five are required:

```
GOOGLE_ADS_DEVELOPER_TOKEN
GOOGLE_ADS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET
MICROSOFT_DEVELOPER_TOKEN
MICROSOFT_CLIENT_ID
```

`GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, and
`MICROSOFT_CUSTOMER_ID` are no longer read anywhere. Delete them from `.env` and
from the Cloud Run service — leaving them sets the expectation that they do
something.

## Per platform

### Google Ads

- **Developer token** belongs to *your* manager account and identifies your
  application. Three levels: Test (test accounts only), Basic, Standard. Apply
  from the API Center in your MCC. Basic is enough to start; Standard raises the
  daily operation cap.
- **Scope** is `https://www.googleapis.com/auth/adwords`. There is **no
  read-only variant** — connecting grants read *and write*. Disclose this;
  sophisticated clients will ask why a reporting tool wants mutate access.
- **`login-customer-id`** is the manager the call is made *through*. Required
  when reaching a client account via an MCC, omitted for direct access.
  Discovery sets it now; you should not need to touch it.

### Meta

- No developer token. App ID + App Secret.
- **`ads_read` requires App Review plus Business Verification** before anyone
  outside your app's own admins/developers/testers can connect. This is the
  longest pole in the whole product; start it before you need it.
- **There is no refresh token.** You get a long-lived user token (~60 days) and
  must re-exchange it for a new one before it expires, per connection. If a
  scheduler doesn't do this, every Meta connection dies roughly every two
  months. A System User token (Business Manager) doesn't expire but requires the
  client to add your app to their Business Manager.
- A Data Deletion Callback URL is required for apps handling user data.

### Microsoft Advertising

- Developer token (yours) + OAuth, same shape as Google.
- `CustomerId` scopes the account context and is stored per account in
  `available_accounts[].customer_id`.
- Microsoft **rotates the refresh token on nearly every exchange**. The code
  persists the rotated value back to the connection; if you add another code
  path that talks to Microsoft, it must do the same or connections will break
  unpredictably.

### LinkedIn

- Marketing Developer Platform access requires an application and is genuinely
  gated.
- Access tokens last 60 days, refresh tokens 365. Refresh tokens are only issued
  to approved partners.

### GA4

- Standard Google OAuth with `analytics.readonly`. No developer token, no
  approval. The easiest one by a wide margin.

## Operating them

**Rate limits are per developer token, not per tenant.** Google Ads caps daily
operations against *your* token across *every* customer you touch. One heavy
account throttles all of them. Before you have more than a handful of connected
accounts you'll need per-tenant queuing and fair scheduling, not just retry.

**Revocation is routine, not exceptional.** Users disconnect, passwords change,
admins remove access. Treat `invalid_grant` and 401 as terminal for that
connection: mark it inactive and surface it in the UI. Retrying a dead token
forever burns quota and hides the problem.

**API versions expire on a schedule.** Meta deprecates Graph API versions on a
rolling ~2-year clock; Google Ads sunsets versions annually. This is recurring
maintenance whether or not anyone is using the app that quarter — budget for it
as a standing cost, not a one-off.

**Platform terms restrict commingling client data.** Google Ads API and Meta
Platform Terms both constrain mixing data across advertisers. Per-workspace
isolation is a legal requirement here, not only an architectural preference —
which is why credentials must stay per-connection.

## If a connection breaks

1. Check `connections.is_active` and `last_sync_status`.
2. `invalid_grant` / 401 → the user revoked access. They must reconnect; no
   amount of retrying helps.
3. Google Ads auth error on a manager-held account → `login_customer_id` is
   probably null. Reconnect to re-run discovery.
4. "Missing Microsoft customer ID" → re-discover accounts and re-save the
   selection for that connection.
5. Meta failures clustered around the 60-day mark → long-lived token expiry.
   That's the re-exchange, not a credential problem.

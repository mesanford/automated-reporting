# Operations

Day-2 reference for running this app. Each section starts with the why so you
can skim and skip what isn't load-bearing for your situation.

## Production mode

The trigger is `APP_ENV=production`. Any other value (or unset) is treated as
dev/staging. The boot-time preflight (`backend/app/preflight.py`) refuses to
start in production when any of these are true:

- `ALLOW_DEV_AUTH=1` (would let any caller mint requests via `X-User-Id`)
- `ALLOW_INTERNAL_NO_OIDC=1` (would let anyone hit the Cloud Tasks worker)
- Neither `GOOGLE_APPLICATION_CREDENTIALS` nor `FIREBASE_PROJECT_ID` is set
  (no way to verify Bearer tokens — every authed endpoint would 503)
- None of `KMS_KEY_NAME` / `ENCRYPTION_KEY` / `GCP_PROJECT_ID` is set (would
  fall back to a randomly-generated local Fernet key file, which is fine on
  one box and a disaster on Cloud Run)

Same module emits **warnings** in non-production for those same conditions
plus a few softer ones (CORS defaulting to localhost in prod, for example).
Warnings are surfaced at `GET /readyz`, so you can verify the deploy without
shell access to the container.

## Env vars (one table)

| Variable                       | Required where     | Purpose                                                                                  |
| ------------------------------ | ------------------ | ---------------------------------------------------------------------------------------- |
| `APP_ENV`                      | All envs           | `production` activates strict preflight + warning ↔ error promotion.                     |
| `DATABASE_URL`                 | All envs           | SQLAlchemy URL. Defaults to local SQLite; use Postgres in any shared env.                |
| `GOOGLE_APPLICATION_CREDENTIALS` | Prod             | Path to a Firebase service-account JSON. Required for verifying Bearer tokens.           |
| `FIREBASE_PROJECT_ID`          | Optional in prod   | Project ID for Firebase Admin. Set if ADC can't infer it.                                |
| `ALLOW_DEV_AUTH`               | Dev only           | `1` enables `X-User-Id` mock auth. **Hard-blocked in production by preflight.**          |
| `GCP_PROJECT_ID`               | Prod-ish           | Activates Secret Manager + Vertex-style auto-discovery; secrets fall back to env vars.   |
| `SECRET_PREFIX`                | Optional           | Prepended to Secret Manager secret names (e.g. `prod-`).                                 |
| `ENCRYPTION_KEY`               | Dev / fallback     | Fernet key for stored OAuth tokens. Auto-generated to a local file if unset in dev.      |
| `KMS_KEY_NAME`                 | Prod (recommended) | Fully-qualified KMS key for envelope encryption. New tokens use `v2:` ciphertext.        |
| `OAUTH_STATE_SECRET`           | Optional           | HMAC key for signed OAuth state. Falls back to `ENCRYPTION_KEY`.                         |
| `OAUTH_REDIRECT_URI`           | Prod               | Public URL of `/api/auth/callback`. Must match the value registered with each provider.  |
| `FRONTEND_URL`                 | Prod               | Public URL of the frontend. Used to build invite accept URLs.                            |
| `CORS_ALLOWED_ORIGINS`         | Prod               | Comma-separated allowlist. Default is localhost — real browsers won't pass without this. |
| `CLOUD_TASKS_QUEUE`            | Prod (recommended) | Full queue resource path. Without it, syncs run in-process (fine for dev).               |
| `CLOUD_TASKS_WORKER_URL`       | With CLOUD_TASKS   | Cloud Run URL of this service.                                                            |
| `CLOUD_TASKS_INVOKER_SA`       | With CLOUD_TASKS   | Service account Cloud Tasks signs with; must match what `/api/internal/tasks/run` checks.|
| `CLOUD_TASKS_OIDC_AUDIENCE`    | Optional           | Defaults to `CLOUD_TASKS_WORKER_URL`.                                                    |
| `ALLOW_INTERNAL_NO_OIDC`       | Dev only           | `1` skips OIDC on the internal worker. **Hard-blocked in production.**                   |
| `SENDGRID_API_KEY`             | Prod (for invites) | Without it, invite emails fall back to "copy this link" UX.                              |
| `INVITE_FROM_EMAIL`            | With SENDGRID      | Verified sender. Without verification, SendGrid rejects.                                 |
| `INVITE_FROM_NAME`             | Optional           | Display name, default `Antigravity`.                                                     |
| `GOOGLE_API_KEY`               | Prod               | Gemini API key for analysis + conversational analytics.                                  |
| `GOOGLE_ADS_*`                 | Per workspace      | Google Ads OAuth client + developer token.                                               |
| `META_CLIENT_*`                | Per workspace      | Meta OAuth client.                                                                       |
| `LINKEDIN_CLIENT_*`            | Per workspace      | LinkedIn OAuth client.                                                                   |
| `TIKTOK_CLIENT_*`              | Per workspace      | TikTok OAuth client.                                                                     |
| `MICROSOFT_CLIENT_*`           | Per workspace      | Microsoft Ads OAuth client + developer token.                                            |

Frontend equivalents (set in `frontend/.env.local`):

| Variable                          | Purpose                                                        |
| --------------------------------- | -------------------------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL`        | Backend URL. Defaults to `http://localhost:8000`.              |
| `NEXT_PUBLIC_FIREBASE_API_KEY`    | Firebase client API key.                                       |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`| Usually `<project>.firebaseapp.com`.                           |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | Project id.                                                    |
| `NEXT_PUBLIC_FIREBASE_APP_ID`     | Web app id from Firebase console.                              |
| `NEXT_PUBLIC_DEV_USER_ID`         | Dev escape hatch — sends `X-User-Id` when Firebase isn't set up.|

## Dev vs production contract

| Behavior                           | Development (default)                                 | Production (`APP_ENV=production`)                          |
| ---------------------------------- | ----------------------------------------------------- | ---------------------------------------------------------- |
| Auth                               | `X-User-Id` accepted if `ALLOW_DEV_AUTH=1`            | Firebase Bearer only. `ALLOW_DEV_AUTH` rejected at boot.   |
| OAuth login                        | `GET /api/auth/{platform}/login` still works         | `GET /…/login` returns 410. Use `POST /api/auth/oauth/start`.|
| Sync                               | In-process asyncio (orphans on TestClient teardown)   | Cloud Tasks, OIDC-verified, retried per queue config.       |
| Internal worker                    | `ALLOW_INTERNAL_NO_OIDC` accepted                     | OIDC required.                                              |
| Token encryption                   | Local key file in `backend/.local_encryption_key`     | KMS-wrapped DEK per token (`v2:` ciphertext).               |
| Secrets                            | `.env` file                                           | Secret Manager (with `.env` as fallback).                   |
| Invites                            | Copy-link UI (delivery: `noop`)                       | SendGrid email (delivery: `sendgrid`).                      |
| CORS                               | localhost:3000                                        | `CORS_ALLOWED_ORIGINS` allowlist.                           |

The `/readyz` endpoint returns `subsystems` and `warnings` keys so any of
these can be verified post-deploy without log-diving.

## First-time GCP deploy

There is a one-shot script: [`scripts/deploy_gcp.sh`](scripts/deploy_gcp.sh).

```bash
# Dry run first — prints every gcloud command without executing.
PROJECT_ID=your-gcp-project FRONTEND_URL=https://your-frontend.example.com \
  ./scripts/deploy_gcp.sh --dry-run

# Real run.
PROJECT_ID=your-gcp-project FRONTEND_URL=https://your-frontend.example.com \
  ./scripts/deploy_gcp.sh
```

What the script does (8 phases, re-runnable):

1. Enables the GCP APIs (run, sqladmin, secretmanager, kms, cloudtasks, cloudscheduler, cloudbuild, artifactregistry, iam).
2. Creates a Cloud SQL Postgres 16 instance, database, and user.
3. Creates a KMS keyring + symmetric encryption key for the OAuth-token envelope.
4. Creates two service accounts (runtime + invoker) and binds the runtime SA's roles.
5. Walks through Secret Manager secrets interactively. Existing secrets are left alone unless you say `rotate`. Generates `ENCRYPTION_KEY` and `DB_PASSWORD` if missing.
6. Deploys Cloud Run from `./backend` with all env vars + secret mounts. Two-pass: first deploy gets a placeholder URL so the deploy can finish; the captured URL is then patched back into `OAUTH_REDIRECT_URI` + `CLOUD_TASKS_WORKER_URL` so the live service points at itself.
7. Creates the Cloud Tasks queue and grants the invoker SA `roles/run.invoker` on the service.
8. Creates / updates two Cloud Scheduler jobs (`*-sync-tick`, `*-digest-tick`) firing every 5 minutes against the internal worker endpoints with OIDC.

Then it `curl`s `/readyz` and prints the subsystem report so you can verify.

**What it does *not* do** (manual steps):

- Deploy the Next.js frontend. App Hosting is interactive enough that `firebase init apphosting` + the console is faster. Set `NEXT_PUBLIC_API_BASE_URL` to the Cloud Run URL the script reports.
- Configure Firebase Auth providers. Enable Google sign-in in the Firebase console once.
- Re-encrypt existing OAuth refresh tokens under the new KMS scheme. See the "Rotating the KEK" runbook below; for a fresh deploy with no production tokens yet, no action needed.

Flags worth knowing:

- `--only-deploy` — skips every provisioning phase, just rebuilds and redeploys the Cloud Run service. Use this on every code push after the first run.
- `--skip-secrets` — skip the interactive secret prompts. Useful in CI.
- `--skip-sql`, `--skip-kms`, `--skip-iam`, `--skip-tasks`, `--skip-scheduler`, `--skip-verify` — surgical reruns of single phases.
- `--dry-run` — print all commands, execute none.

## Runbooks

### Rotating the OAuth-state signing key

Signed state TTL is 10 minutes (`STATE_TTL_SECONDS` in `oauth.py`), so a key
rotation invalidates any state in flight — users who started an OAuth flow
just before rotation will get `Not a member of the workspace` on the
callback and need to click Connect again. Acceptable for a 10-minute window.

1. Generate a new value: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Update Secret Manager (`OAUTH_STATE_SECRET`) or the env var.
3. Restart Cloud Run (`gcloud run services update antigravity-api --update-env-vars=...`).
4. Confirm: hit `GET /readyz` and look at `subsystems.TOKEN_ENCRYPTION` — should still be `kms-envelope` (state signing is independent).
5. If `OAUTH_STATE_SECRET` is unset, the key derives from `ENCRYPTION_KEY` — same rotation steps apply via Secret Manager.

### Rotating the token-encryption KEK (KMS)

Cloud KMS handles version rotation automatically when you call `gcloud kms
keys versions create`. Old `v2:` ciphertext keeps decrypting because the
wrapped DEK carries the key version. There's no app-side action.

To rotate the *Fernet* key (`ENCRYPTION_KEY`) on the legacy path, you must
re-encrypt every stored token before retiring the old key:

```python
# One-shot script, run with the OLD key still set as ENCRYPTION_KEY.
for conn in db.query(Connection).all():
    conn.access_token = encrypt_token(decrypt_token(conn.access_token))
    conn.refresh_token = encrypt_token(decrypt_token(conn.refresh_token))
db.commit()
# Then swap the key in Secret Manager and re-deploy.
```

This is why KMS is the recommended path: rotation becomes a console click.

### Investigating a failed SyncJob

1. `GET /api/sync-jobs/{id}` (as a member of the owning workspace) — shows
   `status`, `error_message`, timing, and `report_id` if it ever completed.
2. The `error_message` field carries the user-visible reason raised by
   `sync_runner.SyncError`. Internal exceptions (anything else) get a
   generic `"Internal error: …"` prefix and the task is re-raised so
   Cloud Tasks can retry per the queue config.
3. Cloud Logging filter (production): `severity>=ERROR resource.labels.service_name="antigravity-api"`.
4. For platform-credential errors, the user's fix is to reconnect that
   `Connection` via the dashboard — that re-encrypts the refresh token
   under the current scheme too.

### "We accidentally shipped `ALLOW_DEV_AUTH=1`"

If preflight is wired (the default since this slice), boot fails and
Cloud Run rolls back to the previous revision automatically. If somehow
the new revision is serving:

1. `gcloud run services update-traffic antigravity-api --to-revisions=PREV=100`
   to flip back instantly.
2. Set `APP_ENV=production` (if it wasn't) — that hard-codes the boot block.
3. Unset `ALLOW_DEV_AUTH` and redeploy.
4. Rotate any OAuth tokens that may have been minted under the
   compromised window — they could have been issued for any UID.

### Restoring user access during Firebase outage

The dev path (`ALLOW_DEV_AUTH=1`) is not an option in production (preflight
refuses), and shouldn't be: it would defeat the whole auth model. Options:

1. Wait for Firebase to recover. The DB is intact; once auth is back,
   users sign in and everything resumes.
2. If the outage is on **your** Firebase project specifically, switch to a
   backup project: change `FIREBASE_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS`,
   redeploy, communicate the temporary sign-in flow to users.

### Cloud SQL Postgres backup / restore

1. **Automated backups**: enable in Cloud SQL console. Default retention is
   7 days; bump to 30 for prod.
2. **Point-in-time recovery**: enable WAL retention. Requires backups and
   binary logs.
3. **Restore**: `gcloud sql backups restore <BACKUP_ID> --restore-instance=<NEW_INSTANCE>` to
   a new instance, then update `DATABASE_URL` to point at it. Don't
   restore over the live instance — restore to a copy and switch the
   connection string.

### Cloud Run cold start tuning

SSE-heavy workloads (this app's chat + sync polling) don't tolerate cold
starts well. Set `--min-instances=1` on the service so at least one container
is always warm. With CPU always-on (`--no-cpu-throttling`, required for SSE
anyway), this costs ~$10/mo per region.

## CI

`.github/workflows/ci.yml` runs on push + PR:

- Backend: install deps, `compileall`, `alembic upgrade head` against a
  temp SQLite, `pytest`.
- Frontend: install, lint, `tsc --noEmit`.

The Alembic step catches migration drift; pytest catches workspace/role/audit
regressions. The frontend typecheck catches API shape changes if the
backend ever breaks the documented response envelopes.

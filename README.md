# Automated Reporting

Cross-channel ad performance reporting with a FastAPI backend and Next.js frontend.

## Stack

- Backend: FastAPI, SQLAlchemy, Pandas
- Frontend: Next.js, TypeScript, Recharts
- Database: SQLite (local)

## Monorepo Layout

- `backend/` FastAPI app and ETL services
- `frontend/` Next.js web app
- `docker-compose.yml` local container orchestration

## Prerequisites

- Python 3.11+
- Node.js 22 LTS recommended
- npm 10 or 11

Frontend runtime note:

- Do not use Node 25 for this project. With Next.js `16.1.6`, Node `25.x` can start the dev server process but leave it hanging before any page is served.
- Supported frontend runtime range in this repo is `>=20.9.0 <25`, with Node `22.x` recommended.
- The repo includes `.nvmrc` and `.node-version`, both pinned to `22`.

## macOS Setup

If you are on macOS and the frontend hangs or never loads, switch to Node `22` before starting `frontend/`.

Using Homebrew:

```bash
brew install node@22
export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
hash -r
node -v
npm -v
```

Expected `node -v` result:

```text
v22.x.x
```

Using `nvm` if you already have it installed:

```bash
nvm install 22
nvm use 22
node -v
```

## Required Environment (Real Integrations)

Create `backend/.env` with platform credentials before connecting accounts in the UI.

Google Ads:

- `GOOGLE_ADS_DEVELOPER_TOKEN`
- `GOOGLE_ADS_CLIENT_ID`
- `GOOGLE_ADS_CLIENT_SECRET`
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (optional, manager account)

Meta:

- `META_CLIENT_ID`
- `META_CLIENT_SECRET`

LinkedIn:

- `LINKEDIN_CLIENT_ID`
- `LINKEDIN_CLIENT_SECRET`
- `LINKEDIN_API_VERSION` (optional override; backend now auto-retries current and prior monthly versions if configured version is deprecated)

TikTok:

- `TIKTOK_CLIENT_ID`
- `TIKTOK_CLIENT_SECRET`

Microsoft Ads:

- `MICROSOFT_CLIENT_ID`
- `MICROSOFT_CLIENT_SECRET`
- `MICROSOFT_DEVELOPER_TOKEN`
- `MICROSOFT_CUSTOMER_ID` (optional for account discovery, still recommended and used for reporting context)

Shared:

- `OAUTH_REDIRECT_URI` (example: `http://localhost:8000/api/auth/callback`)
- `FRONTEND_URL` (example: `http://localhost:3000`)
- `ENCRYPTION_KEY` (Fernet key for token encryption at rest)
- `CORS_ALLOWED_ORIGINS` — comma-separated list of allowed origins. Default: `http://localhost:3000,http://127.0.0.1:3000`. Set explicitly in production (e.g. `https://app.example.com`). Wildcard is **not** supported in combination with the credentialed auth flow — always be explicit.

Firebase Auth (backend verifies user ID tokens):

- `GOOGLE_APPLICATION_CREDENTIALS` — path to a Firebase service-account JSON, or any GCP credential file. Required to verify ID tokens.
- `FIREBASE_PROJECT_ID` — optional; only needed if Application Default Credentials can't infer the project.
- `ALLOW_DEV_AUTH=1` — local-only escape hatch. When set, the backend falls back to the legacy `X-User-Id` header path so the app runs without Firebase credentials. Never set this in production.

Google Cloud secrets (optional; replaces `.env` reads for sensitive values):

- `GCP_PROJECT_ID` — when set, the backend resolves OAuth client secrets, API keys, and `ENCRYPTION_KEY` via Google Secret Manager instead of env vars. Each secret's name in Secret Manager matches its env-var name (e.g. `META_CLIENT_SECRET`). Env vars still win when the secret is missing or `GCP_PROJECT_ID` is unset, so local `.env` keeps working.
- `SECRET_PREFIX` (optional) — prepended to secret names in Secret Manager (e.g. `prod-`). Useful when sharing a project across environments.

Email delivery for workspace invites (optional; falls back to manual link-sharing):

- `SENDGRID_API_KEY` — when set, workspace invites are delivered via SendGrid. Without it, the invite endpoint still mints a token and the settings UI surfaces a copy-able accept link.
- `INVITE_FROM_EMAIL` — the From address SendGrid uses. Required when `SENDGRID_API_KEY` is set; must be a verified sender in your SendGrid account.
- `INVITE_FROM_NAME` (optional) — display name, default `Antigravity`.
- The invite endpoint always returns the same shape (`token`, `accept_url`, `email_delivery: {delivered, provider, error}`), so the frontend can render "emailed" or "copy this link" affordances without conditional API surface.

Cloud KMS (optional; envelope encryption for stored OAuth tokens):

- `KMS_KEY_NAME` — fully-qualified KMS key, e.g. `projects/p/locations/us/keyRings/r/cryptoKeys/k`. When set, new OAuth tokens are encrypted with a per-token AES-GCM data key wrapped by KMS. Stored ciphertext is tagged `v2:`; legacy tokens (`v1:` or unprefixed) keep decrypting through the Fernet `ENCRYPTION_KEY` path so no re-encryption is required to roll this out.
- To rotate the KEK, rotate the underlying KMS key in GCP — historical `v2:` ciphertext keeps decrypting because the wrapped DEK references the prior key version.

Firebase Auth (frontend obtains ID tokens; create `frontend/.env.local`):

- `NEXT_PUBLIC_FIREBASE_API_KEY`
- `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`
- `NEXT_PUBLIC_FIREBASE_PROJECT_ID`
- `NEXT_PUBLIC_FIREBASE_APP_ID`
- `NEXT_PUBLIC_DEV_USER_ID` — optional local-only fallback. When Firebase env vars are absent and this is set, the frontend skips the sign-in gate and sends `X-User-Id: <this value>` instead of a Bearer token. Pair with backend `ALLOW_DEV_AUTH=1`.

Notes:

- `Connect New` now uses real OAuth redirects for all platforms.
- Account discovery uses live provider APIs and saved OAuth tokens.
- Sync endpoints fail fast with explicit credential/integration errors instead of returning mock data.
- Set a stable `ENCRYPTION_KEY` before connecting accounts; if it changes later, stored tokens become undecryptable and those connections must be reconnected.
- If `ENCRYPTION_KEY` is omitted in local development, backend now creates and reuses `backend/.local_encryption_key` automatically.
- You can still open the app locally without `backend/.env`; only real OAuth/account sync flows require those credentials.

## Database

Default: local SQLite at `backend/antigravity.db`. Override with `DATABASE_URL`:

```bash
# Postgres (Cloud SQL, local docker, etc.)
export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
```

The model layer uses a portable JSON type: SQLite gets `JSON`, Postgres gets `JSONB` automatically — no app code changes needed when switching.

### Local Postgres via docker-compose

`docker compose up postgres` brings up a Postgres 16 instance on `:5432`. The compose-managed `backend` service is preconfigured to connect to it.

### Migrations (Alembic)

Schema changes live under `backend/migrations/versions/`. On startup, `main.py` runs `alembic upgrade head` against the configured `DATABASE_URL`. If it detects a pre-Alembic SQLite DB (tables present, no `alembic_version`), it stamps the existing schema at head first so local data is preserved.

Manual commands (run from `backend/` with the venv active):

```bash
alembic upgrade head                              # apply pending migrations
alembic revision --autogenerate -m "add foo"      # generate a new revision from model diffs
alembic stamp head                                # mark current DB as up-to-date without running migrations
alembic downgrade -1                              # roll back one revision
```

`autogenerate` diffs `app.models.Base.metadata` against the live DB — always review the generated file before committing.

## Local Run

Use `localhost` consistently for both services. The frontend defaults to calling `http://localhost:8000`, and the OAuth callback examples also assume `localhost`.

### 1) Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./.venv/bin/python -m uvicorn main:app --host localhost --port 8000
```

Backend health:

```bash
curl http://localhost:8000/
```

Expected response:

```json
{"message":"Antigravity API is running"}
```

### 2) Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev -- --hostname localhost --port 3000
```

If you are using `nvm`, switch to a supported runtime first:

```bash
nvm use 22 || nvm install 22
npm install
npm run dev -- --hostname localhost --port 3000
```

If you just changed Node versions, reinstall frontend dependencies once under Node `22`:

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run dev -- --hostname localhost --port 3000
```

Wait for the Next.js dev server to finish booting before opening the app. On a cold start it can take a few seconds before port `3000` begins accepting requests.

Frontend:

```bash
open http://localhost:3000
```

If the page does not load immediately, retry after the terminal shows the dev server is ready.

## Quick Access Check

After both commands are running, these two URLs should work:

- Backend API: `http://localhost:8000/`
- Frontend app: `http://localhost:3000/`

If `http://localhost:8000/` works but `http://localhost:3000/` does not, the frontend is still starting or the `npm run dev` command did not stay running.

## Troubleshooting

`address already in use` on port `8000`:

- This usually means the backend is already running.
- Verify with `curl http://localhost:8000/` before starting a second copy.

Frontend opens but cannot reach backend:

- Make sure the backend was started with `--host localhost --port 8000`.
- Keep the frontend on `http://localhost:3000` so it matches the frontend and OAuth defaults used by the app.

Frontend dev server starts but browser never loads:

- Check `node -v` inside `frontend/`.
- If you are on Node `25.x`, switch to Node `22.x` and reinstall frontend dependencies.
- After switching versions, run `rm -rf node_modules package-lock.json && npm install` only if the existing install still behaves incorrectly.

Frontend says port `3000` is already in use:

- That usually means an older Next.js dev server is still running.
- Stop the old process, then rerun `npm run dev -- --hostname localhost --port 3000`.
- If you only need to confirm the app boots, you can also start it temporarily on another port such as `3001`.

OAuth redirect problems:

- Set `OAUTH_REDIRECT_URI=http://localhost:8000/api/auth/callback`
- Set `FRONTEND_URL=http://localhost:3000`
- Use the same host everywhere; do not mix `localhost` and `127.0.0.1`.

## Smoke Test (API)

Create a mock connection and trigger sync:

```bash
base="http://localhost:8000/api"
conn=$(curl -sS -X POST "$base/connections?platform=google&account_name=Smoke%20Test")
id=$(echo "$conn" | sed -E 's/.*"id":([0-9]+).*/\1/')
curl -sS -X POST "$base/sync/$id"
curl -sS "$base/reports"
```

## Deploying to Cloud Run

The backend `Dockerfile` is Cloud Run-ready: binds to `$PORT`, runs a single uvicorn worker (SSE-friendly), uses `--proxy-headers` so `X-Forwarded-*` from the Cloud Run proxy reaches FastAPI. `.dockerignore` keeps secrets, the local SQLite DB, and the venv out of the image.

### Build + deploy

```bash
gcloud run deploy antigravity-api \
  --source ./backend \
  --region us-central1 \
  --port 8000 \
  --allow-unauthenticated \                      # Firebase Auth gates real users
  --no-cpu-throttling \                          # SSE needs always-on CPU
  --timeout 3600 \                               # match max SSE chat duration
  --concurrency 80 \                             # each request is one open SSE
  --set-env-vars DATABASE_URL=...,GCP_PROJECT_ID=... \
  --set-secrets KMS_KEY_NAME=projects/.../cryptoKeys/...
```

Health probes:

- Liveness: `GET /healthz` (cheap, no DB).
- Readiness: `GET /readyz` (runs `SELECT 1` against the configured DB).

### Background sync via Cloud Tasks

Long-running platform syncs should not block the request path. The backend ships a queue abstraction (`app/services/task_queue.py`) with two backends:

- **Local**: `asyncio.create_task` fire-and-forget. No setup; runs in the same uvicorn process.
- **Cloud Tasks**: when `CLOUD_TASKS_QUEUE`, `CLOUD_TASKS_WORKER_URL`, and `CLOUD_TASKS_INVOKER_SA` are set, `enqueue(...)` creates an HTTP task that Cloud Tasks delivers (with an OIDC token) to `/api/internal/tasks/run` on this same Cloud Run service.

Required Cloud Tasks env vars on the backend:

- `CLOUD_TASKS_QUEUE` — full queue resource path, e.g. `projects/p/locations/us-central1/queues/sync`.
- `CLOUD_TASKS_WORKER_URL` — the Cloud Run service URL, e.g. `https://antigravity-api-xxx.run.app`.
- `CLOUD_TASKS_INVOKER_SA` — service account email Cloud Tasks signs with; must match the OIDC `email` claim verified by `api/internal.py`.
- `CLOUD_TASKS_OIDC_AUDIENCE` (optional) — defaults to `CLOUD_TASKS_WORKER_URL`.

Grant the invoker SA `roles/run.invoker` on the service so Cloud Tasks can hit `/api/internal/tasks/run`. The endpoint verifies the OIDC token against `CLOUD_TASKS_OIDC_AUDIENCE` and rejects any other signer.

Local-only escape hatch: `ALLOW_INTERNAL_NO_OIDC=1` skips OIDC verification on `/api/internal/tasks/run` so dev/test code can hit it directly. Never set in production.

Task handlers live in `app/services/task_handlers.py`. Register a new one with `@register_handler("name")` and import it (the file is auto-imported at startup). Enqueue with `await enqueue("name", payload)`.

## GitHub Actions CI

The CI workflow runs on pushes and pull requests to `main`:

- Backend dependency install + compile check
- Frontend install + lint + typecheck

## Notes

- Local OAuth/token scratch files are intentionally ignored via `.gitignore`.
- SQLite schema compatibility for older local DB files is handled at backend startup.

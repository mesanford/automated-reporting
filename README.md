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

Notes:

- `Connect New` now uses real OAuth redirects for all platforms.
- Account discovery uses live provider APIs and saved OAuth tokens.
- Sync endpoints fail fast with explicit credential/integration errors instead of returning mock data.
- Set a stable `ENCRYPTION_KEY` before connecting accounts; if it changes later, stored tokens become undecryptable and those connections must be reconnected.
- If `ENCRYPTION_KEY` is omitted in local development, backend now creates and reuses `backend/.local_encryption_key` automatically.
- You can still open the app locally without `backend/.env`; only real OAuth/account sync flows require those credentials.

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

## GitHub Actions CI

The CI workflow runs on pushes and pull requests to `main`:

- Backend dependency install + compile check
- Frontend install + lint + typecheck

## Notes

- Local OAuth/token scratch files are intentionally ignored via `.gitignore`.
- SQLite schema compatibility for older local DB files is handled at backend startup.

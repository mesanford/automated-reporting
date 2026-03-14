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
- Node.js 20+
- npm 10+

## Local Run

### 1) Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Backend health:

```bash
curl http://127.0.0.1:8000/
```

### 2) Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Frontend:

```bash
open http://127.0.0.1:3000
```

## Smoke Test (API)

Create a mock connection and trigger sync:

```bash
base="http://127.0.0.1:8000/api"
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

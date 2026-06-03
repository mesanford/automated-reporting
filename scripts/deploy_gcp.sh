#!/usr/bin/env bash
#
# Provision + deploy the backend to GCP.
#
# Designed to be re-runnable. Each phase checks for existing resources
# before creating, and falls through to update where possible. Anything
# that can't be idempotent (secret values, DB password) prompts.
#
# What it does NOT do:
# - Deploy the Next.js frontend. App Hosting is interactive enough that
#   `firebase init apphosting` + the console is the path of least
#   resistance. Run that separately, then come back here for the API.
# - Run database backfills. Once the API boots, the Alembic chain runs
#   automatically. You may want to load production OAuth refresh tokens
#   under the new KMS scheme — see OPERATIONS.md "Rotating the KEK".
# - Configure Firebase Auth providers. Enable Google/Email in the
#   Firebase console once; the script doesn't reach in.
#
# Usage:
#   PROJECT_ID=my-gcp-project FRONTEND_URL=https://app.example.com \
#     ./scripts/deploy_gcp.sh
#
#   ./scripts/deploy_gcp.sh --dry-run        # print intended commands
#   ./scripts/deploy_gcp.sh --skip-sql       # skip a phase
#   ./scripts/deploy_gcp.sh --only-deploy    # just rebuild + redeploy

set -euo pipefail

# ── CONFIG ──────────────────────────────────────────────────────────────────

: "${PROJECT_ID:?PROJECT_ID is required. Example: PROJECT_ID=my-project ./scripts/deploy_gcp.sh}"
: "${FRONTEND_URL:?FRONTEND_URL is required (used for CORS + invite accept links).}"

REGION="${REGION:-us-central1}"
PREFIX="${PREFIX:-antigravity}"

DB_INSTANCE="${DB_INSTANCE:-${PREFIX}-pg}"
DB_TIER="${DB_TIER:-db-f1-micro}"
DB_NAME="${DB_NAME:-${PREFIX}}"
DB_USER="${DB_USER:-${PREFIX}}"
# DB_PASSWORD prompted below if missing.

KMS_LOCATION="${KMS_LOCATION:-global}"
KMS_KEYRING="${KMS_KEYRING:-${PREFIX}-keyring}"
KMS_KEY="${KMS_KEY:-${PREFIX}-token-key}"

TASKS_QUEUE="${TASKS_QUEUE:-${PREFIX}-sync}"
TASKS_LOCATION="${TASKS_LOCATION:-${REGION}}"

SERVICE_NAME="${SERVICE_NAME:-${PREFIX}-api}"
RUNTIME_SA="${RUNTIME_SA:-${PREFIX}-runtime}"     # the Cloud Run service identity
INVOKER_SA="${INVOKER_SA:-${PREFIX}-invoker}"     # Cloud Tasks + Scheduler caller

BACKEND_DIR="${BACKEND_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backend}"

DRY_RUN=0
SKIP_PHASES=()
ONLY_DEPLOY=0

# ── FLAGS ──────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=1; shift ;;
    --skip-apis)    SKIP_PHASES+=("apis"); shift ;;
    --skip-sql)     SKIP_PHASES+=("sql"); shift ;;
    --skip-kms)     SKIP_PHASES+=("kms"); shift ;;
    --skip-iam)     SKIP_PHASES+=("iam"); shift ;;
    --skip-secrets) SKIP_PHASES+=("secrets"); shift ;;
    --skip-deploy)  SKIP_PHASES+=("deploy"); shift ;;
    --skip-tasks)   SKIP_PHASES+=("tasks"); shift ;;
    --skip-scheduler) SKIP_PHASES+=("scheduler"); shift ;;
    --skip-verify)  SKIP_PHASES+=("verify"); shift ;;
    --only-deploy)  ONLY_DEPLOY=1; shift ;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [[ "$ONLY_DEPLOY" == "1" ]]; then
  SKIP_PHASES=("apis" "sql" "kms" "iam" "secrets" "tasks" "scheduler")
fi

skipped() { local p; for p in "${SKIP_PHASES[@]:-}"; do [[ "$p" == "$1" ]] && return 0; done; return 1; }

# ── HELPERS ────────────────────────────────────────────────────────────────

c_blue=$'\e[34m'; c_grey=$'\e[90m'; c_green=$'\e[32m'; c_yellow=$'\e[33m'; c_reset=$'\e[0m'

say()   { echo "${c_blue}==>${c_reset} $*"; }
note()  { echo "${c_grey}    $*${c_reset}"; }
ok()    { echo "${c_green}  ✓${c_reset} $*"; }
warn()  { echo "${c_yellow}  !${c_reset} $*" >&2; }

require_cmd() {
  command -v "$1" >/dev/null || { echo "Missing command: $1" >&2; exit 1; }
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  ${c_grey}\$${c_reset} $*"
  else
    "$@"
  fi
}

# Run a command that may fail "harmlessly" (e.g. resource already exists)
# and downgrade non-zero to a warning instead of aborting.
run_idempotent() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  ${c_grey}\$${c_reset} $* ${c_grey}(idempotent)${c_reset}"
    return 0
  fi
  if "$@" 2> >(tee /tmp/.gcdeploy.err >&2); then
    return 0
  fi
  if grep -qE "already exists|ALREADY_EXISTS|currently exists" /tmp/.gcdeploy.err; then
    warn "already exists, continuing"
    return 0
  fi
  return 1
}

prompt_secret() {
  local name="$1" value
  read -rsp "  ${name}: " value; echo
  printf '%s' "$value"
}

# ── PRE-FLIGHT ──────────────────────────────────────────────────────────────

require_cmd gcloud
require_cmd jq
require_cmd curl
require_cmd openssl

say "Project: $PROJECT_ID  Region: $REGION  Prefix: $PREFIX"
note "Frontend URL: $FRONTEND_URL"
note "Backend dir: $BACKEND_DIR"
if [[ "$DRY_RUN" == "1" ]]; then
  warn "DRY RUN — printing commands without executing."
fi

run gcloud config set project "$PROJECT_ID" >/dev/null

# Derived
RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA_EMAIL="${INVOKER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
DB_CONN="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
KMS_KEY_FULL="projects/${PROJECT_ID}/locations/${KMS_LOCATION}/keyRings/${KMS_KEYRING}/cryptoKeys/${KMS_KEY}"
TASKS_QUEUE_FULL="projects/${PROJECT_ID}/locations/${TASKS_LOCATION}/queues/${TASKS_QUEUE}"

# ── PHASE 1: APIs ──────────────────────────────────────────────────────────

if ! skipped apis; then
  say "Phase 1/8 — enabling APIs"
  run gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    cloudkms.googleapis.com \
    cloudtasks.googleapis.com \
    cloudscheduler.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    iam.googleapis.com \
    --project "$PROJECT_ID"
  ok "APIs enabled"
fi

# ── PHASE 2: Cloud SQL ─────────────────────────────────────────────────────

if ! skipped sql; then
  say "Phase 2/8 — Cloud SQL (Postgres)"

  if [[ -z "${DB_PASSWORD:-}" ]]; then
    # Generate a strong password if the user didn't bring one. They can
    # rotate via `gcloud sql users set-password` later.
    DB_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
    note "Generated DB_PASSWORD (also written to Secret Manager below)"
  fi

  if ! gcloud sql instances describe "$DB_INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
    run gcloud sql instances create "$DB_INSTANCE" \
      --database-version=POSTGRES_16 \
      --tier="$DB_TIER" \
      --region="$REGION" \
      --root-password="$(openssl rand -base64 32)" \
      --project "$PROJECT_ID"
  else
    ok "instance $DB_INSTANCE already exists"
  fi

  run_idempotent gcloud sql databases create "$DB_NAME" \
    --instance "$DB_INSTANCE" --project "$PROJECT_ID"

  run_idempotent gcloud sql users create "$DB_USER" \
    --instance "$DB_INSTANCE" --password "$DB_PASSWORD" \
    --project "$PROJECT_ID"

  ok "Cloud SQL ready: $DB_CONN"
fi

# ── PHASE 3: KMS ───────────────────────────────────────────────────────────

if ! skipped kms; then
  say "Phase 3/8 — KMS keyring + token-encryption key"

  run_idempotent gcloud kms keyrings create "$KMS_KEYRING" \
    --location "$KMS_LOCATION" --project "$PROJECT_ID"

  run_idempotent gcloud kms keys create "$KMS_KEY" \
    --keyring "$KMS_KEYRING" --location "$KMS_LOCATION" \
    --purpose=encryption --project "$PROJECT_ID"

  ok "KMS key: $KMS_KEY_FULL"
fi

# ── PHASE 4: IAM ───────────────────────────────────────────────────────────

if ! skipped iam; then
  say "Phase 4/8 — service accounts + IAM bindings"

  run_idempotent gcloud iam service-accounts create "$RUNTIME_SA" \
    --display-name "${PREFIX} runtime" --project "$PROJECT_ID"
  run_idempotent gcloud iam service-accounts create "$INVOKER_SA" \
    --display-name "${PREFIX} Cloud Tasks + Scheduler invoker" --project "$PROJECT_ID"

  # Runtime SA: read secrets, decrypt KMS, read/write Cloud SQL, send Cloud Tasks.
  for role in \
    roles/secretmanager.secretAccessor \
    roles/cloudkms.cryptoKeyEncrypterDecrypter \
    roles/cloudsql.client \
    roles/cloudtasks.enqueuer \
    roles/logging.logWriter \
    roles/monitoring.metricWriter; do
    run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member "serviceAccount:${RUNTIME_SA_EMAIL}" \
      --role "$role" --condition=None >/dev/null
  done

  ok "IAM bindings applied (invoker SA gets run.invoker after deploy)"
fi

# ── PHASE 5: Secrets ───────────────────────────────────────────────────────

upsert_secret() {
  local name="$1" value="$2"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  ${c_grey}\$${c_reset} gcloud secrets create $name --replication-policy=automatic --project $PROJECT_ID"
    echo "  ${c_grey}\$${c_reset} echo '...' | gcloud secrets versions add $name --data-file=- --project $PROJECT_ID"
    return 0
  fi
  if ! gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic --project "$PROJECT_ID" >/dev/null
  fi
  printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT_ID" >/dev/null
  ok "secret $name → new version"
}

prompt_or_skip_secret() {
  local name="$1" optional="${2:-required}"
  local existing=""
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    ok "secret $name already configured (re-prompt to rotate)"
    read -p "    rotate $name? [y/N] " yn
    [[ "${yn:-N}" =~ ^[Yy]$ ]] || return 0
  fi
  local value
  value="$(prompt_secret "$name (${optional}, ENTER to skip)")"
  if [[ -z "$value" ]]; then
    [[ "$optional" == "optional" ]] && return 0
    warn "skipped required secret $name"
    return 0
  fi
  upsert_secret "$name" "$value"
}

if ! skipped secrets; then
  say "Phase 5/8 — Secret Manager"
  note "Prompting for secrets. ENTER to skip optional ones."
  note "Existing secrets are left alone unless you opt to rotate."

  # Always-rotated: DB password (the value the API uses to talk to Cloud SQL).
  if [[ -n "${DB_PASSWORD:-}" ]]; then
    upsert_secret "DB_PASSWORD" "$DB_PASSWORD"
  fi

  # Bootstrap token-encryption secret (HMAC for OAuth signed state + Fernet
  # fallback if KMS becomes unreachable). Generate if missing.
  if ! gcloud secrets describe ENCRYPTION_KEY --project "$PROJECT_ID" >/dev/null 2>&1; then
    upsert_secret "ENCRYPTION_KEY" "$(openssl rand -base64 32)"
  else
    ok "secret ENCRYPTION_KEY already configured"
  fi

  prompt_or_skip_secret GOOGLE_API_KEY            # Gemini
  prompt_or_skip_secret SENDGRID_API_KEY optional
  prompt_or_skip_secret INVITE_FROM_EMAIL optional
  prompt_or_skip_secret GOOGLE_ADS_CLIENT_ID
  prompt_or_skip_secret GOOGLE_ADS_CLIENT_SECRET
  prompt_or_skip_secret GOOGLE_ADS_DEVELOPER_TOKEN
  prompt_or_skip_secret META_CLIENT_ID optional
  prompt_or_skip_secret META_CLIENT_SECRET optional
  prompt_or_skip_secret LINKEDIN_CLIENT_ID optional
  prompt_or_skip_secret LINKEDIN_CLIENT_SECRET optional
  prompt_or_skip_secret TIKTOK_CLIENT_ID optional
  prompt_or_skip_secret TIKTOK_CLIENT_SECRET optional
  prompt_or_skip_secret MICROSOFT_CLIENT_ID optional
  prompt_or_skip_secret MICROSOFT_CLIENT_SECRET optional
  prompt_or_skip_secret MICROSOFT_DEVELOPER_TOKEN optional
fi

# ── PHASE 6: Cloud Run deploy ──────────────────────────────────────────────

OAUTH_REDIRECT_URI="${OAUTH_REDIRECT_URI:-}"  # set after we know the service URL

deploy_run() {
  say "Phase 6/8 — Cloud Run deploy ($SERVICE_NAME)"

  # First deploy or redeploy. Service URL is needed for OAUTH_REDIRECT_URI
  # and CLOUD_TASKS_WORKER_URL, so we deploy once to get the URL, then
  # update env to point things at it. Re-running this script after the
  # first deploy is a single pass.
  local existing_url=""
  if gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
    existing_url="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" \
      --project "$PROJECT_ID" --format='value(status.url)')"
    note "existing service URL: $existing_url"
  fi

  local effective_url="${existing_url:-https://${SERVICE_NAME}-placeholder.run.app}"
  : "${OAUTH_REDIRECT_URI:=${effective_url}/api/auth/callback}"

  local env_vars=(
    "APP_ENV=production"
    "GCP_PROJECT_ID=${PROJECT_ID}"
    "KMS_KEY_NAME=${KMS_KEY_FULL}"
    "DATABASE_URL=postgresql+pg8000://${DB_USER}@/${DB_NAME}?unix_sock=/cloudsql/${DB_CONN}/.s.PGSQL.5432"
    "CORS_ALLOWED_ORIGINS=${FRONTEND_URL}"
    "FRONTEND_URL=${FRONTEND_URL}"
    "OAUTH_REDIRECT_URI=${OAUTH_REDIRECT_URI}"
    "CLOUD_TASKS_QUEUE=${TASKS_QUEUE_FULL}"
    "CLOUD_TASKS_WORKER_URL=${effective_url}"
    "CLOUD_TASKS_INVOKER_SA=${INVOKER_SA_EMAIL}"
    "CLOUD_TASKS_OIDC_AUDIENCE=${effective_url}"
  )

  # Secrets pulled at boot via Secret Manager mounts. Pulling them as env
  # vars (rather than via the runtime client) means a missing secret
  # fails the deploy rather than failing the first request.
  local secret_envs=(
    "ENCRYPTION_KEY=ENCRYPTION_KEY:latest"
    "DB_PASSWORD=DB_PASSWORD:latest"
    "GOOGLE_API_KEY=GOOGLE_API_KEY:latest"
  )

  run gcloud run deploy "$SERVICE_NAME" \
    --source "$BACKEND_DIR" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --service-account "$RUNTIME_SA_EMAIL" \
    --no-cpu-throttling \
    --timeout 3600 \
    --concurrency 80 \
    --min-instances 1 \
    --add-cloudsql-instances "$DB_CONN" \
    --allow-unauthenticated \
    --set-env-vars "$(IFS=,; echo "${env_vars[*]}")" \
    --set-secrets "$(IFS=,; echo "${secret_envs[*]}")"

  if [[ "$DRY_RUN" != "1" ]]; then
    SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" \
      --project "$PROJECT_ID" --format='value(status.url)')"
    ok "service URL: $SERVICE_URL"

    # If this was the first deploy, redeploy with the *real* URL so the
    # OAuth callback + Cloud Tasks worker URL match the live service.
    if [[ "$effective_url" != "$SERVICE_URL" ]]; then
      note "URL changed; redeploying with correct OAuth + Tasks URLs"
      env_vars_real=(
        "${env_vars[@]/${effective_url}/${SERVICE_URL}}"
      )
      gcloud run services update "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" \
        --update-env-vars "OAUTH_REDIRECT_URI=${SERVICE_URL}/api/auth/callback,CLOUD_TASKS_WORKER_URL=${SERVICE_URL},CLOUD_TASKS_OIDC_AUDIENCE=${SERVICE_URL}" \
        >/dev/null
      ok "second-pass env update applied"
    fi
  else
    SERVICE_URL="$effective_url"
  fi
}

if ! skipped deploy; then
  deploy_run
fi

# ── PHASE 7: Cloud Tasks + Cloud Run invoker binding ───────────────────────

if ! skipped tasks; then
  say "Phase 7/8 — Cloud Tasks queue + run.invoker"

  run_idempotent gcloud tasks queues create "$TASKS_QUEUE" \
    --location "$TASKS_LOCATION" --project "$PROJECT_ID"

  # Cloud Tasks signs requests as INVOKER_SA. Grant it permission to hit
  # the (now-deployed) Cloud Run service.
  run gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --region "$REGION" --project "$PROJECT_ID" \
    --member "serviceAccount:${INVOKER_SA_EMAIL}" \
    --role roles/run.invoker >/dev/null

  ok "queue $TASKS_QUEUE_FULL ready"
fi

# ── PHASE 8: Cloud Scheduler (sync + digest ticks) ─────────────────────────

if ! skipped scheduler; then
  say "Phase 8/8 — Cloud Scheduler jobs (sync + digest ticks)"

  : "${SERVICE_URL:=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" \
                       --project "$PROJECT_ID" --format='value(status.url)' 2>/dev/null \
                     || echo "https://${SERVICE_NAME}-placeholder.run.app")}"

  schedule_or_update() {
    local job_name="$1" path="$2"
    local uri="${SERVICE_URL}${path}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "  ${c_grey}\$${c_reset} gcloud scheduler jobs create-or-update http $job_name --location $REGION --uri $uri ..."
      return
    fi
    if gcloud scheduler jobs describe "$job_name" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
      gcloud scheduler jobs update http "$job_name" \
        --location "$REGION" --project "$PROJECT_ID" \
        --schedule="*/5 * * * *" \
        --uri="$uri" \
        --http-method=POST \
        --oidc-service-account-email="$INVOKER_SA_EMAIL" \
        --oidc-token-audience="$SERVICE_URL" >/dev/null
      ok "updated scheduler $job_name → $uri"
    else
      gcloud scheduler jobs create http "$job_name" \
        --location "$REGION" --project "$PROJECT_ID" \
        --schedule="*/5 * * * *" \
        --uri="$uri" \
        --http-method=POST \
        --oidc-service-account-email="$INVOKER_SA_EMAIL" \
        --oidc-token-audience="$SERVICE_URL" >/dev/null
      ok "created scheduler $job_name → $uri"
    fi
  }

  schedule_or_update "${PREFIX}-sync-tick" "/api/internal/scheduler/tick"
  schedule_or_update "${PREFIX}-digest-tick" "/api/internal/digests/tick"
fi

# ── VERIFY ────────────────────────────────────────────────────────────────

if ! skipped verify; then
  say "Verify — /readyz subsystem report"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  ${c_grey}\$${c_reset} curl -fsS \"\$SERVICE_URL/readyz\" | jq"
  else
    if curl -fsS "${SERVICE_URL}/readyz" | jq .; then
      ok "Live."
      note "Expected subsystems: AUTH_BACKEND=firebase, SECRETS_BACKEND=secret-manager,"
      note "TOKEN_ENCRYPTION=kms-envelope, EMAIL_BACKEND=sendgrid (or noop), TASK_QUEUE=cloud-tasks"
      note ""
      note "If you see warnings about Firebase, finish the Firebase Auth setup:"
      note "  1. Create / link a Firebase project to ${PROJECT_ID}"
      note "  2. Enable Google sign-in provider in the Firebase console"
      note "  3. Add a Web app, copy the config into frontend/.env.local as"
      note "     NEXT_PUBLIC_FIREBASE_API_KEY etc."
      note "  4. Mount a service-account JSON via GOOGLE_APPLICATION_CREDENTIALS"
      note "     or rely on Application Default Credentials on Cloud Run."
    else
      warn "readyz check failed — inspect Cloud Run logs"
    fi
  fi
fi

echo
ok "Done."
echo "Frontend: deploy via \`firebase deploy --only apphosting\` or another Cloud Run service."
echo "Service URL: ${SERVICE_URL:-(unknown — re-run with --only-deploy after first deploy completes)}"

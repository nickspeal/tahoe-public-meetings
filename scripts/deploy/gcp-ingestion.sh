#!/usr/bin/env bash
# Deploy the Tahoe Public Meetings ingestion job to Google Cloud.
#
# Idempotent: every step describes the resource before creating it, so the
# script can be rerun after a failure or to roll out a new image. It also sets
# up a monthly budget and a budget guard that pauses the scheduler once the
# budget is spent (a delayed safety stop, not an exact cap).
# Runbook: docs/operations/deployment.md
#
# Usage:
#   scripts/deploy/gcp-ingestion.sh [deploy] [--yes] [--run-now] [--cloud-build] [--skip-build]
#   scripts/deploy/gcp-ingestion.sh set-secret database-url|vercel-deploy-hook-url
#   scripts/deploy/gcp-ingestion.sh run-now [--yes]
#
# Secret values are never accepted as arguments or printed. They are read from
# a hidden terminal prompt, or from stdin for `set-secret`, and piped straight
# to `gcloud secrets versions add --data-file=-`.

set -euo pipefail

# ---------------------------------------------------------------- settings
PROJECT_ID="${PROJECT_ID:-tahoe-public-meetings}"
PROJECT_NAME="${PROJECT_NAME:-Tahoe Public Meetings}"
REGION="${REGION:-us-west1}"                  # Oregon: Tier 1 pricing, closest Tier 1 region to Tahoe
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"        # only needed to create/link a project or create the budget
AR_REPO="${AR_REPO:-tahoe}"
IMAGE_NAME="${IMAGE_NAME:-ingestion}"
IMAGE_TAG="${IMAGE_TAG:-}"                    # default: git short SHA
KEEP_IMAGES="${KEEP_IMAGES:-3}"
JOB_NAME="${JOB_NAME:-tahoe-ingest}"
JOB_SA_NAME="${JOB_SA_NAME:-tahoe-ingest-job}"
SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-tahoe-ingest-scheduler}"
SCHEDULER_JOB="${SCHEDULER_JOB:-tahoe-ingest-every-6h}"
SCHEDULE="${SCHEDULE:-0 */6 * * *}"
TIME_ZONE="${TIME_ZONE:-America/Los_Angeles}"
JOB_MEMORY="${JOB_MEMORY:-2Gi}"
JOB_CPU="${JOB_CPU:-1}"
TASK_TIMEOUT="${TASK_TIMEOUT:-3600s}"
MAX_RETRIES="${MAX_RETRIES:-1}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-5}"           # USD per month for this project
BUDGET_NAME="${BUDGET_NAME:-tahoe-public-meetings-monthly}"
BUDGET_GUARD="${BUDGET_GUARD:-1}"             # 1: pause the scheduler when cost >= budget
BUDGET_TOPIC="${BUDGET_TOPIC:-tahoe-budget-alerts}"
GUARD_NAME="${GUARD_NAME:-tahoe-budget-guard}"  # Cloud Run service, service account, and image name
GUARD_SUBSCRIPTION="${GUARD_SUBSCRIPTION:-tahoe-budget-guard-push}"
GUARD_ROLE_ID="${GUARD_ROLE_ID:-tahoeSchedulerPauser}"
ALERT_EMAIL="${ALERT_EMAIL:-}"                # operator's address; alerts are skipped when empty
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
GCLOUD="${GCLOUD:-gcloud}"
# Claude via workload identity federation (no API key): IDs from the Claude Console; none are secret.
ANTHROPIC_FEDERATION_RULE_ID="${ANTHROPIC_FEDERATION_RULE_ID:-}"
ANTHROPIC_ORGANIZATION_ID="${ANTHROPIC_ORGANIZATION_ID:-}"
ANTHROPIC_SERVICE_ACCOUNT_ID="${ANTHROPIC_SERVICE_ACCOUNT_ID:-}"
ANTHROPIC_WORKSPACE_ID="${ANTHROPIC_WORKSPACE_ID:-}"

SECRET_DATABASE_URL="tahoe-database-url"
SECRET_DEPLOY_HOOK="tahoe-vercel-deploy-hook-url"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AR_HOST="${REGION}-docker.pkg.dev"
IMAGE_BASE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/${IMAGE_NAME}"
JOB_SA="${JOB_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GUARD_SA="${GUARD_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GUARD_IMAGE_BASE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/${GUARD_NAME}"
BUDGET_ALERT_PUBLISHER="serviceAccount:billing-budget-alert@system.gserviceaccount.com"

ASSUME_YES=0
RUN_NOW=0
USE_CLOUD_BUILD=0
SKIP_BUILD=0
COMMAND="deploy"
SECRET_ARG=""
TMP_DIR=""

# ---------------------------------------------------------------- helpers
log()  { printf '\n==> %s\n' "$*" >&2; }
info() { printf '    %s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

g() { "$GCLOUD" --project="$PROJECT_ID" --quiet "$@"; }

cleanup() { if [[ -n "$TMP_DIR" ]]; then rm -rf "$TMP_DIR"; fi; }
trap cleanup EXIT

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

parse_args() {
  while (($#)); do
    case "$1" in
      deploy|run-now) COMMAND="$1" ;;
      set-secret) COMMAND="set-secret"; SECRET_ARG="${2:-}"; shift || true ;;
      --yes|-y) ASSUME_YES=1 ;;
      --run-now) RUN_NOW=1 ;;
      --cloud-build) USE_CLOUD_BUILD=1 ;;
      --skip-build) SKIP_BUILD=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown argument: $1 (secret values are never accepted as arguments)" ;;
    esac
    shift
  done
}

secret_id_for() {
  case "$1" in
    database-url) echo "$SECRET_DATABASE_URL" ;;
    vercel-deploy-hook-url) echo "$SECRET_DEPLOY_HOOK" ;;
    *) die "unknown secret '$1'; use database-url or vercel-deploy-hook-url" ;;
  esac
}

# ---------------------------------------------------------------- identity
check_identity() {
  log "Checking gcloud identity"
  command -v "$GCLOUD" >/dev/null || die "gcloud not found; set GCLOUD=/path/to/gcloud"
  local account
  account="$("$GCLOUD" auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)"
  [[ -n "$account" ]] || die "no active gcloud account; run: gcloud auth login"
  "$GCLOUD" auth print-access-token >/dev/null 2>&1 \
    || die "credentials for $account are expired; run: gcloud auth login"
  info "active account: $account"
  info "project:        $PROJECT_ID"
  info "region:         $REGION"
  info "job:            $JOB_NAME ($JOB_CPU vCPU, $JOB_MEMORY, timeout $TASK_TIMEOUT, retries $MAX_RETRIES)"
  info "schedule:       '$SCHEDULE' $TIME_ZONE"
  info "billing:        ${BILLING_ACCOUNT:-<from project link>}"
  if ((ASSUME_YES)); then return; fi
  [[ -t 0 ]] || die "not a terminal; pass --yes after verifying the account and project above"
  local reply
  read -r -p "Type the project ID to confirm this account and project: " reply </dev/tty
  [[ "$reply" == "$PROJECT_ID" ]] || die "confirmation did not match; nothing changed"
}

# ---------------------------------------------------------------- project
ensure_project() {
  log "Project $PROJECT_ID"
  if "$GCLOUD" projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
    info "exists"
  else
    [[ -n "$BILLING_ACCOUNT" ]] || die "project not found or not accessible; set BILLING_ACCOUNT to create it"
    "$GCLOUD" projects create "$PROJECT_ID" --name="$PROJECT_NAME" --quiet \
      || die "could not create $PROJECT_ID (project IDs are global; choose another PROJECT_ID)"
    info "created"
  fi

  local enabled linked
  enabled="$("$GCLOUD" billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)"
  linked="$("$GCLOUD" billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null || true)"
  if [[ "$enabled" == [Tt]rue ]]; then
    info "billing linked: ${linked#billingAccounts/}"
    if [[ -n "$BILLING_ACCOUNT" && "${linked#billingAccounts/}" != "$BILLING_ACCOUNT" ]]; then
      die "project is linked to ${linked#billingAccounts/}, not BILLING_ACCOUNT=$BILLING_ACCOUNT; resolve before continuing"
    fi
    BILLING_ACCOUNT="${linked#billingAccounts/}"
  else
    [[ -n "$BILLING_ACCOUNT" ]] || die "billing is not enabled on $PROJECT_ID; set BILLING_ACCOUNT"
    "$GCLOUD" billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" --quiet >/dev/null
    info "billing linked: $BILLING_ACCOUNT"
  fi
}

enable_apis() {
  log "Enabling required APIs"
  local apis=(
    run.googleapis.com
    artifactregistry.googleapis.com
    cloudscheduler.googleapis.com
    secretmanager.googleapis.com
    iam.googleapis.com
    logging.googleapis.com
    monitoring.googleapis.com
    billingbudgets.googleapis.com
  )
  if ((BUDGET_GUARD)); then apis+=(pubsub.googleapis.com); fi
  if ((USE_CLOUD_BUILD)); then apis+=(cloudbuild.googleapis.com); fi
  g services enable "${apis[@]}"
  info "${apis[*]}"
}

# ---------------------------------------------------------------- registry and image
ensure_registry() {
  log "Artifact Registry repository $AR_REPO ($REGION)"
  if g artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1; then
    info "exists"
  else
    g artifacts repositories create "$AR_REPO" --location="$REGION" \
      --repository-format=docker --description="Tahoe Public Meetings ingestion images"
    info "created"
  fi
  # Keep only the most recent images so storage stays inside the 0.5 GB free tier.
  local policy="$TMP_DIR/cleanup-policy.json"
  cat >"$policy" <<JSON
[
  {"name": "keep-recent", "action": {"type": "Keep"},
   "mostRecentVersions": {"packageNamePrefixes": ["${IMAGE_NAME}", "${GUARD_NAME}"], "keepCount": ${KEEP_IMAGES}}},
  {"name": "delete-older", "action": {"type": "Delete"},
   "condition": {"tagState": "any", "olderThan": "1d"}}
]
JSON
  g artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" \
    --policy="$policy" --no-dry-run >/dev/null
  info "cleanup policy: keep newest $KEEP_IMAGES per image, delete older images after 1 day"
}

resolve_tag() {
  if [[ -n "$IMAGE_TAG" ]]; then return; fi
  IMAGE_TAG="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD)"
  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain -- workers/ingestion packages/domain config)" ]]; then
    ((ALLOW_DIRTY)) || die "uncommitted changes under workers/ingestion, packages/domain, or config; commit them or set ALLOW_DIRTY=1"
    IMAGE_TAG="${IMAGE_TAG}-dirty-$(date -u +%Y%m%dT%H%M%SZ)"
    warn "building from uncommitted files; tag $IMAGE_TAG"
  fi
}

# build_push CONTEXT_DIR DOCKERFILE IMAGE:TAG
build_push() {
  local context="$1" dockerfile="$2" ref="$3"
  if ((USE_CLOUD_BUILD)); then
    # Uploads the context honoring .gitignore (so /private/ and /data/ stay local).
    local config="$TMP_DIR/cloudbuild-$RANDOM.yaml"
    cat >"$config" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "${dockerfile#"$context"/}", "-t", "${ref}", "."]
images: ["${ref}"]
YAML
    g builds submit "$context" --config="$config" --region="$REGION"
  else
    command -v docker >/dev/null || die "docker not found; install it or pass --cloud-build"
    "$GCLOUD" auth configure-docker "$AR_HOST" --quiet >/dev/null 2>&1
    docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
      -f "$dockerfile" -t "$ref" --push "$context"
  fi
}

# digest_ref IMAGE_BASE TAG -> IMAGE_BASE@sha256:...
digest_ref() {
  local digest
  digest="$(g artifacts docker images describe "$1:$2" --format='value(image_summary.digest)' 2>/dev/null || true)"
  [[ "$digest" == sha256:* ]] || return 1
  printf '%s@%s\n' "$1" "$digest"
}

build_image() {
  resolve_tag
  log "Building ${IMAGE_BASE}:${IMAGE_TAG}"
  build_push "$REPO_ROOT" "$REPO_ROOT/workers/ingestion/Dockerfile" "${IMAGE_BASE}:${IMAGE_TAG}"
}

resolve_image() {
  if [[ -z "$IMAGE_TAG" ]]; then
    ((SKIP_BUILD)) || die "internal: no image tag"
    local current
    current="$(g run jobs describe "$JOB_NAME" --region="$REGION" \
      --format='value(spec.template.spec.template.spec.containers[0].image)' 2>/dev/null || true)"
    [[ -n "$current" ]] || die "--skip-build needs IMAGE_TAG or an existing job image"
    IMAGE_REF="$current"
  else
    IMAGE_REF="$(digest_ref "$IMAGE_BASE" "$IMAGE_TAG")" \
      || die "could not resolve digest for ${IMAGE_BASE}:${IMAGE_TAG}"
  fi
  info "image: $IMAGE_REF"
}

# ---------------------------------------------------------------- service accounts
ensure_sa() {
  local name="$1" display="$2" email="${1}@${PROJECT_ID}.iam.gserviceaccount.com"
  if g iam service-accounts describe "$email" >/dev/null 2>&1; then
    info "service account $email exists"
  else
    g iam service-accounts create "$name" --display-name="$display" >/dev/null
    info "service account $email created"
  fi
}

ensure_service_accounts() {
  log "Service accounts (no project-level roles)"
  ensure_sa "$JOB_SA_NAME" "Tahoe ingestion job runtime"
  ensure_sa "$SCHEDULER_SA_NAME" "Tahoe ingestion scheduler invoker"
}

# ---------------------------------------------------------------- secrets
secret_has_version() {
  [[ -n "$(g secrets versions list "$1" --filter='state=ENABLED' --limit=1 --format='value(name)' 2>/dev/null)" ]]
}

ensure_secret_resource() {
  local id="$1"
  if ! g secrets describe "$id" >/dev/null 2>&1; then
    g secrets create "$id" --replication-policy=user-managed --locations="$REGION" >/dev/null
    info "secret $id created (no value yet)"
  fi
  g secrets add-iam-policy-binding "$id" --member="serviceAccount:${JOB_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
}

validate_secret_value() {
  local id="$1" value="$2"
  case "$id" in
    "$SECRET_DATABASE_URL")
      [[ "$value" =~ ^postgres(ql)?:// ]] || die "DATABASE_URL must start with postgres:// or postgresql://"
      [[ "$value" == *sslmode=require* ]] || warn "DATABASE_URL has no sslmode=require" ;;
    "$SECRET_DEPLOY_HOOK")
      [[ "$value" == https://api.vercel.com/* ]] || warn "deploy hook URL does not start with https://api.vercel.com/" ;;
  esac
}

# Adds a version from $value without placing it in argv or output.
add_secret_version() {
  local id="$1" value="$2"
  validate_secret_value "$id" "$value"
  printf '%s' "$value" | g secrets versions add "$id" --data-file=- >/dev/null
  info "secret $id: new version added"
}

prompt_secret() {
  local id="$1" label="$2" required="$3" value=""
  if secret_has_version "$id"; then
    info "secret $id: enabled version exists (rotate with: $0 set-secret ...)"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    if [[ "$required" == 1 ]]; then die "secret $id has no value; run '$0 set-secret' from a terminal or pipe to it"; fi
    info "secret $id: no value (optional, skipped)"
    return 0
  fi
  read -r -s -p "    $label (hidden; Enter to skip): " value </dev/tty
  printf '\n' >&2
  if [[ -z "$value" ]]; then
    if [[ "$required" == 1 ]]; then die "$label is required"; fi
    info "secret $id: skipped"
    return 0
  fi
  add_secret_version "$id" "$value"
  value=""
}

ensure_secrets() {
  log "Secret Manager secrets ($REGION, accessible only to $JOB_SA)"
  ensure_secret_resource "$SECRET_DATABASE_URL"
  ensure_secret_resource "$SECRET_DEPLOY_HOOK"
  prompt_secret "$SECRET_DATABASE_URL" "DATABASE_URL (Prisma Postgres TCP URL)" 1
  prompt_secret "$SECRET_DEPLOY_HOOK" "VERCEL_DEPLOY_HOOK_URL" 0
}

cmd_set_secret() {
  [[ -n "$SECRET_ARG" ]] || die "usage: $0 set-secret database-url|vercel-deploy-hook-url"
  local id value=""
  id="$(secret_id_for "$SECRET_ARG")"
  g secrets describe "$id" >/dev/null 2>&1 || die "secret $id does not exist; run deploy first"
  if [[ -t 0 ]]; then
    read -r -s -p "New value for $SECRET_ARG (hidden): " value
    printf '\n' >&2
  else
    IFS= read -r value || true   # first line of stdin
  fi
  [[ -n "$value" ]] || die "empty value; nothing changed"
  add_secret_version "$id" "$value"
  value=""
  info "running executions keep the old value; the next execution reads :latest"
  info "after a successful run, disable or destroy older versions (see the runbook)"
}

# ---------------------------------------------------------------- job and schedule
deploy_job() {
  log "Cloud Run job $JOB_NAME"
  local secrets="DATABASE_URL=${SECRET_DATABASE_URL}:latest"
  local env_vars="TAHOE_RUNTIME=cloud-run"
  if [[ -n "$ANTHROPIC_FEDERATION_RULE_ID" && -n "$ANTHROPIC_ORGANIZATION_ID" ]]; then
    env_vars+=",TAHOE_IDENTITY_TOKEN_SOURCE=gcp-metadata"
    env_vars+=",ANTHROPIC_FEDERATION_RULE_ID=${ANTHROPIC_FEDERATION_RULE_ID},ANTHROPIC_ORGANIZATION_ID=${ANTHROPIC_ORGANIZATION_ID}"
    [[ -z "$ANTHROPIC_SERVICE_ACCOUNT_ID" ]] || env_vars+=",ANTHROPIC_SERVICE_ACCOUNT_ID=${ANTHROPIC_SERVICE_ACCOUNT_ID}"
    [[ -z "$ANTHROPIC_WORKSPACE_ID" ]] || env_vars+=",ANTHROPIC_WORKSPACE_ID=${ANTHROPIC_WORKSPACE_ID}"
    info "Claude via workload identity federation (rule $ANTHROPIC_FEDERATION_RULE_ID)"
  else
    warn "no ANTHROPIC_FEDERATION_RULE_ID/ANTHROPIC_ORGANIZATION_ID; new agendas keep rule tags"
  fi
  if secret_has_version "$SECRET_DEPLOY_HOOK"; then
    secrets+=",VERCEL_DEPLOY_HOOK_URL=${SECRET_DEPLOY_HOOK}:latest"
  else
    warn "no Vercel deploy hook; the site will not rebuild after runs"
  fi
  g run jobs deploy "$JOB_NAME" --region="$REGION" --image="$IMAGE_REF" \
    --service-account="$JOB_SA" --tasks=1 --parallelism=1 --max-retries="$MAX_RETRIES" \
    --task-timeout="$TASK_TIMEOUT" --cpu="$JOB_CPU" --memory="$JOB_MEMORY" \
    --set-secrets="$secrets" --set-env-vars="$env_vars" --labels=app=tahoe-public-meetings,component=ingestion >/dev/null
  # shellcheck disable=SC2001  # print env var names only, never secret references' values
  info "deployed with secrets: $(sed 's/=[^,]*//g' <<<"$secrets")"
  g run jobs add-iam-policy-binding "$JOB_NAME" --region="$REGION" \
    --member="serviceAccount:${SCHEDULER_SA}" --role=roles/run.invoker >/dev/null
  info "$SCHEDULER_SA may run this job (roles/run.invoker on the job only)"
}

ensure_scheduler() {
  log "Cloud Scheduler job $SCHEDULER_JOB"
  local uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
  local args=(--location="$REGION" --schedule="$SCHEDULE" --time-zone="$TIME_ZONE" --uri="$uri"
    --http-method=POST --oauth-service-account-email="$SCHEDULER_SA"
    --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform
    --description="Run $JOB_NAME every 6 hours")
  if g scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" >/dev/null 2>&1; then
    g scheduler jobs update http "$SCHEDULER_JOB" "${args[@]}" >/dev/null
    info "updated (state unchanged; resume a paused job with 'gcloud scheduler jobs resume')"
  else
    g scheduler jobs create http "$SCHEDULER_JOB" "${args[@]}" >/dev/null
    info "created: '$SCHEDULE' $TIME_ZONE"
  fi
}

# ---------------------------------------------------------------- monitoring and budget
monitoring_api() {
  # $1 method, $2 path, $3 optional body file. Token passed via a header file, not argv.
  local token
  token="$("$GCLOUD" auth print-access-token)"
  local curl_args=(-sS --fail-with-body -X "$1"
    -H @<(printf 'Authorization: Bearer %s\nx-goog-user-project: %s\nContent-Type: application/json\n' "$token" "$PROJECT_ID")
    "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/$2")
  if [[ -n "${3:-}" ]]; then curl_args+=(--data-binary "@$3"); fi
  curl "${curl_args[@]}"
}

CHANNEL=""
ensure_alerting() {
  log "Failure alert"
  if [[ -z "$ALERT_EMAIL" ]]; then
    warn "ALERT_EMAIL not set; skipping the failed-execution alert and budget email channel"
    return 0
  fi
  command -v python3 >/dev/null || die "python3 is required to build alert JSON"
  CHANNEL="$(monitoring_api GET 'notificationChannels?pageSize=200' | ALERT_EMAIL="$ALERT_EMAIL" python3 -c '
import json, os, sys
for c in json.load(sys.stdin).get("notificationChannels", []):
    if c.get("type") == "email" and c.get("labels", {}).get("email_address") == os.environ["ALERT_EMAIL"]:
        print(c["name"]); break')"
  if [[ -z "$CHANNEL" ]]; then
    local body="$TMP_DIR/channel.json"
    ALERT_EMAIL="$ALERT_EMAIL" python3 -c '
import json, os
print(json.dumps({"type": "email", "displayName": "Tahoe ingestion operator",
                  "labels": {"email_address": os.environ["ALERT_EMAIL"]}}))' >"$body"
    CHANNEL="$(monitoring_api POST notificationChannels "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
    info "email notification channel created"
  else
    info "email notification channel exists"
  fi

  local display="Tahoe ingestion job failed"
  if [[ -n "$(g monitoring policies list --filter="displayName=\"$display\"" --format='value(name)')" ]]; then
    info "alert policy exists"
    return 0
  fi
  local policy="$TMP_DIR/policy.json"
  # shellcheck disable=SC2016  # Python source; values arrive via the environment
  JOB_NAME="$JOB_NAME" CHANNEL="$CHANNEL" DISPLAY="$display" PROJECT_ID="$PROJECT_ID" REGION="$REGION" python3 -c '
import json, os
job, project, region = os.environ["JOB_NAME"], os.environ["PROJECT_ID"], os.environ["REGION"]
f = ("resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"%s\" "
     "AND metric.type = \"run.googleapis.com/job/completed_execution_count\" "
     "AND metric.labels.result = \"failed\"" % job)
logs = "https://console.cloud.google.com/run/jobs/details/%s/%s/executions?project=%s" % (region, job, project)
print(json.dumps({
  "displayName": os.environ["DISPLAY"],
  "combiner": "OR",
  "conditions": [{
    "displayName": "Cloud Run job execution failed",
    "conditionThreshold": {
      "filter": f,
      "aggregations": [{"alignmentPeriod": "600s", "perSeriesAligner": "ALIGN_SUM"}],
      "comparison": "COMPARISON_GT", "thresholdValue": 0, "duration": "0s",
      "trigger": {"count": 1}}}],
  "alertStrategy": {"autoClose": "86400s"},
  "notificationChannels": [os.environ["CHANNEL"]],
  "documentation": {"mimeType": "text/markdown",
    "content": "An execution of `%s` failed. The public site keeps its previous data. "
               "Executions and logs: %s. Runbook: docs/operations/deployment.md" % (job, logs)}}))' >"$policy"
  g monitoring policies create --policy-from-file="$policy" >/dev/null
  info "alert policy created: emails on any failed execution"
}

ensure_budget_guard() {
  log "Budget guard (pauses $SCHEDULER_JOB when cost >= budget)"
  if ! ((BUDGET_GUARD)); then
    warn "BUDGET_GUARD=0; the budget will only send emails"
    return 0
  fi
  if g pubsub topics describe "$BUDGET_TOPIC" >/dev/null 2>&1; then
    info "topic $BUDGET_TOPIC exists"
  else
    g pubsub topics create "$BUDGET_TOPIC" >/dev/null
    info "topic $BUDGET_TOPIC created"
  fi
  # Cloud Billing publishes budget notifications as this Google-managed account.
  g pubsub topics add-iam-policy-binding "$BUDGET_TOPIC" --member="$BUDGET_ALERT_PUBLISHER" \
    --role=roles/pubsub.publisher >/dev/null \
    || warn "could not grant Cloud Billing publish rights on $BUDGET_TOPIC; connecting the budget may grant it"

  # Least privilege: a custom role that can only read and pause scheduler jobs.
  if g iam roles describe "$GUARD_ROLE_ID" >/dev/null 2>&1; then
    g iam roles update "$GUARD_ROLE_ID" --permissions=cloudscheduler.jobs.get,cloudscheduler.jobs.pause >/dev/null
    info "custom role $GUARD_ROLE_ID exists"
  else
    g iam roles create "$GUARD_ROLE_ID" --title="Tahoe scheduler pauser" \
      --description="Read and pause Cloud Scheduler jobs (budget guard)" \
      --permissions=cloudscheduler.jobs.get,cloudscheduler.jobs.pause --stage=GA >/dev/null
    info "custom role $GUARD_ROLE_ID created"
  fi
  ensure_sa "$GUARD_NAME" "Tahoe budget guard"
  g projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${GUARD_SA}" \
    --role="projects/${PROJECT_ID}/roles/${GUARD_ROLE_ID}" --condition=None >/dev/null

  # Image tag is a content hash, so an unchanged guard is not rebuilt.
  local guard_dir="$REPO_ROOT/scripts/deploy/budget-guard" tag ref
  tag="$(cat "$guard_dir/Dockerfile" "$guard_dir/main.py" | shasum -a 256 | cut -c1-12)"
  if ! ref="$(digest_ref "$GUARD_IMAGE_BASE" "$tag")"; then
    build_push "$guard_dir" "$guard_dir/Dockerfile" "${GUARD_IMAGE_BASE}:${tag}"
    ref="$(digest_ref "$GUARD_IMAGE_BASE" "$tag")" || die "could not resolve guard image digest"
  fi
  g run deploy "$GUARD_NAME" --region="$REGION" --image="$ref" --service-account="$GUARD_SA" \
    --no-allow-unauthenticated --min-instances=0 --max-instances=1 --cpu=1 --memory=256Mi \
    --concurrency=1 --timeout=60s \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},SCHEDULER_JOB=${SCHEDULER_JOB},BUDGET_NAME=${BUDGET_NAME}" \
    --labels=app=tahoe-public-meetings,component=budget-guard >/dev/null
  # Pub/Sub pushes with the guard's own identity; only it may invoke the service.
  g run services add-iam-policy-binding "$GUARD_NAME" --region="$REGION" \
    --member="serviceAccount:${GUARD_SA}" --role=roles/run.invoker >/dev/null
  local url
  url="$(g run services describe "$GUARD_NAME" --region="$REGION" --format='value(status.url)')"
  [[ -n "$url" ]] || die "could not read $GUARD_NAME URL"
  info "service $GUARD_NAME deployed (private, scales to zero)"

  local sub_args=(--push-endpoint="$url" --push-auth-service-account="$GUARD_SA"
    --ack-deadline=60 --min-retry-delay=60s --max-retry-delay=600s --expiration-period=never)
  if g pubsub subscriptions describe "$GUARD_SUBSCRIPTION" >/dev/null 2>&1; then
    g pubsub subscriptions update "$GUARD_SUBSCRIPTION" "${sub_args[@]}" >/dev/null
    info "push subscription $GUARD_SUBSCRIPTION updated"
  else
    g pubsub subscriptions create "$GUARD_SUBSCRIPTION" --topic="$BUDGET_TOPIC" "${sub_args[@]}" >/dev/null
    info "push subscription $GUARD_SUBSCRIPTION created"
  fi
}

ensure_budget() {
  log "Billing budget ($BUDGET_AMOUNT USD/month on $PROJECT_ID)"
  [[ -n "$BILLING_ACCOUNT" ]] || { warn "billing account unknown; skipping budget"; return 0; }
  local existing
  if ! existing="$("$GCLOUD" billing budgets list --billing-account="$BILLING_ACCOUNT" \
      --billing-project="$PROJECT_ID" --filter="displayName=\"$BUDGET_NAME\"" --format='value(name)' 2>/dev/null)"; then
    warn "cannot list budgets (needs Billing Account Administrator or Costs Manager); see the runbook"
    return 0
  fi
  local notify=()
  if [[ -n "$CHANNEL" ]]; then notify+=(--notifications-rule-monitoring-notification-channels="$CHANNEL"); fi
  if ((BUDGET_GUARD)); then notify+=(--notifications-rule-pubsub-topic="projects/${PROJECT_ID}/topics/${BUDGET_TOPIC}"); fi
  if [[ -n "$existing" ]]; then
    if "$GCLOUD" billing budgets update "$existing" --billing-project="$PROJECT_ID" \
        --budget-amount="${BUDGET_AMOUNT}USD" ${notify[@]+"${notify[@]}"} --quiet >/dev/null; then
      info "exists; amount and notifications updated"
    else
      warn "budget update failed; check it in the console (Billing > Budgets & alerts)"
    fi
    return 0
  fi
  if "$GCLOUD" billing budgets create --billing-account="$BILLING_ACCOUNT" --billing-project="$PROJECT_ID" \
      --display-name="$BUDGET_NAME" --budget-amount="${BUDGET_AMOUNT}USD" \
      --filter-projects="projects/${PROJECT_ID}" --calendar-period=month \
      --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
      ${notify[@]+"${notify[@]}"} --quiet >/dev/null; then
    info "created; billing admins${ALERT_EMAIL:+ and ALERT_EMAIL} are emailed at 50/90/100%"
    if ((BUDGET_GUARD)); then info "notifications also go to $BUDGET_TOPIC for the guard"; fi
  else
    warn "budget creation failed; create it in the console (Billing > Budgets & alerts)"
  fi
  return 0
}

# ---------------------------------------------------------------- run once
cmd_run_now() {
  log "Executing $JOB_NAME"
  g run jobs execute "$JOB_NAME" --region="$REGION" --async >/dev/null
  local exec_name
  exec_name="$(g run jobs executions list --job="$JOB_NAME" --region="$REGION" \
    --sort-by='~metadata.creationTimestamp' --limit=1 --format='value(metadata.name)')"
  [[ -n "$exec_name" ]] || die "could not find the new execution"
  local url="https://console.cloud.google.com/run/jobs/executions/details/${REGION}/${exec_name}/logs?project=${PROJECT_ID}"
  info "execution: $exec_name"
  info "logs:      $url"
  local finished="" ok="" failed=""
  while [[ -z "$finished" ]]; do
    sleep 20
    IFS=, read -r finished ok failed < <(g run jobs executions describe "$exec_name" --region="$REGION" \
      --format='csv[no-heading](status.completionTime,status.succeededCount,status.failedCount)') || true
    printf '.' >&2
  done
  printf '\n' >&2
  info "CLI logs: gcloud logging read 'resource.type=\"cloud_run_job\" AND labels.\"run.googleapis.com/execution_name\"=\"$exec_name\"' --project=$PROJECT_ID --format='value(textPayload)' --order=asc"
  if [[ "${ok:-0}" -ge 1 && "${failed:-0}" -eq 0 ]]; then
    info "execution succeeded"
  else
    die "execution failed (succeeded=${ok:-0}, failed=${failed:-0}); see $url"
  fi
}

# ---------------------------------------------------------------- main
main() {
  parse_args "$@"
  TMP_DIR="$(mktemp -d)"
  case "$COMMAND" in
    set-secret) check_identity; cmd_set_secret ;;
    run-now) check_identity; cmd_run_now ;;
    deploy)
      check_identity
      ensure_project
      enable_apis
      ensure_registry
      ((SKIP_BUILD)) || build_image
      resolve_image
      ensure_service_accounts
      ensure_secrets
      deploy_job
      ensure_scheduler
      ensure_alerting
      ensure_budget_guard
      ensure_budget
      if ((RUN_NOW)); then cmd_run_now; fi
      log "Done. Runbook: docs/operations/deployment.md"
      ;;
  esac
}

main "$@"

#!/usr/bin/env bash
# Reproducible record of the MarketSentinel GCP project.
#
# NOTE: the live project was built MANUALLY in Cloud Shell. This script documents and
# re-creates that state (see infra/gcp/inventory.md). It is idempotent: every resource is
# checked with a read-only describe/list first and only created when missing.
#
# Dry-run by default: prints what it would do. Pass --apply to execute.
#   bash infra/gcp/setup.sh            # dry run
#   bash infra/gcp/setup.sh --apply    # create missing resources
#
# Needs: gcloud + bq on PATH, signed in as a project owner. Python 3.12 for the SQL steps.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-project-f8544066-3df8-4f99-8ed}"
REGION="${REGION:-us-central1}"
BQ_LOCATION="US"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SA_AGENT="sa-agent@${PROJECT_ID}.iam.gserviceaccount.com"
SA_INGEST="sa-ingest@${PROJECT_ID}.iam.gserviceaccount.com"
PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "mode: $([ $APPLY = 1 ] && echo APPLY || echo DRY-RUN)  project: $PROJECT_ID  region: $REGION"

# do <description> <command...>: print, and run only with --apply.
do_() {
  local desc="$1"; shift
  echo "-> $desc"
  if [ $APPLY = 1 ]; then "$@"; else echo "   (dry-run) $*"; fi
}

# ensure <description> <exists-check command> -- <create command...>
ensure() {
  local desc="$1" check="$2"; shift 3  # drop desc, check and the "--" separator
  if eval "$check" >/dev/null 2>&1; then echo "ok   $desc (exists)"; else do_ "create $desc" "$@"; fi
}

# ---- 1. APIs (idempotent by nature) ----
APIS="aiplatform artifactregistry bigquery bigqueryreservation cloudbuild iam iamcredentials
logging monitoring pubsub run secretmanager serviceusage storage"
for api in $APIS; do
  do_ "enable ${api}.googleapis.com" gcloud services enable "${api}.googleapis.com" --project "$PROJECT_ID"
done

# ---- 2. BigQuery datasets and tables (rendered by scripts/run_sql.py) ----
# Tables are created empty with CREATE ... IF NOT EXISTS. Data is loaded by the generator
# (python -m services.ingest.synth --apply), then sql/schema/harden.sql and
# sql/graph/market_graph.sql are applied. See infra/gcp/README.md for the order.
for f in sql/schema/00_datasets.sql sql/schema/10_risk_raw.sql sql/schema/20_risk_curated.sql \
         sql/schema/30_eval.sql; do
  if [ $APPLY = 1 ]; then python scripts/run_sql.py "$f" --project "$PROJECT_ID" --apply
  else python scripts/run_sql.py "$f" --project "$PROJECT_ID"; fi
done

# ---- 3. Service accounts and roles ----
ensure "service account sa-agent" "gcloud iam service-accounts describe $SA_AGENT" -- \
  gcloud iam service-accounts create sa-agent --display-name "Agent runtime" --project "$PROJECT_ID"
ensure "service account sa-ingest" "gcloud iam service-accounts describe $SA_INGEST" -- \
  gcloud iam service-accounts create sa-ingest --display-name "Trade ingestion" --project "$PROJECT_ID"

bind() {  # bind <member> <role>  (add-iam-policy-binding is idempotent)
  do_ "grant $2 to $1" gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "$1" --role "$2" --condition=None --quiet
}
for role in roles/aiplatform.user roles/bigquery.dataViewer roles/bigquery.jobUser; do
  bind "serviceAccount:$SA_AGENT" "$role"
done
for role in roles/bigquery.dataEditor roles/bigquery.jobUser roles/pubsub.subscriber; do
  bind "serviceAccount:$SA_INGEST" "$role"
done
# Roles the default compute SA needs for `gcloud run deploy --source` (granted by hand live).
for role in roles/cloudbuild.builds.builder roles/storage.objectViewer \
            roles/artifactregistry.writer roles/logging.logWriter; do
  bind "serviceAccount:$COMPUTE_SA" "$role"
done

# ---- 4. Pub/Sub with dead-letter topic ----
ensure "topic trades" "gcloud pubsub topics describe trades --project $PROJECT_ID" -- \
  gcloud pubsub topics create trades --project "$PROJECT_ID"
ensure "topic trades-dlq" "gcloud pubsub topics describe trades-dlq --project $PROJECT_ID" -- \
  gcloud pubsub topics create trades-dlq --project "$PROJECT_ID"
ensure "subscription trades-dlq-sub" \
  "gcloud pubsub subscriptions describe trades-dlq-sub --project $PROJECT_ID" -- \
  gcloud pubsub subscriptions create trades-dlq-sub --topic trades-dlq --project "$PROJECT_ID"
do_ "ensure Pub/Sub service identity" gcloud beta services identity create \
  --service pubsub.googleapis.com --project "$PROJECT_ID"
ensure "subscription trades-sub" \
  "gcloud pubsub subscriptions describe trades-sub --project $PROJECT_ID" -- \
  gcloud pubsub subscriptions create trades-sub --topic trades --ack-deadline 60 \
    --dead-letter-topic trades-dlq --max-delivery-attempts 5 --project "$PROJECT_ID"
do_ "dlq topic: Pub/Sub SA may publish" gcloud pubsub topics add-iam-policy-binding trades-dlq \
  --member "serviceAccount:$PUBSUB_SA" --role roles/pubsub.publisher --project "$PROJECT_ID"
do_ "trades-sub: Pub/Sub SA may subscribe" gcloud pubsub subscriptions add-iam-policy-binding \
  trades-sub --member "serviceAccount:$PUBSUB_SA" --role roles/pubsub.subscriber \
  --project "$PROJECT_ID"

# ---- 5. Artifact Registry ----
ensure "Artifact Registry repo sentinel" \
  "gcloud artifacts repositories describe sentinel --location $REGION --project $PROJECT_ID" -- \
  gcloud artifacts repositories create sentinel --repository-format docker \
    --location "$REGION" --project "$PROJECT_ID"

# ---- 6. Reservation gql-poc and project-wide assignment ----
# WARNING: the assignment routes EVERY query in the project through the reservation
# (autoscale up to 50 slots). Costs apply. Names allow letters, digits and hyphens only.
ensure "reservation gql-poc" \
  "bq show --reservation --location=$BQ_LOCATION --project_id=$PROJECT_ID gql-poc" -- \
  bq mk --project_id="$PROJECT_ID" --location="$BQ_LOCATION" --reservation --slots=0 \
    --edition=ENTERPRISE --autoscale_max_slots=50 gql-poc
if bq ls --reservation_assignment --location="$BQ_LOCATION" --project_id="$PROJECT_ID" 2>/dev/null \
    | grep -q "gql-poc"; then
  echo "ok   reservation assignment (exists)"
else
  do_ "assign gql-poc to project (QUERY)" bq mk --project_id="$PROJECT_ID" \
    --location="$BQ_LOCATION" --reservation_assignment \
    --reservation_id="$PROJECT_ID:$BQ_LOCATION.gql-poc" --job_type=QUERY \
    --assignee_type=PROJECT --assignee_id="$PROJECT_ID"
fi

# ---- 7. Cloud Run: sentinel-hello (source deploy of services/web) ----
# Live service is PUBLIC (--allow-unauthenticated). Remove that flag to make it private.
do_ "deploy sentinel-hello" gcloud run deploy sentinel-hello --source services/web \
  --region "$REGION" --project "$PROJECT_ID" --service-account "$SA_AGENT" \
  --allow-unauthenticated --max-instances 2 --min-instances 0 --memory 256Mi

echo "done ($([ $APPLY = 1 ] && echo applied || echo 'dry-run, nothing changed'))"

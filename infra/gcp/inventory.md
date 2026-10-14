# Live GCP inventory snapshot

Snapshot taken 2026-10-14 with read-only commands (`bq ls/show`, `INFORMATION_SCHEMA.COLUMNS`,
`gcloud ... list/describe/get-iam-policy`) as the personal gcloud profile. Project
`project-f8544066-3df8-4f99-8ed` (number `578558579917`), region `us-central1`, BigQuery
location `US`.

The live project was built manually in Cloud Shell. `setup.sh` and `sql/` are the
reproducible record; `scripts/verify_sync.py` checks them against live.
Human e-mail addresses are intentionally omitted.

## BigQuery (location US)

| Dataset | Table | Rows | Notes |
|---|---|---:|---|
| risk_raw | trades | 35,073 | trade_id, ts, market_id, account_id, side, outcome, price, size_usd, source |
| risk_curated | accounts | 2,039 | account_id, created_ts, kyc_tier, country, device_id, funding_source_id, source |
| risk_curated | markets | 20 | market_id, title, resolve_ts, source |
| risk_curated | alerts | 39 | cluster_id, account_id, score, reasons, cluster_size (written by `detect.py`, P2) |
| risk_graph | account_nodes | 2,039 | |
| risk_graph | device_nodes | 1,860 | |
| risk_graph | funding_nodes | 1,967 | |
| risk_graph | market_nodes | 20 | |
| risk_graph | uses_device | 2,039 | |
| risk_graph | funded_by | 2,039 | |
| risk_graph | traded_in | 22,164 | |
| risk_graph | traded_with | 54 | |
| risk_graph | `market_graph` | n/a | property graph, present in `INFORMATION_SCHEMA.PROPERTY_GRAPHS` |
| eval | ground_truth | 39 | scenario_id, scenario_type, account_id, market_id |
| eval | benchmark | 3 | method, flagged, true_pos, precision, recall, f1, run_ts |
| eval | benchmark_runs | 12 | benchmark plus leading `seed` column (4 seeds x 3 methods) |
| audit | (empty dataset) | | |

All columns are NULLABLE, no partitioning or clustering, no labels. Row counts come from
`bq show` table metadata. `INFORMATION_SCHEMA.TABLE_STORAGE` is not enabled (it would need
an `ALTER PROJECT` change, deliberately not made).

Live data is the output of the Cloud Shell generator with **SEED=42** (35,073 trades),
followed by `harden.sql` and `graph.sql`. This was confirmed offline: seed 42 gives
2,039 accounts / 35,073 trades / 39 truth (8 wash, 6 coordination, 25 mule); seed 7 gives
34,306 trades.

## Reservation

- `project-f8544066-3df8-4f99-8ed:US.gql-poc`: ENTERPRISE, slotCapacity 0, autoscale max 50.
- Assignment: QUERY job type, assignee `projects/project-f8544066-3df8-4f99-8ed` (whole project).

## Pub/Sub

| Resource | Settings |
|---|---|
| topic `trades` | |
| topic `trades-dlq` | publisher: Pub/Sub service agent |
| subscription `trades-sub` | pull, ack 60 s, DLQ `trades-dlq`, max 5 delivery attempts, retention 7 d |
| subscription `trades-dlq-sub` | pull, ack 10 s, retention 7 d |

## Service accounts and project roles

| Principal | Roles |
|---|---|
| `sa-agent` ("Agent runtime") | aiplatform.user, bigquery.dataViewer, bigquery.jobUser |
| `sa-ingest` ("Trade ingestion") | bigquery.dataEditor, bigquery.jobUser, pubsub.subscriber |
| default compute SA | artifactregistry.writer, cloudbuild.builds.builder, logging.logWriter, storage.objectViewer |
| Cloud Build SA | cloudbuild.builds.builder |
| Google-managed service agents | containerregistry, aiplatform, artifactregistry, cloudbuild, pubsub, run |
| human: project owner | roles/owner (a different person from the operator) |
| human: operator (personal profile) | admin roles for aiplatform, artifactregistry, bigquery, cloudbuild.editor, dataplex, datastore.owner, iam.*, logging.viewer, pubsub, projectIamAdmin, run, secretmanager, serviceUsage, storage |
| human: one external user | iam.devOps, iam.infrastructureAdmin, resourcemanager.projectIamAdmin |

## Artifact Registry (us-central1, DOCKER)

- `sentinel` (created by hand; empty)
- `cloud-run-source-deploy` (auto-created by `gcloud run deploy --source`, ~196 MB)

Also: GCS bucket `run-sources-project-f8544066-3df8-4f99-8ed-us-central1` (source uploads).

## Cloud Run (us-central1)

`sentinel-hello`: URL `https://sentinel-hello-tipo5b3mpa-uc.a.run.app`, SA `sa-agent`,
256Mi / 1 CPU, concurrency 80, template maxScale 2 (service-level annotation maxScale 3),
min instances 0, ingress `all`, revision `sentinel-hello-00001-7b7`, image
`cloud-run-source-deploy/sentinel-hello@sha256:e6e11a19...`.
IAM: `allUsers` has `roles/run.invoker` (**public, unauthenticated**).

## Enabled APIs

aiplatform, analyticshub, apptopology, artifactregistry, bigquery, bigqueryconnection,
bigquerydatapolicy, bigquerydatatransfer, bigquerymigration, bigqueryreservation,
bigquerystorage, cloudaicompanion, cloudapis, cloudbuild, cloudresourcemanager, cloudtrace,
containerregistry, dataform, dataplex, datastore, iam, iamcredentials, logging, monitoring,
privilegedaccessmanager, pubsub, run, secretmanager, servicemanagement, serviceusage,
sql-component, storage-api, storage-component, storage, telemetry (all `*.googleapis.com`).

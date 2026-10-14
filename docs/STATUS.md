# STATUS

Live state of GCP and the repo. Update in the same commit as any cloud change.

Last updated: 2026-10-14

## Live GCP resources

Project `project-f8544066-3df8-4f99-8ed`, region `us-central1`, BigQuery location `US`.
Snapshot 2026-10-14; full detail in `infra/gcp/inventory.md`. The project was built by hand
in Cloud Shell; `infra/gcp/setup.sh` and `sql/` are the reproducible record.

| Area | Live state |
|---|---|
| BigQuery datasets | `risk_raw`, `risk_curated`, `risk_graph`, `eval`, `audit` (empty) |
| Data (seed 42) | accounts 2,039; trades 35,073; markets 20; ground truth 39 (8 wash, 6 coordination, 25 mule) |
| Graph | 8 tables in `risk_graph` + property graph `market_graph` |
| Eval tables | `eval.benchmark` (3), `eval.benchmark_runs` (12), `risk_curated.alerts` (39) |
| Pub/Sub | topics `trades`, `trades-dlq`; subs `trades-sub` (DLQ, 5 attempts, ack 60 s), `trades-dlq-sub` |
| Service accounts | `sa-agent` (aiplatform.user, bigquery.dataViewer, bigquery.jobUser), `sa-ingest` (bigquery.dataEditor, bigquery.jobUser, pubsub.subscriber) |
| Artifact Registry | `sentinel` (empty), `cloud-run-source-deploy` (auto-created) |
| Cloud Run | `sentinel-hello` at `https://sentinel-hello-tipo5b3mpa-uc.a.run.app`, runs as `sa-agent`, **public** (`allUsers` invoker) |
| Reservation | `gql-poc` (ENTERPRISE, 0 baseline slots, autoscale max 50), QUERY assignment to the whole project |
| APIs | 35 enabled, listed in the inventory |

Note: the Phase 1 brief quoted 34,306 trades. That is the seed-7 count; live holds the
seed-42 data (35,073) because the last Cloud Shell loop ended on seed 42.

## Repo state

- Branch `chore/claude-setup`: Claude Code settings, CLAUDE.md, STATUS and ROADMAP.
- Scaffold, docs and scenario generator are merged to `main`.
- `feat/bq-schema-graph` exists locally with no commits beyond `main`; its relation to
  live GCP is unverified until P1.

## Known risks

- Reservation `gql-poc` is assigned to the whole project: every query consumes slots and cost.
- Live GCP state may contain console-only changes not yet captured in the repo (P1 resolves this).
- Gemini model ID in `us-central1` is unconfirmed; verify with a real call before use (P4).
- Tight schedule: submission Oct 18.

## Teardown after Nov 6

Run only after the human confirms. Each step needs a script under `infra/` with `--apply`.

1. Delete the reservation assignment, then reservation `gql-poc`.
2. Delete Cloud Run services.
3. Delete Pub/Sub topics, subscriptions and the DLQ.
4. Delete BigQuery datasets if desired.
5. Delete Artifact Registry repo `sentinel` if desired.

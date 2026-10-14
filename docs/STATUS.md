# STATUS

Live state of GCP and the repo. Update in the same commit as any cloud change.

Last updated: 2026-10-14 (P1 gcp-sync)

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

## Sync check (`python scripts/verify_sync.py`, 2026-10-14, exit 0)

36 checks, 0 drift, 0 warnings. Highlights:

- Row counts match repo/generator: trades 35,073, accounts 2,039, markets 20, ground truth 39;
  graph tables and eval tables match the snapshot.
- Trades fingerprint: live `SUM(size_usd)` = 2,989,086.94 = generator seed 42.
- Ground truth by type: wash_trading 8, coordinated_accumulation 6, mule_funding 25.
- `risk_graph.market_graph` exists; Pub/Sub topics, subscriptions and DLQ policy match.
- Cloud Run `sentinel-hello` returns HTTP 200 `{"service":"marketsentinel","status":"ok"}`
  with no credentials. **It is public** (`allUsers` has `roles/run.invoker`).
- Reservation `gql-poc` (ENTERPRISE, 0 slots, autoscale 50) and its QUERY assignment to the
  whole project match.

## Known drift

Things in GCP that the repo cannot (yet) reproduce:

1. **Human IAM bindings**: the operator holds ~18 admin roles and another account holds
   `roles/owner`; a third external account holds `iam.devOps`, `iam.infrastructureAdmin`,
   `resourcemanager.projectIamAdmin`. Granted by hand; deliberately not scripted. Review.
2. **Cloud Run is public** and was deployed with `--source`, which auto-created the
   `cloud-run-source-deploy` Artifact Registry repo and the GCS bucket
   `run-sources-<project>-us-central1`. `setup.sh` re-deploys but does not manage them. The
   `sentinel` registry repo is empty and unused so far. The service template caps at 2
   instances while the service-level annotation says 3.
3. **`risk_curated.alerts`, `eval.benchmark`, `eval.benchmark_runs`** were written by the
   Cloud Shell `detect.py` and a multi-seed loop. DDL is in `sql/schema/`, but their
   contents cannot be regenerated until the detector lands in P2.
4. **Timestamps**: live data was generated with the wall clock at load time; the repo
   generator uses a fixed `--now`. Counts, ids and relative timing are identical; absolute
   timestamps are shifted by a constant.
5. **`sa-ingest` and `sa-agent` roles** are scripted in `setup.sh`, but the Cloud Build and
   default-compute grants were added by hand during the first deploy and are scripted
   best-effort only.
6. **API list**: 35 APIs are enabled (several auto-enabled, e.g. dataplex, analyticshub).
   `setup.sh` enables only the 14 the project uses.
7. `INFORMATION_SCHEMA.TABLE_STORAGE` is not enabled (needs `ALTER PROJECT`); not required.

## Repo state

- Branch `feat/gcp-sync` (P1): SQL runner + schema DDL, `infra/gcp/`, canonical generator
  (ADR 0001), `services/web` starter, `scripts/verify_sync.py`.
- Scaffold, docs and the earlier scenario generator are merged to `main`; that older
  generator is replaced by the Cloud Shell one in this branch.
- To reproduce live data: `python -m services.ingest.synth --apply`, then
  `scripts/run_sql.py sql/schema/harden.sql --apply`, then `sql/graph/market_graph.sql --apply`.

## Known risks

- Reservation `gql-poc` is assigned to the whole project: every query consumes slots and cost.
- Cloud Run `sentinel-hello` is publicly invokable (see Known drift).
- Gemini model ID in `us-central1` is unconfirmed; verify with a real call before use (P4).
- Tight schedule: submission Oct 18.

## Teardown after Nov 6

Run only after the human confirms. Each step needs a script under `infra/` with `--apply`.

1. Delete the reservation assignment, then reservation `gql-poc`.
2. Delete Cloud Run services.
3. Delete Pub/Sub topics, subscriptions and the DLQ.
4. Delete BigQuery datasets if desired.
5. Delete Artifact Registry repo `sentinel` if desired.

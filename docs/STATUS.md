# STATUS

Live state of GCP and the repo. Update in the same commit as any cloud change.

Last updated: 2026-10-16 (P2 hardening)

## Live GCP resources

Project `project-f8544066-3df8-4f99-8ed`, region `us-central1`, BigQuery location `US`.
Snapshot 2026-10-14, data and graph reloaded 2026-10-16 (ADR 0002); full detail in `infra/gcp/inventory.md`. The project was built by hand
in Cloud Shell; `infra/gcp/setup.sh` and `sql/` are the reproducible record.

| Area | Live state |
|---|---|
| BigQuery datasets | `risk_raw`, `risk_curated`, `risk_graph`, `eval`, `audit` (empty) |
| Pre-P2 backups | `eval.{trades,accounts,markets,ground_truth,alerts}_pre_p2` = 35,073 / 2,039 / 20 / 39 / 39 rows (`sql/migrations/p2_00_backup_pre_p2.sql`). Delete after P2 is accepted. |
| Data (seed 42, ADR 0002) | accounts 2,039; trades 35,116; markets 20; ground truth 39 (8 wash, 6 coordination, 25 mule); `eval.decoy_groups` 23 (5 household, 8 exchange cluster, 10 busy-honest) |
| Graph | 8 tables in `risk_graph` + property graph `market_graph`; device_nodes 1,870, traded_in 22,169, traded_with 54. GQL runs natively in BigQuery on reservation `gql-poc`. |
| Eval tables | `eval.benchmark` (3), `eval.benchmark_runs` (12) from the old Cloud Shell `detect.py`; `eval.detector_results` (64 rows, iteration 1); `eval.decoy_groups` (23) |
| Detector outputs | `risk_curated.account_scores` (2,039), `risk_curated.alerts` (4 alerts, 38 accounts, columns `status` and `n_flagged` added by `sql/migrations/p2h_10_alerts_status.sql`), intermediates `risk_curated.det_*` and `baseline_flags`; old alerts kept as `risk_curated.alerts_legacy` (39) |
| Validation | dataset `eval_val`: `s7_*`, `s11_*`, `s23_*` inputs plus `s<seed>_det_*` and `s<seed>_baseline_flags` (threshold selection only) |
| Robustness | dataset `eval_val`: `s101_*` to `s108_*` (same layout, varied generator parameters from `evals/robustness_profiles.json`); scored only, never used for tuning |
| Pub/Sub | topics `trades`, `trades-dlq`; subs `trades-sub` (DLQ, 5 attempts, ack 60 s), `trades-dlq-sub` |
| Service accounts | `sa-agent` (aiplatform.user, bigquery.dataViewer, bigquery.jobUser), `sa-ingest` (bigquery.dataEditor, bigquery.jobUser, pubsub.subscriber) |
| Artifact Registry | `sentinel` (empty), `cloud-run-source-deploy` (auto-created) |
| Cloud Run | `sentinel-hello` at `https://sentinel-hello-tipo5b3mpa-uc.a.run.app`, runs as `sa-agent`, **public** (`allUsers` invoker) |
| Reservation | `gql-poc` (ENTERPRISE, 0 baseline slots, autoscale max 50), QUERY assignment to the whole project |
| APIs | 35 enabled, listed in the inventory |

Note: the Phase 1 brief quoted 34,306 trades (seed 7). Live holds seed 42: 35,073 trades
before ADR 0002, 35,116 after (43 decoy trades appended; earlier rows and ids unchanged).

Flat rule (funding source shared by 5+ accounts) measured on live data after the reload:
28 flagged, 20 of 39 attackers caught (wash 5/8, coordination 0/6, mule 15/25), 8 false
positives (all of the exchange cluster). It matches the offline generator numbers.

## Sync check (`python scripts/verify_sync.py`, 2026-10-16, exit 0)

42 checks, 0 drift, 0 warnings after the ADR 0002 reload and the P2 hardening changes. Highlights:

- Row counts match repo/generator: trades 35,116, accounts 2,039, markets 20, ground truth 39,
  decoy groups 23; graph row counts are derived from the generator, not a snapshot.
- Trades fingerprint: live `SUM(size_usd)` = 3,014,521.32 = generator seed 42.
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
3. **`eval.benchmark`, `eval.benchmark_runs`** were written by the Cloud Shell `detect.py`
   and a multi-seed loop and describe the pre-ADR-0002 data. They are superseded by
   `eval.detector_results`; DDL is in `sql/schema/`, contents are not regenerable.
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
  `scripts/run_sql.py sql/graph/market_graph.sql --apply`. `harden.sql` is gone: decoys and
  link evasion are generated (ADR 0002).
- Branch `feat/detector` (P2) is stacked on `feat/gcp-sync`: `origin/main` does not yet contain P1.
- Reproduce the detector: `python -m services.detector val-load --apply`, then
  `pipeline --target <7|11|23|live> --apply`, `select --iteration 1 --apply`,
  `score-live --iteration 1 --apply`, `python scripts/benchmark.py --iteration 1`.
  One-off migrations: `sql/migrations/p2_00_backup_pre_p2.sql`, `p2_10_alerts_v2.sql`,
  `p2h_10_alerts_status.sql`.
- Hardening (branch `feat/detector-hardening`): `python -m services.detector robust-load --apply`,
  `pipeline --target <101..108> --apply`, `margin --apply` (review margin from validation seeds
  into `threshold.json`), `score-live --iteration 1 --apply` (alerts with `status`),
  `python -m services.detector report` (writes `docs/results/hardening.json`, reads cached free
  table reads in `.cache/`), then `python scripts/benchmark.py --iteration 1`.

## P2 detector result (seed 42, frozen threshold 43.0, iteration 1)

| | Flat rule | Detector v1 |
|---|---|---|
| Precision / recall | 71% / 51% (20 of 39) | 100% / 97% (38 of 39) |
| Decoy false positives | 8 (exchange cluster) | 0 of 23 |

Full tables and caveats: `docs/benchmark.md`. The margin is thin (one evaded coordination
account scored 42.99 and was missed) and `scenario_hint` mislabels the wash ring as
`mule_funding`. Weights are fixed a priori; only the threshold came from data (validation
seeds 7, 11, 23). Pre-P2 backups (`eval.*_pre_p2`) can be deleted once P2 is accepted.

## P2 hardening (threshold still frozen at 43.0)

Details and tables: `docs/benchmark.md`. Cloud changes: alerts `status`/`n_flagged` columns
(migration), `eval_val.s101..s108` loaded and run through the detector SQL (batch loads free;
5 statements per seed, 500 MB cap each, about 35 s per seed), `risk_curated.alerts` and
`account_scores` rewritten by `score-live`. The flagged set on seed 42 is unchanged (38 of 39,
0 false positives).

- `scenario_hint` now comes from ring structure and is correct for every attacker alert on all
  11 other seeds and on seed 42 (it was rewritten after the seed 42 confusion was known).
- Review band [31.66, 43.0), margin 11.34 = mean validation gap. Seed 42: 1 attacker, 0 decoys in the
  band, 0 `needs_review` alerts.
- Robustness (8 varied seeds, frozen threshold): precision 100% on all, recall 95% to 100%
  (median 98%), worst F1 98% (seed 101). All 5 missed attackers sit in the review band, but so do
  1 to 2 decoys on seeds 102, 105 and 107.
- Ablation (validation-selected cut-offs): robustness F1 median graph-only 97%, behavior-only 81%,
  components-only 71%, flat rule 62%, full 99%.
- Reads of detector tables now use `list_rows` (no query, no slots) plus a local cache; clear
  `.cache/` after reloading data.

## P3 graph tools (`services/graph`)

No cloud changes: read-only queries on `market_graph` and `risk_graph.*` (100 MB billed cap,
15 s timeout). `neighbors`, `ring_subgraph`, `trades_for`; GQL first, SQL + NetworkX fallback,
`engine` on every reply. Live check (`GRAPH_LIVE_TESTS=1`): GQL and fallback return identical
rings for the wash (8), coordination (4) and mule (25) rings. Latency: warm GQL 3-10 s, fallback
instant after a ~9 s cold load. GQL variable-length quantified paths are not used: the graph's
edge types mix node types, so traversal is one GQL query set per hop.

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
4. Delete BigQuery datasets if desired (`eval_val` and the `eval.*_pre_p2` backups first).
5. Delete Artifact Registry repo `sentinel` if desired.

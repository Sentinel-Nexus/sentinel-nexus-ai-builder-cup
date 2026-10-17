# STATUS

Live state of GCP and the repo. Update in the same commit as any cloud change.

Last updated: 2026-10-17 (P5 web workbench + first deploy, branch `feat/web-workbench`)

## Live GCP resources

Project `project-f8544066-3df8-4f99-8ed`, region `us-central1`, BigQuery location `US`.
Snapshot 2026-10-14, data and graph reloaded 2026-10-16 (ADR 0002); full detail in `infra/gcp/inventory.md`. The project was built by hand
in Cloud Shell; `infra/gcp/setup.sh` and `sql/` are the reproducible record.

| Area | Live state |
|---|---|
| BigQuery datasets | `risk_raw`, `risk_curated`, `risk_graph`, `eval`, `audit` (`agent_events`, P4) |
| Agent tables (P4) | `audit.agent_events` (every step, tool call, LLM call, retry and verdict), `risk_curated.case_reports` (cached cases, append only, `lane` column), `risk_curated.case_decisions` (analyst overrides). DDL: `sql/schema/50_agents.sql`, `51_case_decisions.sql`, `sql/migrations/p4_20_case_lane.sql` |
| Pre-P2 backups | `eval.{trades,accounts,markets,ground_truth,alerts}_pre_p2` = 35,073 / 2,039 / 20 / 39 / 39 rows (`sql/migrations/p2_00_backup_pre_p2.sql`). Delete after P2 is accepted. |
| Data (seed 42, ADR 0002) | accounts 2,039; trades 35,116; markets 20; ground truth 39 (8 wash, 6 coordination, 25 mule); `eval.decoy_groups` 23 (5 household, 8 exchange cluster, 10 busy-honest) |
| Graph | 8 tables in `risk_graph` + property graph `market_graph`; device_nodes 1,870, traded_in 22,169, traded_with 54. GQL runs natively in BigQuery on reservation `gql-poc`. |
| Eval tables | `eval.benchmark` (3), `eval.benchmark_runs` (12) from the old Cloud Shell `detect.py`; `eval.detector_results` (64 rows, iteration 1); `eval.decoy_groups` (23) |
| Detector outputs | `risk_curated.account_scores` (2,039), `risk_curated.alerts` (4 alerts, 38 accounts, columns `status` and `n_flagged` added by `sql/migrations/p2h_10_alerts_status.sql`), intermediates `risk_curated.det_*` and `baseline_flags`; old alerts kept as `risk_curated.alerts_legacy` (39) |
| Validation | dataset `eval_val`: `s7_*`, `s11_*`, `s23_*` inputs plus `s<seed>_det_*` and `s<seed>_baseline_flags` (threshold selection only) |
| Robustness | dataset `eval_val`: `s101_*` to `s108_*` (same layout, varied generator parameters from `evals/robustness_profiles.json`); scored only, never used for tuning |
| Pub/Sub | topics `trades`, `trades-dlq`; subs `trades-sub` (DLQ, 5 attempts, ack 60 s), `trades-dlq-sub` |
| Service accounts | `sa-agent` (aiplatform.user, bigquery.dataViewer, bigquery.jobUser), `sa-ingest` (bigquery.dataEditor, bigquery.jobUser, pubsub.subscriber) |
| Artifact Registry | `sentinel` (`sentinel-app` images, one per deploy), `cloud-run-source-deploy` (auto-created) |
| Cloud Run | `sentinel-hello` at `https://sentinel-hello-tipo5b3mpa-uc.a.run.app`, runs as `sa-agent`, **public** (`allUsers` invoker). **`sentinel-app`** (P5, the workbench and agents API) at `https://sentinel-app-tipo5b3mpa-uc.a.run.app`, revision `sentinel-app-00005-v85`, runs as `sa-agent`, **public**, min 1 / max 1 instance, 1Gi, 1 CPU always allocated, concurrency 40, request timeout 300 s, image in Artifact Registry `sentinel` |
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
2. **Cloud Run is public** (`sentinel-hello` and `sentinel-app`; the latter is scripted by `infra/deploy_app.py`). `sentinel-hello` was deployed with `--source`, which auto-created the
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

- Branch `feat/agents` (P4) is stacked on the P2/P3 work already on `main`. Reproduce the agent
  tables: `python scripts/run_sql.py sql/schema/50_agents.sql --apply`,
  `sql/schema/51_case_decisions.sql --apply`, `sql/migrations/p4_20_case_lane.sql --apply`
  (set `PROJECT_ID`). Run the service: `uvicorn services.app.main:app`. Evals:
  `python -m evals.agents.run` (dry run) / `--apply --max-minutes 20`; rebuild the report from
  the stored cases without Gemini: `--rebuild`; cache timing: `python -m evals.agents.cache_latency`.

## P2 detector result (seed 42, frozen threshold 43.0, iteration 1)

| | Flat rule | Detector v1 |
|---|---|---|
| Precision / recall | 71% / 51% (20 of 39) | 100% / 97% (38 of 39) |
| Decoy false positives (the detector flags none of the 23 decoy accounts) | 8 (exchange cluster) | 0 of 23 |

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

## P4 agents (`services/app`, branch `feat/agents`)

Code complete and tested (189 unit tests with P5, ruff clean); deployed to Cloud Run with P5.
Details: `services/app/README.md`; results: `docs/agent-evals.md`, `docs/results/agent_evals.json`,
per-case files in `docs/results/agent_eval_cases/`, `critic_ab.json`, `cache_latency.json`.

- **Models** (confirmed with real calls in us-central1): `gemini-2.5-flash` for Investigator, Critic
  and Drafter. `gemini-2.5-pro` was measured as Critic: same accuracy as Flash on 10 labelled
  claims (10/10 twice each) and slower, so it is not used. `gemini-3-pro-preview` returns 404.
  The global endpoint did not reduce 429s in a burst test. ADK 2.12.0 (graph runtime, 2.x API).
- **Policy retrieval**: keyword (BM25-style) over `data/policies/`. Vertex AI Search
  (`discoveryengine`) is not enabled. Recorded on every case.
- **Pipeline**: three core tools prefetched in parallel (graph engine and `cached` shown in the
  timeline and audit), Investigator, deterministic ledger and number check, Flash Critic, one
  send-back, Drafter picks policy clauses. Limits: 8 tool calls, per-agent and total token caps
  (60k), 120 s per case (`timed_out`, not cached). Concurrency 2, backoff with jitter on 429/503.
- **Case members are deterministic** (alert members plus `ring_subgraph` members). The model
  decides escalate / clear / uncertain with rationale and evidence ids; it no longer lists accounts.
  Before this change the agent escalated 29 of 39 attackers: it built its list from its
  "15 accounts share funding source FM1" claim and dropped the 10 evaded mule accounts that were
  linked only through devices, although all 25 were in the alert. Not a token or truncation limit.
- **Claims**: a claim stays in a report only if all its evidence ids resolve in that run's ledger
  (re-checked; 13/13 in the four re-run real cases). Rejected claims appear as `removed_claims`
  with the reason and an "unverified, removed" note. The Investigator's own claim acceptance is
  separate: 63% final / 51% first pass of 35 claims.
- **Lanes**: `escalated`, `agent_cleared` (reason and evidence stored), `needs_analyst`.
  `GET /lanes`, `POST /cases/{id}/override` (analyst override of a clear, audited). Approval is
  still required before the mock webhook fires.

Final eval, first run 2026-10-16 (pipeline v1; seed 42, 8 cases + adversarial; `docs/agent-evals.md`). The four injected cases were re-run on 2026-10-17 on v2 with different results (next table rows and `docs/injected-rerun.md`):

| Metric | Result |
|---|---|
| Attacker recall per account | 39/39, evaded 15/15 (ring-level decision; see caveat) |
| Decoy false escalations, first run (2026-10-16, v1) | 0 of 3 groups, 0 of 23 accounts (DG1, DG2 cleared, DG3 `uncertain`) |
| Decoy false escalations, re-run (2026-10-17, v2) | 2 of 3 groups, 18 of 23 decoy accounts (plus 6 linked accounts): DG2, DG3 escalated |
| needs_review attackers escalated | first run 0/1 (`EV_C004` cleared); re-run 1/1 (escalated). Threshold and logic not changed |
| Where real alerts come from | the detector, which flagged none of the 23 decoy accounts; the `EV_` cases bypass it to test the agents in isolation |
| Adversarial (3 injected claims) | 3/3 rejected, 0 leaked (invented id, number not in rows, and a real-id claim only the LLM Critic could stop) |
| Live latency (8 cases) | median 44.1 s, max 67.4 s; 1 of 8 above 60 s (`EV_C004`, one send-back call took 37 s) |
| Cached case | median 0.07 ms, worst 0.21 ms service-side (excludes HTTP) |
| Eval cost, all runs | 136 LLM calls recorded in `audit.agent_events` (plus 4 in `critic_ab`): about $0.55 at third-party prices (+-30%); the first, crashed attempt has no input/output split, so its share is estimated |

Caveats: recall follows from escalating whole rings, so it no longer measures per-account
judgement and the exclusion path was never exercised on live data. The four decoy/borderline
`EV_*` cases are injected `needs_review` alerts (seed 42 has none) and were not re-run after the
membership change. Four real alerts are cached in `case_reports` from the final eval runs.

IAM applied 2026-10-17: `sql/iam/sa_agent_agent_tables.sql` gave `sa-agent`
`roles/bigquery.dataEditor` on three tables only (`audit.agent_events`, `risk_curated.case_reports`,
`risk_curated.case_decisions`), verified on each table's policy; no dataset-level binding, and
`risk_curated.alerts` is untouched (still read-only for `sa-agent`).

Deployed with P5 (see below). The forced precompute was skipped on
purpose (`python -m scripts.precompute_cases`; the four real alerts are already cached).

## P5 web workbench and first deploy (`services/app/static`, branch `feat/web-workbench`)

Deployed 2026-10-16/17 with `python infra/deploy_app.py --apply` (dry run by default). It stages a
build context (`services/app`, `services/graph`, `data/policies`, `infra/app/Dockerfile`), builds with
Cloud Build into `sentinel/sentinel-app:<timestamp>`, polls the build (log streaming is not permitted
for the operator) and deploys. IAM: only what `--allow-unauthenticated` adds for this one service;
`sa-agent` roles are unchanged (table-level `dataEditor` on the three agent tables, `dataViewer`,
`jobUser`, `aiplatform.user`). Nothing was deleted and the reservation was not touched.

- **Service** (revision `sentinel-app-00005-v85`, deployed 2026-10-16 21:16 UTC): `https://sentinel-app-tipo5b3mpa-uc.a.run.app`.
  Cloud Run configuration, read back with `gcloud run services describe`: **CPU 1, always allocated**
  (`--no-cpu-throttling`, instance-based billing) with startup CPU boost, **min instances 1, max
  instances 1**, 1Gi, concurrency 40, request timeout 300 s, runs as `sa-agent`, public. Env:
  `PROJECT_ID`, `GCP_REGION`, `LIVE_PER_CLIENT_HOUR=3`, `LIVE_PER_HOUR=20`, `LIVE_PER_DAY=120`.
  **Startup probe** `GET /health` every 5 s, up to 48 failures (240 s): `/health` answers 503 until the
  service is warm (tables read, graph and model clients up), so no traffic reaches an instance that is
  listening but not ready. Always-allocated CPU keeps the warm-up thread and the background audit writer
  running between requests.
- **Why max 1 instance for the judging window**: the case and decision caches, the graph and trade
  caches and the live-run limits are in memory per instance. With several instances a visitor could see
  stale lanes for up to the 30 s cache TTL, and the live ceiling would multiply by the instance count.
  One instance (1 vCPU, concurrency 40) handled 300 requests at concurrency 10 with no error
  (median 0.8 s from a laptop). The cost is no scale-out: more than about 40 simultaneous requests queue.
- **Screens**: Overview (new first tab, `#/overview`, see "Overview, readable graph" below), command centre (detector-alert KPIs and queue, a "Replay a recorded investigation" control that animates a stored timeline in the browser), case
  (ring graph, timeline, draft report with evidence chips, Approve / Reject / Escalate with a required
  reason, Investigate live, JSON export, print view), lanes, benchmark (numbers parsed from
  `docs/benchmark.md` and `docs/agent-evals.md` by `scripts/build_ui_data.py`, caveats and limits shown),
  governance (caps, guards, audit log). Light and dark themes, keyboard shortcuts (`?`), AA contrast
  test (`tests/unit/test_ui_contrast.py`). Screenshots: `docs/img/ui_*.png` (and `_dark`).
- **Cached versus live**: every case shows a CACHED or LIVE badge. Browsing never calls Gemini. A live
  run is a cache miss or `force=true`; it is limited to 3 per client per hour, 20 per hour and 120 per
  day for the service (env `LIVE_PER_CLIENT_HOUR`, `LIVE_PER_HOUR`, `LIVE_PER_DAY`; counted per
  instance, and there is one). A live run costs about $0.01 to $0.02, so the worst case is about $2.40
  a day. A refused run answers 429 with a plain message (including that the cached cases are real
  recorded agent runs) and `Retry-After`, and its result goes to the requester only: the stored case is not
  replaced (`PERSIST_FORCED=1` would change that). The UI stops at 120 s with a clear message.
- **Checks** (2026-10-17, no credentials): `scripts/e2e_smoke.py --write` 43/43 and 41/41 read-only
  (`docs/results/e2e_smoke.json`): every UI endpoint, audit write by the live service as `sa-agent` and
  read back from `audit.agent_events` (two `smoke-test` approvals now sit in the audit table), reads of
  all tables, 180 of 180 repeated GETs returned 200. One real live run on `AL_5a1a959872` through the
  deployed service: `ok`, 13.3 s, 10,329 tokens, 3 tool calls, GQL, about $0.01 to $0.02; the stored case
  was unchanged and the client quota went 2 to 1. A rate-limited or failing model is covered by
  `test_a_rate_limited_model_gives_a_clean_error_result_not_a_crash` (a real Vertex 429 was not forced).
- **Latency** (from a laptop, includes the network): cached case median 308 ms, p95 329 ms
  (service-side 0.07 ms); first request about 0.7 to 2 s (TLS). Cold instance (fresh revision, from the
  logs): process start to listening 15 s, `ready` 76 s after process start (60.8 s warm-up: seven table
  reads, GQL client and model clients), views prefetched at 95 s. Requests work during warm-up but read
  tables lazily. With min-instances 1 a visitor should not meet a cold instance unless a scale-out happens.
- **The intermittent 500s, traced but not removed**: Cloud Run's request log names the revision that
  served every 500 seen so far (HTTP 500, 0 s latency, no application log, a Google-style HTML error
  page): 20:19:11 and 20:20:15 `sentinel-app-00001-k88`, 21:06:30, 21:20:15 and 21:22:07
  `sentinel-app-00003-kkz`, plus one more at 21:24 in the IPv6 test below. None was ever served by the
  active revision; the revisions named had logged "Application shutdown complete" (20:18:52 and
  21:03:49), and traffic is 100% on the latest revision. So these are requests that a Google front-end
  still routes to a retired revision's stopped instance. They persisted for at least 20 minutes after
  the retirement and at a rate of about 1 in 300 requests from the test machine: a clean 300-request run
  at concurrency 10 on revision 00004 (0 non-200), then 1 non-200 in each of two further runs on revision
  00005. A split by address family: 150 requests over IPv4 all returned 200, 150 over IPv6 returned 149
  and one 500, which points at one front-end reached over IPv6 from this network. The bar of zero
  non-200 was therefore **not met** on every run. Nothing in the app can catch it. The remaining
  lever is to delete the retired revisions (`sentinel-app-00001-k88` to `00004-6ph`), which was not done
  because nothing may be deleted without an explicit go-ahead; one command, run by the project owner:
  `gcloud run revisions delete sentinel-app-00001-k88 sentinel-app-00002-c46 sentinel-app-00003-kkz sentinel-app-00004-6ph --region us-central1`.
  Also avoid a deploy during judging. A global handler logs any real 500 from the application with a
  traceback and a request id (`unhandled error request_id=...`) and returns the same id to the caller;
  none has occurred.
- **Startup**: with the startup probe, revision 00004 listened 7 s after process start, answered 503
  on `/health` five times and became ready 35 s after process start (warm-up 24.9 s); revision 00005
  was ready 18 s after process start (warm-up 17.0 s, views prefetched at 49 s). Revision 00002, before
  the probe, took 76 s, so the probe allows up to 240 s.
- **Cost**: see the Cloud Run line above. With CPU always allocated and 1 instance, the service bills
  vCPU, memory and requests continuously (about 24 h a day) even when nobody visits.

**Nov 6 teardown (stop idle billing)**: setting only min-instances to 0 is not enough while CPU is
"always allocated"; both must change. Run `python infra/deploy_app.py --apply --teardown`, which runs

    gcloud run services update sentinel-app --region us-central1 --project project-f8544066-3df8-4f99-8ed --min-instances 0 --cpu-throttling

(`--cpu-throttling` returns to request-based billing, so with no traffic the service costs nothing).
Check with `gcloud run services describe sentinel-app` that `minScale` is gone or 0 and
`run.googleapis.com/cpu-throttling` is `true`. Then follow the teardown list below; do not leave
`sentinel-app` public after the judging window.

**Fixes made after the first deploy (2026-10-17)**:

- Decisions by an analyst whose name starts with `smoke` are test records: they stay in
  `audit.agent_events` and `case_decisions` (nothing was deleted) but are left out of lanes, queue,
  summary and the case page. `?include_test=true` shows them and the Governance audit log marks them
  "(test record)". The earlier `smoke-test` approvals on `AL_584807441a` no longer affect any state:
  all eight cases show no analyst decision and `/summary` reports `decided 0`.
- `/audit` caps the result at `limit` after filtering. A request with `limit=15` returned 15 on the
  deployed revision before the change, so the 20-event report could not be reproduced; the ceiling is
  now enforced in the endpoint and covered by a test.
- Injected review cases are stored: see the next section.

## Overview, readable graph, evaluation cases (branch `feat/web-workbench`, 2026-10-17)

- **Overview** (`#/overview`, first tab; an empty hash is replaced by it, `#/` is still the Command
  centre). Static data in `services/app/static/data/overview.json`, built by
  `python scripts/build_overview_data.py --base <service url> [--refresh-bq]` from `benchmark.json`,
  the service's `/summary`, `/queue` and stored reports, `docs/results/cache_latency.json`, and **one**
  capped BigQuery query (USD traded by flagged accounts per scenario, plus trade and account counts;
  estimate printed first: 543,320 bytes, cap 50 MB; result cached in `docs/results/overview_bq.json`).
  The page makes no BigQuery or Gemini call (`scripts/overview_screens.py` checks the requests).
  Pre-computed from recorded runs; synthetic data; no savings, ROI or hours-saved figures.
  Numbers are tied to their sources in `tests/unit/test_overview_data.py` and `ui_smoke`.
- **Case graph**: plain-language caption, legend, edge toggles, Clusters/Circle layouts (deterministic),
  node size by volume, glow on accounts the report cites, hubs always labelled, account labels when
  zoomed or hovered (always for groups of 12 or fewer), side panel with plain facts, "Why this was
  flagged" bars, "Walk me through it" (4 steps, Autoplay, Esc), collapsible list view. Screenshots
  `docs/img/ui_graph_*.png` (`scripts/graph_screens.py`). Known limit: the graph payload has no per-pair
  fill counts, so for rings over 40 edges the merged line per pair is thick when the pair traded both
  ways and thin when one way; the hover says which.
- **Evaluation cases**: injected cases are hidden by default behind a toggle on the Command centre and
  Lanes and shown in a separate section; `/summary` now reports `detector_alerts` and
  `evaluation_cases` apart (the old totals are kept).
- **Idempotent GETs** retry once after 400 ms on a network error or 5xx
  (`tests/unit/test_ui_api_retry.py`). Detector scores show two decimals (43.01 vs 42.99).
- **Deployed 2026-10-17: revision `sentinel-app-00006-pqv`** (`python infra/deploy_app.py --apply`; max 1
  instance, min 1, startup probe unchanged; the old revisions were not deleted). `e2e_smoke` (no
  `--write`) against the live URL: **71/71 passed**, including the new Overview checks; the Overview
  makes no API call (`overview_screens.py` against the live URL). Cached case median 308 ms from a laptop.
- Local checks before the deploy: `ui_smoke --write` 73/73 (override moved EV_DG1 to escalated in the
  test view only; default lanes stayed escalated 7, agent-cleared 1, needs-analyst 0).
- **Cache fix**: `/static/*` is served with `Cache-Control: no-cache` (ETag revalidation) and `/` with
  `no-store`; `index.html` carries `?v=<build id>` on the CSS and JS entry files and a footer build id
  (short git sha, set as env `BUILD_ID` by `infra/deploy_app.py`; `dev` locally). Cause of the earlier
  stale pages: no cache headers on `/static`, so browsers applied heuristic caching to old JS.
  Test: `tests/unit/test_workbench.py::test_cache_headers_and_build_id`; browser check:
  `scripts/cache_check.py` (two visits in one context).
- **Graph containment fix**: the edge tooltip (a long "traded both ways" line near the bottom of
  `#graph`) was absolutely positioned at the pointer with no measuring or clamping and `#graph` did not
  clip, so it painted over the legend; a plain wheel also zoomed the graph instead of scrolling the page.
  Now `#graph` is `overflow: hidden; isolation: isolate`, the tooltip is measured, flipped and clamped
  inside it and hidden on leave, zoom, pan and wheel, plain wheel scrolls the page, Ctrl/Cmd + wheel (and
  touch pinch) zooms, `+`, `-` and Fit buttons and a hint are in the graph, and a ResizeObserver refits on
  resize. Reproduced first with `scripts/graph_overlap_check.py` (before: tooltip left `#graph` on the
  8-account case, and the wheel zoomed on all four case/size combinations; after: 0 problems on 8 and 25
  accounts at 1280x800 and 390x844). Also run in pytest against recorded payloads
  (`tests/unit/test_graph_browser.py`, fixtures in `tests/unit/fixtures/graph_cases`).
- **Not done in this release**: the executable evidence standard (signal families) and the repeated
  3x evaluation. Agent decisions on injected decoys were unstable across runs; real alerts come from the
  detector, which flagged none of the decoys.

## Injected review cases (stored 2026-10-17, source `eval_injected`)

`python -m scripts.injected_cases --apply --max-runs 4` (estimate first: expected $0.11, worst case
$0.34; actual 83,525 tokens) wrote `data/injected_alerts.json` (detector fields only, no labels) and
ran EV_C004, EV_DG1_household, EV_DG2_exchange_cluster and EV_DG3_busy_honest once each through the
final pipeline into `risk_curated.case_reports`. The app loads the file as extra alerts and labels
every such case "Injected review case (evaluation), not detector output". Result and what it means:
`docs/injected-rerun.md`. **The re-run did not reproduce the earlier evaluation**: EV_C004 was
escalated (first run: cleared), EV_DG2 and EV_DG3 were escalated (first run: cleared, uncertain), only
EV_DG1 was cleared. So 2 of 3 innocent groups (18 of 23 decoy accounts, plus 6 linked accounts from the
ring subgraph) were falsely escalated in the re-run (2026-10-17, v2); the "0 of 23" figure above is the
first run's (2026-10-16, v1). Real alerts come from the detector, which flagged none of the decoys. Lanes now:
**escalated 7, agent-cleared 1, needs-analyst 0**: the needs-analyst lane is empty. Nothing was tuned
and no case was re-run to get a different mix. Override was checked on the live service with analyst
`smoke-test` on EV_DG1: it moved to escalated in the test view (`/lanes?include_test=true`), was audited,
and the default view still showed it agent-cleared (the test reset is the exclusion of test records).

Pending for P6: the replay control, Model Armor and PII redaction (shown as "not deployed" on the
Governance screen).

## Known risks

- Reservation `gql-poc` is assigned to the whole project: every query consumes slots and cost.
- Cloud Run `sentinel-hello` and `sentinel-app` are publicly invokable (see Known drift). `sentinel-app` can spend Gemini tokens only through the limited live path; reads are free.
- Live-run limits are per instance (one instance now), and a public visitor can still use up the hourly quota for others.
- Vertex latency is uneven (one send-back call took 37 s; 429s under burst). Mitigated by caps,
  retries and a 120 s deadline, not removed.
- The `sa-agent` write grants exist only on the three agent tables; the deploy that uses them was
  tested on 2026-10-17 (audit write and read-back, see P5).
- Tight schedule: submission Oct 18.

## Teardown after Nov 6

Run only after the human confirms. Each step needs a script under `infra/` with `--apply`.

1. Delete the reservation assignment, then reservation `gql-poc`.
2. Delete Cloud Run services (first set `sentinel-app` min-instances to 0, see P5).
3. Delete Pub/Sub topics, subscriptions and the DLQ.
4. Delete BigQuery datasets if desired (`eval_val` and the `eval.*_pre_p2` backups first).
5. Delete Artifact Registry repo `sentinel` if desired.

# Architecture

MarketSentinel finds manipulation in prediction-market data, explains it with evidence, and leaves
every consequential decision to a human analyst. All data is synthetic (there is no real market feed).

## Layers

| Layer | What it is | Where |
|---|---|---|
| Data | Synthetic generator (seed 42: 2,039 accounts, 35,116 trades, 39 planted attackers, 23 decoy accounts) loaded into BigQuery datasets `risk_raw`, `risk_curated`, `risk_graph`, `eval`, `audit` | `services/ingest/synth`, `sql/schema` |
| Graph | BigQuery property graph `market_graph` queried with GQL; automatic SQL plus NetworkX fallback when GQL is unavailable | `sql/graph`, `services/graph` |
| Detection | Frozen linear score over nine scenario-agnostic features, threshold 43.0, written to `risk_curated.account_scores` and `risk_curated.alerts` | `sql/views`, `services/detector` |
| Agents | Google ADK agents on Gemini 2.5 Flash (Investigator, Critic, Drafter) behind a tool-call ledger | `services/app` |
| Workbench | FastAPI service serving the analyst UI, the cached cases, approvals and the audit log | `services/app` (`static/`) |
| Streaming | Pub/Sub topic, subscription and dead-letter queue into stream tables, scored by the frozen detector | `services/stream` |

## Identities

- **`sa-agent`** is the runtime identity of both Cloud Run services (`sentinel-app`, `sentinel-hello`).
  Roles on the project: `aiplatform.user`, `bigquery.dataViewer`, `bigquery.jobUser`. Write access is
  table-level only: `bigquery.dataEditor` on the three agent tables (`audit.agent_events`,
  `risk_curated.case_reports`, `risk_curated.case_decisions`) and on the four stream tables;
  `pubsub.publisher` on topic `trades` and `pubsub.subscriber` on subscription `trades-sub` only.
  `risk_curated.alerts` is read-only for it.
- **`sa-ingest`** exists for ingestion (`bigquery.dataEditor`, `bigquery.jobUser`, `pubsub.subscriber`).
- Both Cloud Run services are public (`allUsers` invoker); the service holds no secret and exposes no write
  path to account data. Analyst names on approvals are free text: there is no sign-in.

Sources: `docs/STATUS.md` (live state), `sql/iam/`, `infra/stream_setup.py`.

## Guards

Active: evidence ledger (a claim survives only if every evidence id came from a tool call in the same run),
number check, LLM Critic with one send-back, escalation guard (two supported claims and named accounts, else
downgraded to uncertain), analyst approval with a written reason, audit of every step, read-only SQL guard for
graph queries, stream schema validation, idempotent consumer, rate limits, token and tool-call caps. Not built:
Model Armor or any prompt-injection guard, PII redaction, Vertex AI Search retrieval (retrieval is keyword-based).
The outbound webhook is a mock that never leaves the process.

## Cost controls

- Every BigQuery job goes through one runner with a read-only guard, a byte cap (100 MB; 500 MB for the single
  stream scoring script, see below) and a timeout. Reads of whole tables use `list_rows`, which is free.
- The reservation `gql-poc` is assigned to the whole project, so every query uses slots.
- Per run: at most 8 tool calls, 60,000 tokens (investigator 40,000, critic 14,000, drafter 8,000) and a 120 s
  wall clock. Live investigations: 3 per client per hour, 20 per hour and 120 per day for the instance.
- Cached cases are served from memory; the four real alerts and four evaluation cases were precomputed.
- One instance only (`max-instances 1`), because the limits and caches are per instance.
- Teardown after Nov 6: `docs/submission-checklist.md`.

## Batch path (the detector's home)

```
synthetic generator (seed 42)  ->  BigQuery risk_raw / risk_curated
                                    ->  property graph market_graph (risk_graph.*)
                                    ->  frozen detector (sql/views + services/detector,
                                        threshold 43.0)  ->  risk_curated.alerts
alerts  ->  Gemini agents (ADK, Cloud Run sentinel-app)  ->  case report with evidence ids
        ->  analyst approves / rejects / overrides (audit.agent_events)
```

Detector results, benchmark and limits: `docs/benchmark.md`. Agent results: `docs/agent-evals.md`.

## Streaming path (P6, `STREAMING_ENABLED`)

A near-real-time micro-batch path. It is additive: with the flag off the service behaves exactly like
revision `sentinel-app-00009-lgx`, and no batch table, count or KPI is touched.

```
POST /stream/replay            fixed burst (data/stream/burst_01.jsonl, 310 events, no request body)
  -> Pub/Sub topic trades       attributes: event_type, run_id
  -> subscription trades-sub    pull, ack 60 s, dead-letter to trades-dlq after 5 attempts
  -> streaming-pull thread in sentinel-app (min = max = 1 instance, CPU always allocated)
       strict schema check -> in-memory duplicate set -> micro-batch (3 s or 200 messages)
       -> MERGE into risk_raw.stream_accounts / risk_raw.stream_trades (natural-key idempotence)
       -> ack            (malformed: nack, retried, dead-lettered after 5 attempts)
  -> when the run has everything (or goes quiet): ONE scoring query
       frozen detector SQL (det_10..det_40) over the stream tables, as TEMP tables
       -> frozen weights and threshold (services/detector/scoring.py, threshold.json)
       -> MERGE into risk_curated.stream_alerts (alert_id stable across replays)
  -> GET /queue marks them "streamed": true; case detail and graph use the existing endpoints
  -> audit.stream_events: published, ingested, duplicate_dropped, malformed, retry,
     dead_letter_candidate, scored, alert_raised
```

Design points:

- **Nothing is forked.** The detector SQL files are rendered by the detector's own `render_step`; the
  only change is mechanical: output tables become script-local TEMP tables (and the output dataset
  prefix is removed). Run on the live tables, that script reproduces the stored `account_scores`
  (2,039 of 2,039 scores equal, 38 flagged both ways). Weights, threshold 43.0 and features are
  untouched; nothing was tuned to the stream.
- **Labels never reach the service.** The burst file has no labels. The ground truth is
  `evals/stream/burst_01_truth.json`, read only by `scripts/stream_eval.py` and tests; a test fails if
  service or detector code mentions it, and the deploy staging excludes `evals/`.
- **Least privilege.** `sa-agent` has `roles/pubsub.publisher` on topic `trades` only,
  `roles/pubsub.subscriber` on subscription `trades-sub` only, and `roles/bigquery.dataEditor` on
  exactly four new tables. BigQuery jobs go through one module (`services/stream/bq.py`): reads use
  the read-only guard, writes are MERGE statements whose target must be one of the four tables, and
  the scoring script may create only TEMP tables.
- **Rate limits** (per instance): 1 per client per 10 minutes, 6 per hour, 30 per day, for replay and,
  with separate counters, for the bad-message demo. A refusal is HTTP 429 with a plain message.
- **Streamed alerts** are replaced when a newer run starts, are excluded from `/summary` and the
  detector KPIs, and their graph is built by the existing graph service (fallback engine) from the
  stream tables.
- **Ordering.** The burst is published in event-time order, but Pub/Sub does not guarantee delivery
  order on this subscription (ordering is not enabled and the subscription is not modified). Ingestion
  and scoring do not depend on order.

## Deployment

One Cloud Run service `sentinel-app` (FastAPI, static workbench, agents, stream consumer). Deploys use
`infra/deploy_app.py`: `--no-traffic` publishes a tagged candidate revision, `--promote` moves
traffic, and the rollback command is printed by both. See `docs/STATUS.md` for the live revision.

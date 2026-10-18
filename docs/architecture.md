# Architecture

MarketSentinel finds manipulation in prediction-market data, explains it with evidence, and leaves
every consequential decision to a human analyst. All data is synthetic.

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
POST /stream/replay            fixed burst under a fresh id suffix per run (data/stream/burst_01.jsonl, 310 events, no request body);
                               ?same=true re-sends the previous run's exact ids (idempotency demo)
  -> Pub/Sub topic trades       attributes: event_type, run_id
  -> subscription trades-sub    pull, ack 60 s, dead-letter to trades-dlq after 5 attempts
  -> streaming-pull thread in sentinel-app (min = max = 1 instance, CPU always allocated)
       strict schema check -> in-memory duplicate set -> micro-batch (3 s or 200 messages)
       -> MERGE into risk_raw.stream_accounts / risk_raw.stream_trades (natural-key idempotence)
       -> ack            (malformed: nack, retried, dead-lettered after 5 attempts)
  -> when the run has everything (or goes quiet): ONE scoring query
       frozen detector SQL (det_10..det_40) over the stream tables, as TEMP tables
       -> frozen weights and threshold (services/detector/scoring.py, threshold.json)
       -> MERGE into risk_curated.stream_alerts (alert_id stable for the same burst, new for a fresh one)
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

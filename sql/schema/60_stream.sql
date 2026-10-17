-- P6: tables for the near-real-time micro-batch stream path. New tables only; nothing existing is
-- altered. IF NOT EXISTS, so a re-run is harmless.
-- Rows are inserted with MERGE on the natural key (account_id, trade_id, alert_id), so a duplicate
-- delivery or a second replay of the same burst cannot create a second row.
CREATE TABLE IF NOT EXISTS `__P__.risk_raw.stream_accounts` (
  account_id STRING,
  created_ts TIMESTAMP,
  kyc_tier INT64,
  device_id STRING,
  funding_source_id STRING,
  run_id STRING,
  ingested_ts TIMESTAMP,
  source STRING
);

CREATE TABLE IF NOT EXISTS `__P__.risk_raw.stream_trades` (
  trade_id STRING,
  ts TIMESTAMP,
  market_id STRING,
  account_id STRING,
  side STRING,
  outcome STRING,
  price FLOAT64,
  size_usd FLOAT64,
  run_id STRING,
  ingested_ts TIMESTAMP,
  source STRING
)
PARTITION BY DATE(ts)
CLUSTER BY account_id;

-- Alerts of the frozen detector run over the stream tables. alert_id is stable across replays
-- of the same burst (a newer run updates run_id and created_ts); member_scores holds each
-- member's score, features and contributions as JSON text so the case view can be rebuilt.
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.stream_alerts` (
  alert_id STRING,
  ring_id STRING,
  scenario_hint STRING,
  member_accounts ARRAY<STRING>,
  n_members INT64,
  score FLOAT64,
  top_features STRING,
  evidence_trade_ids ARRAY<STRING>,
  evidence_edge_ids ARRAY<STRING>,
  threshold FLOAT64,
  status STRING,
  n_flagged INT64,
  source STRING,
  run_id STRING,
  created_ts TIMESTAMP,
  member_scores STRING
);

-- Append-only event log of the stream path. event_type: published, ingested, duplicate_dropped,
-- malformed, retry, dead_letter_candidate, scored, alert_raised.
CREATE TABLE IF NOT EXISTS `__P__.audit.stream_events` (
  run_id STRING,
  ts TIMESTAMP,
  event_type STRING,
  message_id STRING,
  delivery_attempt INT64,
  detail STRING
);

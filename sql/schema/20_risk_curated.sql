CREATE TABLE IF NOT EXISTS `__P__.risk_curated.accounts` (
  account_id STRING,
  created_ts TIMESTAMP,
  kyc_tier INT64,
  country STRING,
  device_id STRING,
  funding_source_id STRING,
  source STRING
);
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.markets` (
  market_id STRING,
  title STRING,
  resolve_ts TIMESTAMP,
  source STRING
);
-- Written by the P2 detector (services/detector). Replaces the Cloud Shell detect.py table,
-- which is kept as alerts_legacy (sql/migrations/p2_10_alerts_v2.sql).
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.alerts` (
  alert_id STRING,
  ring_id STRING,
  scenario_hint STRING,
  status STRING,
  n_flagged INT64,
  member_accounts ARRAY<STRING>,
  n_members INT64,
  score FLOAT64,
  top_features STRING,
  evidence_trade_ids ARRAY<STRING>,
  evidence_edge_ids ARRAY<STRING>,
  threshold FLOAT64,
  run_id STRING,
  created_ts TIMESTAMP
);
-- One row per account: score plus every feature and its contribution to the score.
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.account_scores` (
  account_id STRING,
  ring_id STRING,
  score FLOAT64,
  flagged BOOL,
  threshold FLOAT64,
  features JSON,
  contributions JSON,
  run_id STRING,
  created_ts TIMESTAMP
);

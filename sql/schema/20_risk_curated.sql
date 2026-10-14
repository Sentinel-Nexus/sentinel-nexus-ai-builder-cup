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
-- Written by the detector (P2); live copy was produced by the Cloud Shell detect.py.
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.alerts` (
  cluster_id STRING,
  account_id STRING,
  score INT64,
  reasons STRING,
  cluster_size INT64
);

CREATE TABLE IF NOT EXISTS `__P__.eval.ground_truth` (
  scenario_id STRING,
  scenario_type STRING,
  account_id STRING,
  market_id STRING
);
CREATE TABLE IF NOT EXISTS `__P__.eval.benchmark` (
  method STRING,
  flagged INT64,
  true_pos INT64,
  precision FLOAT64,
  recall FLOAT64,
  f1 FLOAT64,
  run_ts TIMESTAMP
);
CREATE TABLE IF NOT EXISTS `__P__.eval.benchmark_runs` (
  seed INT64,
  method STRING,
  flagged INT64,
  true_pos INT64,
  precision FLOAT64,
  recall FLOAT64,
  f1 FLOAT64,
  run_ts TIMESTAMP
);

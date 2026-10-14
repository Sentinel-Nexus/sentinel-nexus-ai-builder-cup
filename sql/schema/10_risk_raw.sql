CREATE TABLE IF NOT EXISTS `__P__.risk_raw.trades` (
  trade_id STRING,
  ts TIMESTAMP,
  market_id STRING,
  account_id STRING,
  side STRING,
  outcome STRING,
  price FLOAT64,
  size_usd FLOAT64,
  source STRING
);

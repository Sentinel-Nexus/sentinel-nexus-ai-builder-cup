-- Matched fills: a SELL and a BUY with the same market, outcome, time, price and size are the
-- two sides of one fill. Same rule as risk_graph.traded_with, but keeps both trade ids.
CREATE OR REPLACE TABLE `__OUT__.__PFX__det_fills` AS
SELECT
  s.trade_id AS sell_trade_id,
  b.trade_id AS buy_trade_id,
  s.account_id AS seller_id,
  b.account_id AS buyer_id,
  s.market_id,
  s.size_usd
FROM `__TRADES__` s
JOIN `__TRADES__` b
  ON s.market_id = b.market_id AND s.outcome = b.outcome AND s.ts = b.ts
 AND s.price = b.price AND s.size_usd = b.size_usd
WHERE s.side = 'SELL' AND b.side = 'BUY' AND s.account_id != b.account_id;

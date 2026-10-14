CREATE OR REPLACE TABLE `__P__.risk_graph.account_nodes` AS
  SELECT account_id, kyc_tier, country, created_ts FROM `__P__.risk_curated.accounts`;
CREATE OR REPLACE TABLE `__P__.risk_graph.device_nodes` AS
  SELECT DISTINCT device_id FROM `__P__.risk_curated.accounts`;
CREATE OR REPLACE TABLE `__P__.risk_graph.funding_nodes` AS
  SELECT DISTINCT funding_source_id FROM `__P__.risk_curated.accounts`;
CREATE OR REPLACE TABLE `__P__.risk_graph.market_nodes` AS
  SELECT market_id, resolve_ts FROM `__P__.risk_curated.markets`;
CREATE OR REPLACE TABLE `__P__.risk_graph.uses_device` AS
  SELECT account_id, device_id FROM `__P__.risk_curated.accounts`;
CREATE OR REPLACE TABLE `__P__.risk_graph.funded_by` AS
  SELECT account_id, funding_source_id FROM `__P__.risk_curated.accounts`;
CREATE OR REPLACE TABLE `__P__.risk_graph.traded_in` AS
  SELECT account_id, market_id, COUNT(*) AS n_trades, SUM(size_usd) AS total_usd
  FROM `__P__.risk_raw.trades` GROUP BY 1, 2;
-- matched fills: a SELL and a BUY with identical market, outcome, time, price and size are counterparties
CREATE OR REPLACE TABLE `__P__.risk_graph.traded_with` AS
  SELECT s.account_id AS seller_id, b.account_id AS buyer_id, COUNT(*) AS n_fills, SUM(s.size_usd) AS total_usd
  FROM `__P__.risk_raw.trades` s JOIN `__P__.risk_raw.trades` b
    ON s.market_id = b.market_id AND s.outcome = b.outcome AND s.ts = b.ts
   AND s.price = b.price AND s.size_usd = b.size_usd
  WHERE s.side = 'SELL' AND b.side = 'BUY' AND s.account_id != b.account_id
  GROUP BY 1, 2;

CREATE OR REPLACE PROPERTY GRAPH `__P__.risk_graph.market_graph`
  NODE TABLES (
    `__P__.risk_graph.account_nodes` AS Account KEY (account_id),
    `__P__.risk_graph.device_nodes`  AS Device KEY (device_id),
    `__P__.risk_graph.funding_nodes` AS FundingSource KEY (funding_source_id),
    `__P__.risk_graph.market_nodes`  AS Market KEY (market_id)
  )
  EDGE TABLES (
    `__P__.risk_graph.uses_device` AS UsesDevice
      KEY (account_id, device_id)
      SOURCE KEY (account_id) REFERENCES Account (account_id)
      DESTINATION KEY (device_id) REFERENCES Device (device_id),
    `__P__.risk_graph.funded_by` AS FundedBy
      KEY (account_id, funding_source_id)
      SOURCE KEY (account_id) REFERENCES Account (account_id)
      DESTINATION KEY (funding_source_id) REFERENCES FundingSource (funding_source_id),
    `__P__.risk_graph.traded_in` AS TradedIn
      KEY (account_id, market_id)
      SOURCE KEY (account_id) REFERENCES Account (account_id)
      DESTINATION KEY (market_id) REFERENCES Market (market_id),
    `__P__.risk_graph.traded_with` AS TradedWith
      KEY (seller_id, buyer_id)
      SOURCE KEY (seller_id) REFERENCES Account (account_id)
      DESTINATION KEY (buyer_id) REFERENCES Account (account_id)
  );

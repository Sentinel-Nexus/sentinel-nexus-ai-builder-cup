-- Account-to-account links from the three edge types of market_graph:
--   UsesDevice (shared device), FundedBy (shared funding source), TradedWith (matched fills).
-- Device and funding nodes shared by more than 25 accounts are hubs (an exchange wallet, a
-- cafe): they would chain unrelated accounts together, so they create no links.
CREATE OR REPLACE TABLE `__OUT__.__PFX__det_pairs` AS
WITH dev AS (
  SELECT device_id AS node, ARRAY_AGG(account_id) AS members
  FROM `__ACCOUNTS__` GROUP BY device_id HAVING COUNT(*) BETWEEN 2 AND 25
),
fund AS (
  SELECT funding_source_id AS node, ARRAY_AGG(account_id) AS members
  FROM `__ACCOUNTS__` GROUP BY funding_source_id HAVING COUNT(*) BETWEEN 2 AND 25
),
shared AS (
  SELECT 'UsesDevice' AS link_type, node, members FROM dev
  UNION ALL
  SELECT 'FundedBy', node, members FROM fund
),
node_pairs AS (
  SELECT LEAST(x, y) AS a, GREATEST(x, y) AS b, link_type
  FROM shared, UNNEST(members) AS x, UNNEST(members) AS y
  WHERE x != y
  GROUP BY a, b, link_type
)
SELECT a, b, link_type FROM node_pairs
UNION ALL
SELECT LEAST(seller_id, buyer_id), GREATEST(seller_id, buyer_id), 'TradedWith'
FROM `__OUT__.__PFX__det_fills`
GROUP BY 1, 2;

-- Distinct linked pairs (any link type) in both directions, for label propagation.
CREATE OR REPLACE TABLE `__OUT__.__PFX__det_dir` AS
WITH p AS (SELECT DISTINCT a, b FROM `__OUT__.__PFX__det_pairs`)
SELECT a AS src, b AS dst FROM p
UNION ALL
SELECT b, a FROM p;

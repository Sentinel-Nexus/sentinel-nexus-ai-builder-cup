-- One row per account: scenario-agnostic features plus evidence references.
-- Constants: final window before resolution = 6 h; "fresh" = account age at first trade
-- below 168 h; hub limit 25 (see det_20_links.sql).
CREATE OR REPLACE TABLE `__OUT__.__PFX__det_features` AS
WITH
t AS (
  SELECT tr.trade_id, tr.account_id, tr.market_id, tr.ts, tr.side, tr.outcome, tr.size_usd,
         TIMESTAMP_DIFF(m.resolve_ts, tr.ts, MINUTE) / 60.0 AS hours_to_resolve
  FROM `__TRADES__` tr JOIN `__MARKETS__` m USING (market_id)
),
-- Market baselines: robust location and scale (median, 1.4826 * MAD) of log size and of
-- log hours-to-resolution, over all trades in the market.
base0 AS (
  SELECT market_id,
    APPROX_QUANTILES(LN(size_usd), 100)[OFFSET(50)] AS med_ls,
    APPROX_QUANTILES(LN(1 + GREATEST(hours_to_resolve, 0)), 100)[OFFSET(50)] AS med_lh
  FROM t GROUP BY market_id
),
base AS (
  SELECT t.market_id, ANY_VALUE(b.med_ls) AS med_ls, ANY_VALUE(b.med_lh) AS med_lh,
    1.4826 * APPROX_QUANTILES(ABS(LN(t.size_usd) - b.med_ls), 100)[OFFSET(50)] AS sd_ls,
    1.4826 * APPROX_QUANTILES(
      ABS(LN(1 + GREATEST(t.hours_to_resolve, 0)) - b.med_lh), 100)[OFFSET(50)] AS sd_lh
  FROM t JOIN base0 b USING (market_id) GROUP BY t.market_id
),
tz AS (
  SELECT t.*,
    (LN(t.size_usd) - b.med_ls) / NULLIF(b.sd_ls, 0) AS size_z,
    -(LN(1 + GREATEST(t.hours_to_resolve, 0)) - b.med_lh) / NULLIF(b.sd_lh, 0) AS late_z
  FROM t JOIN base b USING (market_id)
),
acct_trades AS (
  SELECT account_id, COUNT(*) AS n_trades, AVG(size_z) AS mean_size_z,
    AVG(IF(side = 'BUY', late_z, NULL)) AS mean_late_z,
    SUM(IF(side = 'BUY', size_usd, 0)) AS buy_usd, MIN(ts) AS first_ts
  FROM tz GROUP BY account_id
),
late AS (
  SELECT account_id, market_id, outcome, SUM(size_usd) AS late_usd,
    ARRAY_AGG(trade_id ORDER BY size_usd DESC LIMIT 20) AS trade_ids
  FROM t WHERE side = 'BUY' AND hours_to_resolve BETWEEN 0 AND 6
  GROUP BY account_id, market_id, outcome
),
late_best AS (
  SELECT account_id,
    ARRAY_AGG(STRUCT(late_usd, trade_ids) ORDER BY late_usd DESC LIMIT 1)[OFFSET(0)] AS s
  FROM late GROUP BY account_id
),
age AS (
  SELECT a.account_id, a.device_id, a.funding_source_id,
    GREATEST(TIMESTAMP_DIFF(f.first_ts, a.created_ts, MINUTE) / 60.0, 0) AS age_h
  FROM `__ACCOUNTS__` a LEFT JOIN acct_trades f USING (account_id)
),
fund_grp AS (
  SELECT funding_source_id, COUNT(*) AS n, COUNTIF(age_h < 168) AS n_fresh
  FROM age GROUP BY funding_source_id
),
dev_grp AS (SELECT device_id, COUNT(*) AS n FROM age GROUP BY device_id),
comp_fresh AS (
  SELECT c.ring_id, COUNTIF(a.age_h < 168) AS n_fresh
  FROM `__OUT__.__PFX__det_components` c JOIN age a USING (account_id) GROUP BY c.ring_id
),
fill_side AS (
  SELECT seller_id AS account_id, buyer_id AS partner, sell_trade_id AS trade_id, size_usd,
         'sell' AS dir
  FROM `__OUT__.__PFX__det_fills`
  UNION ALL
  SELECT buyer_id, seller_id, buy_trade_id, size_usd, 'buy'
  FROM `__OUT__.__PFX__det_fills`
),
fill_stats AS (
  SELECT account_id, COUNT(*) AS fill_n, COUNT(DISTINCT partner) AS fill_partners,
    SUM(IF(dir = 'buy', size_usd, 0)) AS fill_buy_usd,
    SUM(IF(dir = 'sell', size_usd, 0)) AS fill_sell_usd,
    ARRAY_AGG(trade_id ORDER BY size_usd DESC LIMIT 20) AS trade_ids,
    ARRAY_AGG(DISTINCT IF(dir = 'sell', CONCAT('TradedWith:', account_id, '>', partner),
                          CONCAT('TradedWith:', partner, '>', account_id)) LIMIT 20) AS edge_ids
  FROM fill_side GROUP BY account_id
)
SELECT
  c.account_id, c.ring_id, c.comp_size, c.comp_edges, c.comp_density,
  dg.n AS device_group, fg.n AS funding_group,
  IFNULL(fs.fill_n, 0) AS fill_n, IFNULL(fs.fill_partners, 0) AS fill_partners,
  IF(IFNULL(fs.fill_buy_usd, 0) + IFNULL(fs.fill_sell_usd, 0) > 0,
     2 * LEAST(fs.fill_buy_usd, fs.fill_sell_usd) / (fs.fill_buy_usd + fs.fill_sell_usd), 0)
    AS recip,
  a.age_h AS age_first_trade_h,
  IF(a.age_h < 168 AND fg.n BETWEEN 2 AND 25, fg.n_fresh - 1, 0) AS fresh_fanin,
  IF(a.age_h < 168, cf.n_fresh - 1, cf.n_fresh) AS fresh_linked,
  IFNULL(lb.s.late_usd, 0) AS late_usd,
  IF(atr.buy_usd > 0, IFNULL(lb.s.late_usd, 0) / atr.buy_usd, 0) AS late_share,
  IFNULL(atr.mean_size_z, 0) AS mean_size_z,
  IFNULL(atr.mean_late_z, 0) AS mean_late_z,
  IFNULL(atr.n_trades, 0) AS n_trades,
  IFNULL(atr.buy_usd, 0) AS buy_usd,
  ARRAY_CONCAT(IFNULL(fs.trade_ids, []), IFNULL(lb.s.trade_ids, [])) AS evidence_trade_ids,
  ARRAY_CONCAT(
    IF(dg.n BETWEEN 2 AND 25, [CONCAT('UsesDevice:', c.account_id, '>', a.device_id)], []),
    IF(fg.n BETWEEN 2 AND 25, [CONCAT('FundedBy:', c.account_id, '>', a.funding_source_id)], []),
    IFNULL(fs.edge_ids, [])
  ) AS evidence_edge_ids
FROM `__OUT__.__PFX__det_components` c
JOIN age a USING (account_id)
JOIN fund_grp fg ON fg.funding_source_id = a.funding_source_id
JOIN dev_grp dg ON dg.device_id = a.device_id
JOIN comp_fresh cf ON cf.ring_id = c.ring_id
LEFT JOIN acct_trades atr ON atr.account_id = c.account_id
LEFT JOIN late_best lb ON lb.account_id = c.account_id
LEFT JOIN fill_stats fs ON fs.account_id = c.account_id;

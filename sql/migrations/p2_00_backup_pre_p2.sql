-- One-off safety copies taken before the ADR 0002 reload (WRITE_TRUNCATE) in P2.
-- IF NOT EXISTS: re-running never overwrites a backup with newer data.
CREATE TABLE IF NOT EXISTS `__P__.eval.trades_pre_p2` AS
  SELECT * FROM `__P__.risk_raw.trades`;
CREATE TABLE IF NOT EXISTS `__P__.eval.accounts_pre_p2` AS
  SELECT * FROM `__P__.risk_curated.accounts`;
CREATE TABLE IF NOT EXISTS `__P__.eval.markets_pre_p2` AS
  SELECT * FROM `__P__.risk_curated.markets`;
CREATE TABLE IF NOT EXISTS `__P__.eval.ground_truth_pre_p2` AS
  SELECT * FROM `__P__.eval.ground_truth`;
CREATE TABLE IF NOT EXISTS `__P__.eval.alerts_pre_p2` AS
  SELECT * FROM `__P__.risk_curated.alerts`;

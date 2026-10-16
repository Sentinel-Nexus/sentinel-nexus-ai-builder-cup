-- P2: replace the Cloud Shell detect.py alerts table with the detector schema.
-- The old table is copied to alerts_legacy first (never overwritten), then dropped.
-- Only acts while the old layout (cluster_id column) is still in place, so it is re-runnable.
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.alerts_legacy` AS
  SELECT * FROM `__P__.risk_curated.alerts`;

IF EXISTS (
  SELECT 1 FROM `__P__.risk_curated`.INFORMATION_SCHEMA.COLUMNS
  WHERE table_name = 'alerts' AND column_name = 'cluster_id'
) THEN
  DROP TABLE `__P__.risk_curated.alerts`;
END IF;

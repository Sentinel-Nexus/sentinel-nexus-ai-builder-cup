-- Baseline: flag every account whose funding source is shared by 5 or more accounts.
CREATE OR REPLACE TABLE `__OUT__.__PFX__baseline_flags` AS
SELECT a.account_id
FROM `__ACCOUNTS__` a
JOIN (
  SELECT funding_source_id FROM `__ACCOUNTS__` GROUP BY funding_source_id HAVING COUNT(*) >= 5
) g USING (funding_source_id);

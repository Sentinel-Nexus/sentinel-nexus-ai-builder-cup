-- P4: workbench lane of a cached case (escalated | agent_cleared | needs_analyst).
-- Existing rows keep NULL; the service derives the lane from `decision` when it is NULL.
ALTER TABLE `__P__.risk_curated.case_reports` ADD COLUMN IF NOT EXISTS lane STRING;

-- P2 hardening: review band. alerts gets status ('flagged' | 'needs_review') and n_flagged.
-- Re-runnable. Existing rows keep NULL until score-live --apply rewrites the table.
ALTER TABLE `__P__.risk_curated.alerts` ADD COLUMN IF NOT EXISTS status STRING;
ALTER TABLE `__P__.risk_curated.alerts` ADD COLUMN IF NOT EXISTS n_flagged INT64;

-- P4: let the agent service (Cloud Run, runs as sa-agent) write its own three tables and nothing else.
-- Table-level grants keep sa-agent read-only on the detector and graph tables.
-- Applied 2026-10-17 (table-level grants only; verified on each table's IAM policy).
-- Apply with: python scripts/run_sql.py sql/iam/sa_agent_agent_tables.sql --apply   (project owner)
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.audit.agent_events`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_curated.case_reports`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_curated.case_decisions`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";

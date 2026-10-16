-- P4: let the agent service (Cloud Run, runs as sa-agent) write its own two tables and nothing else.
-- Table-level grants keep sa-agent read-only on the detector and graph tables.
-- Not applied yet: the service runs locally in P4 under the operator's credentials.
-- Apply with: python scripts/run_sql.py sql/iam/sa_agent_agent_tables.sql --apply   (project owner)
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.audit.agent_events`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_curated.case_reports`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";

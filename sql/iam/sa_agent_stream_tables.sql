-- P6: let the service (Cloud Run, runs as sa-agent) write exactly the four stream tables.
-- Table-level grants only: no dataset or project binding, so sa-agent stays read-only everywhere
-- else (risk_raw.trades, risk_curated.alerts, ...). Run after sql/schema/60_stream.sql.
-- Applied by: python infra/stream_setup.py --apply   (project owner)
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_raw.stream_accounts`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_raw.stream_trades`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.risk_curated.stream_alerts`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";
GRANT `roles/bigquery.dataEditor`
ON TABLE `__P__.audit.stream_events`
TO "serviceAccount:sa-agent@__P__.iam.gserviceaccount.com";

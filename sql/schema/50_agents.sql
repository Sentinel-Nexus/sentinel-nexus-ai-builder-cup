-- P4 agents. Every step, tool call and verdict of an investigation run (append only).
-- event_type: step | tool_call | llm_call | verdict | limit | cache | error | approval | webhook_mock
-- engine: graph engine for tool calls (gql | fallback), table (stored detector output) or keyword
-- cached: true when the tool answer came from a cache; it is never presented as live
-- args, detail: JSON text
CREATE TABLE IF NOT EXISTS `__P__.audit.agent_events` (
  event_id STRING,
  run_id STRING,
  seq INT64,
  ts TIMESTAMP,
  alert_id STRING,
  agent STRING,
  event_type STRING,
  tool STRING,
  args STRING,
  summary STRING,
  evidence_ids ARRAY<STRING>,
  engine STRING,
  cached BOOL,
  latency_ms INT64,
  tokens INT64,
  detail STRING
);
-- Cached case reports. Append only; the latest created_ts per alert_id is the current case.
-- decision: escalate | clear | uncertain
-- report: JSON of the full case report (with meta)
-- retrieval: policy retrieval used (keyword | vertex_ai_search)
-- budget_status: ok | budget_exhausted
-- source: live | precompute | eval
-- lane: escalated | agent_cleared | needs_analyst (added last by sql/migrations/p4_20_case_lane.sql)
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.case_reports` (
  case_id STRING,
  alert_id STRING,
  ring_id STRING,
  alert_status STRING,
  run_id STRING,
  created_ts TIMESTAMP,
  decision STRING,
  confidence FLOAT64,
  report STRING,
  ledger_ids ARRAY<STRING>,
  models STRING,
  retrieval STRING,
  graph_engines STRING,
  budget_status STRING,
  tool_calls INT64,
  tokens_total INT64,
  latency_ms INT64,
  forced BOOL,
  source STRING,
  lane STRING
);

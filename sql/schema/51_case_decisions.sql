-- P4: analyst decisions on agent cases (append only; the latest per alert_id wins).
-- decision: override_to_escalate | confirm_clear
-- previous_lane: the lane the agent put the case in when the analyst decided
CREATE TABLE IF NOT EXISTS `__P__.risk_curated.case_decisions` (
  decision_id STRING,
  alert_id STRING,
  case_run_id STRING,
  ts TIMESTAMP,
  analyst STRING,
  decision STRING,
  note STRING,
  previous_lane STRING
);

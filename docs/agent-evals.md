# Agent evals (P4)

Run 2026-10-16 18:20 UTC on seed 42 live data, models {'investigator': 'gemini-2.5-flash', 'drafter': 'gemini-2.5-flash', 'critic': 'gemini-2.5-flash'}, policy retrieval `keyword`.

Cases: the real detector alerts, the review-band attacker and the three decoy groups as `needs_review` alerts (injected at their real detector scores, see `evals/agents/cases.py`), and one adversarial run. Labels are used only for scoring.

| Metric | Result |
|---|---|
| Attacker recall (per account) | 29/39 (74%) |
| - wash_trading | 8/8 (100%) |
| - coordinated_accumulation | 6/6 (100%) |
| - mule_funding | 15/25 (60%) |
| Evasion cases caught (left the shared funding source) | 5/15 (33%) |
| Claim support rate (final / first pass) | 71% / 55% of 31 claims |
| Critic rejection rate | 23/58 (40%) |
| False escalations, decoy groups | 0/2 groups, 0/13 accounts |
| Borderline attackers escalated | 0/1 |
| Decoy groups cleared | 2/2 |
| Alerts with an accepted merge proposal | 0 |
| Disagreed with detector hint | 1 (EV_C004) |
| Avg tokens / tool calls per run | 21651.4 / 3.1 |
| Runs that hit a budget | 0 |
| Missed attackers | M005, M006, M007, M011, M013, M014, M016, M018, M023, M025 |

Latency (ms) by graph engine and cache state:

| Group | n | avg | max |
|---|---|---|---|
| engine=gql, graph_live | 7 | 49019.7 | 67390 |

Per case:

| Alert | Kind | Decision | Tokens | Latency ms | Engines |
|---|---|---|---|---|---|
| AL_3df9da0276 | real | escalate | 32222 | 66000 | gql |
| AL_5a1a959872 | real | escalate | 10600 | 21453 | gql |
| AL_fa9727ee1e | real | escalate | 32318 | 47467 | gql |
| AL_584807441a | real | escalate | 28241 | 44063 | gql |
| EV_C004 | borderline_attacker | clear | 13178 | 67390 | gql |
| EV_DG1_household | decoy | clear | 14235 | 53250 | gql |
| EV_DG2_exchange_cluster | decoy | clear | 20766 | 43515 | gql |

## Status of this run

**Partial.** Completed: AL_3df9da0276, AL_5a1a959872, AL_fa9727ee1e, AL_584807441a, EV_C004, EV_DG1_household, EV_DG2_exchange_cluster.

Not completed: EV_DG3_busy_honest (stopped while running) and the adversarial run (not started). Metrics above cover the completed cases only.
The adversarial test passed in run 1 (`docs/results/agent_evals_run1.json`, 2/2 rejected, 0 leaked) and was not re-run after the Critic change.

# Agent evals (P4)

Run 2026-10-16 19:10 UTC on seed 42 live data, models {'investigator': 'gemini-2.5-flash', 'drafter': 'gemini-2.5-flash', 'critic': 'gemini-2.5-flash'}, policy retrieval `keyword`.

Cases: the real detector alerts, the review-band attacker and the three decoy groups as `needs_review` alerts (injected at their real detector scores, see `evals/agents/cases.py`), and one adversarial run. Labels are used only for scoring.

| Metric | Result |
|---|---|
| Attacker recall (per account) | 39/39 (100%) |
| - wash_trading | 8/8 (100%) |
| - coordinated_accumulation | 6/6 (100%) |
| - mule_funding | 25/25 (100%) |
| Evasion cases caught (left the shared funding source) | 15/15 (100%) |
| Investigator claims kept by the Critic (final / first pass) | 63% / 51% of 35 claims |
| Claims in the final reports with resolvable evidence ids | 13/13 (100%), measured on 4 of 8 cases |
| Critic rejection rate | 30/67 (45%) |
| False escalations, decoy groups | 0/3 groups, 0/23 accounts |
| Borderline attackers escalated | 0/1 |
| Decoy groups cleared | 2/3 |
| Alerts with an accepted merge proposal | 0 |
| Disagreed with detector hint | 1 (EV_C004) |
| Avg tokens / tool calls per run | 21688.4 / 3.0 |
| Runs that hit a budget | 0 |
| Adversarial: injected unsupported claims rejected | 3/3, leaked into report 0 (PASS) |

Latency (ms) by graph engine and cache state:

| Group | n | avg | max |
|---|---|---|---|
| case_cache_hit | 5 | 0.0 | 0.0 |
| engine=gql, graph_live | 8 | 43365.0 | 67390 |

Per case:

| Alert | Kind | Decision | Tokens | Latency ms | Engines |
|---|---|---|---|---|---|
| AL_3df9da0276 | real | escalate | 27769 | 50078 | gql |
| AL_584807441a | real | escalate | 24816 | 27469 | gql |
| AL_5a1a959872 | real | escalate | 10329 | 18188 | gql |
| AL_fa9727ee1e | real | escalate | 33104 | 42311 | gql |
| EV_C004 | borderline_attacker | clear | 13178 | 67390 | gql |
| EV_DG1_household | decoy | clear | 14235 | 53250 | gql |
| EV_DG2_exchange_cluster | decoy | clear | 20766 | 43515 | gql |
| EV_DG3_busy_honest | decoy | uncertain | 29310 | 44719 | gql |

## How the numbers are defined

- **Case members** are deterministic: the detector alert's members plus the members of `ring_subgraph` (evaded accounts linked only through devices or fills). The model decides escalate / clear / uncertain for the case and supplies rationale and evidence ids; it no longer lists accounts. It can carve an account out only with a supported `exclusion` claim.
- **Attacker recall (per account)** = attackers in escalated reports / 39. Because the decision now covers the whole ring, this equals the detector's alert coverage (39 of 39 attackers are in the four flagged alerts, one of them, C004, scored in the review band) times the agent escalating each alert. It is a ring-level result, not per-account judgement, and `exclusions` was never needed on this data.
- **Investigator claims kept** = claims the Critic accepted / claims the Investigator produced. Rejected claims are removed from the report. This measures the Investigator.
- **Claims in the final reports with resolvable evidence ids** = report claims whose ids all resolve in that run's ledger / report claims. It is 100% by construction (`pipeline._final_claims` drops anything else) and re-checked per run. A removed claim is listed in the report as `removed_claims` with the Critic's reason and an `unverified_note`; the summary cannot cite it.
- **Lanes**: `escalated`, `agent_cleared` (reason and evidence stored, analyst can override) and `needs_analyst` (uncertain).

## Adversarial test (current pipeline)

Alert AL_584807441a, 3 injected claims, 3 rejected, 0 leaked into the report (PASS).

| Injected claim | Cited ids | Stage | Critic verdict |
|---|---|---|---|
| W001 received funds from a mixer wallet shared with 40 other accounts | FundedBy:W001>FX_NOT_REAL | ledger | c7: rejected (ledger) evidence ids not in this run's tool-call ledger: ['FundedBy:W001>FX_NOT_REAL'] |
| Every account in the ring is KYC tier 3 and older than 400 days | UsesDevice:W001>DW1 | ledger | c8: rejected (ledger) numbers not in the cited rows: [400.0] |
| Accounts W001 and W002 were created within one hour of each other | UsesDevice:W001>DW1, UsesDevice:W002>DW2 | llm | c9: rejected (llm) The claim states that accounts W001 and W002 were created within one hour of each other. The provided edge IDs only show that W001 uses DW1 a |

## Serving a cached case

Service-side time of a cache hit over 100 repeats: median 0.066 ms, worst 0.212 ms (`docs/results/cache_latency.json`, `perf_counter`, excludes HTTP).

## Caveats

- Decoy and borderline cases are injected `needs_review` alerts built at the accounts' real detector scores (the decoys score far below the review band), so they test the investigation, not the detector. Seed 42 has no detector `needs_review` alert.
- Only the four real alerts were re-run with the final pipeline; the four `EV_*` cases are from the previous pipeline (before deterministic membership and removed-claim bookkeeping). Their decisions are unaffected by the change in membership but their report text was not kept.
- `EV_C004`, the one attacker that scored in the review band, was **cleared** by the agent, which also disagreed with the detector's hint. The threshold and decision logic were not changed to catch it. `EV_DG3_busy_honest` ended `uncertain` (not escalated).
- Latency varies with Vertex: a send-back call once took 37 s, and a single model call took 346 s during a machine suspend (the deadline watchdog now polls the wall clock). The slowest recorded case is 67.4 s.
- Dollar figures use third-party prices (+-30%).

## Run history

| Run | What | Outcome |
|---|---|---|
| First attempt (`.cache/eval_run.log`) | old pipeline | 2 of 9 cases; the mule ring crashed on a truncated JSON answer; a Drafter call failed on 429 |
| Run 1 (`docs/results/agent_evals_run1.json`) | Flash critic, 12 evidence rows | 8 cases + adversarial; recall 12/39 |
| Partial run | Critic sees all edge ids | 7 of 9 cases, stopped by the operator; recall 29/39 |
| Final | deterministic members; DG3 and adversarial added; 4 real alerts re-run | 8 cases + adversarial (3 claims) complete |

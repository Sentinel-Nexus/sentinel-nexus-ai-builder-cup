# Agent evals (P4)

Run 2026-10-16 19:10 UTC on seed 42 live data, models {'investigator': 'gemini-2.5-flash', 'drafter': 'gemini-2.5-flash', 'critic': 'gemini-2.5-flash'}, policy retrieval `keyword`.

Pipeline version: **v1** for the eight rows below (the model listed the case members; before deterministic membership). The four real alerts were later re-run on the final pipeline **v2**; the four injected cases were re-run on v2 on 2026-10-17, see "Results by run" below. Every `EV_` figure in this table belongs to this first run only.

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
| False escalations, decoy groups (first run 2026-10-16, v1) | 0/3 groups, 0/23 accounts |
| Borderline attackers escalated (first run) | 0/1 |
| Decoy groups cleared (first run) | 2/3 |
| False escalations, decoy groups (re-run 2026-10-17, v2) | 2/3 groups, 18 of 23 decoy accounts (plus 6 linked accounts) |
| Borderline attackers escalated (re-run) | 1/1 |
| Decoy groups cleared (re-run) | 1/3 |
| Alerts with an accepted merge proposal | 0 |
| Disagreed with detector hint (first run) | 1 (EV_C004) |
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

## Results by run (decoys and the borderline attacker)

Same four injected cases each time, one run per case per row. Nothing was tuned between rows.

| Run | Date | Pipeline | EV_C004 (borderline attacker) | EV_DG1 household | EV_DG2 exchange cluster | EV_DG3 busy honest | Decoy groups falsely escalated | Borderline attacker escalated |
|---|---|---|---|---|---|---|---|---|
| First evaluation run | 2026-10-16 | v1, model-listed members | clear | clear | clear | uncertain | 0/3 (0/23 accounts) | 0/1 |
| Injected re-run | 2026-10-17 | v2 final, deterministic members | escalate | clear | escalate | escalate | 2/3 (18 of 23 decoy accounts, plus 6 linked accounts) | 1/1 |

Reading the two rows together: agent decisions on borderline and decoy cases are **unstable**. The same cases flipped
between runs, so neither row alone is "the" false-escalation rate. The earlier "0 of 23" and "2 of 3 cleared" figures
belong to the first run only.

**Where real alerts come from.** Real alerts come from the detector, which flagged **none of the 23 decoy accounts**
(0 of 23 in `docs/benchmark.md`, seed 42; the flat rule flagged 8). The `EV_` cases bypass the detector on purpose: they are
injected `needs_review` alerts built at the accounts' real scores, to test the agents in isolation. So the false escalations
above are what the agents do when they are handed a decoy group; they are not false alerts the system raised.

A repeated run (3 runs per case on more decoy cases) has **not** been run for this release. Agent decisions on injected
decoys were unstable across runs; real alerts come from the detector, which flagged none of the decoys.

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
- In the first run only the four real alerts were on the final pipeline; the four `EV_*` cases were on v1 (before deterministic membership and removed-claim bookkeeping). They were re-run on v2 on 2026-10-17 with different results (see above).
- `EV_C004`, the one attacker that scored in the review band, was **cleared** by the agent in the first run (2026-10-16, v1), which also disagreed with the detector's hint, and **escalated** in the re-run (2026-10-17, v2). `EV_DG3_busy_honest` ended `uncertain` in the first run and `escalate` in the re-run. The threshold and decision logic were not changed between runs.
- Latency varies with Vertex: a send-back call once took 37 s, and a single model call took 346 s during a machine suspend (the deadline watchdog now polls the wall clock). The slowest recorded case is 67.4 s.
- Dollar figures use third-party prices (+-30%).

## Run history

| Run | What | Outcome |
|---|---|---|
| First attempt (`.cache/eval_run.log`) | old pipeline | 2 of 9 cases; the mule ring crashed on a truncated JSON answer; a Drafter call failed on 429 |
| Run 1 (`docs/results/agent_evals_run1.json`) | Flash critic, 12 evidence rows | 8 cases + adversarial; recall 12/39 |
| Partial run | Critic sees all edge ids | 7 of 9 cases, stopped by the operator; recall 29/39 |
| Final | deterministic members; DG3 and adversarial added; 4 real alerts re-run | 8 cases + adversarial (3 claims) complete |

## Streaming path (P6): what it proves and what it does not

No Gemini call is made by the streaming path or by its evaluation (`scripts/stream_eval.py`). The
agents never see a streamed alert unless an analyst clicks "Investigate live", which is the existing
rate-limited path. Results and method: `docs/STATUS.md` (section "P6 streaming") and
`docs/results/stream_eval.json`.

**It proves**

- The frozen detector, unchanged (same SQL, weights and threshold 43.0), also works on events that
  arrive as Pub/Sub messages and are scored in micro-batches: on a held-out burst from a new seed
  (6001), the 6-account wash ring is flagged as one alert and the 4-account innocent household is not.
- The pipeline is idempotent: replaying the same burst adds no trade, account or alert row, and a
  duplicate delivery is dropped.
- A malformed message is retried and ends in the dead-letter topic after 5 attempts; it is never written.
- The measured delay from the last publish to the alert, and how it splits (publish, micro-batches,
  one scoring query of roughly 25 to 30 seconds).

**It does not prove**

- Detection quality in general. It is one small burst (1 ring, 1 household, 30 background accounts).
  The ring scored 44.37 against the 43.0 threshold, a thin margin of the same kind `docs/benchmark.md`
  reports for the batch detector. One burst cannot give precision or recall.
- Scale or low latency. This is a near-real-time micro-batch path: the delay is dominated by one
  BigQuery scoring script (about 20 statements), measured at roughly half a minute for 40 accounts.
  A larger stream would need a different scoring design.
- Agent behaviour on streamed alerts. No agent case was run on one for this phase.
- Exactly-once delivery. Pub/Sub delivers at least once; idempotence comes from the in-memory set and
  the MERGE on the natural key. Delivery order is not guaranteed and nothing depends on it.
- Anything about real traders: all accounts, devices and trades are synthetic.

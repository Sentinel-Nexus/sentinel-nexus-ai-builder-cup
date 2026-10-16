# services/app

FastAPI service that runs the investigation agents (Google ADK 2.12 on Vertex AI Gemini).

```
uvicorn services.app.main:app --port 8080     # needs ADC; runs as sa-agent on Cloud Run
```

| Endpoint | What |
|---|---|
| `GET /health` | `ready` once the graph service, table caches and model clients are warm; model ids |
| `GET /alerts` | detector alerts (`flagged` and `needs_review`) and whether a case is cached |
| `GET /cases/{alert_id}` | cached case report (404 if none) |
| `POST /cases/{alert_id}/investigate?force=false` | server-sent events: every step, then `result` with `status` `ok` / `timed_out` / `error`. Cached case unless `force=true` (rate limited: 6/h, 30/day, 429 with `Retry-After`). At most 2 investigations run at once; the rest get a `queued` event |
| `POST /cases/{alert_id}/approve` | analyst decision `{analyst, decision: approve\|reject, note}`; audited. The mocked webhook fires only on approval of an escalation, and never leaves the process |

## Pipeline

prefetch (3 tools in parallel) -> `Investigator` (more tools only if needed, <= 8 calls in total)
-> `Critic` -> one send-back for rejected claims -> policy retrieval (visible step) -> `Drafter`
(picks and explains the clauses). Orchestration is plain Python (`pipeline.py`), the three
agents are ADK `LlmAgent`s (`agents.py`).

- **Models** (verified by real calls in us-central1): `gemini-2.5-flash` for all three. A Flash vs
  Pro Critic measurement (`evals/agents/critic_ab.py`, `docs/results/critic_ab.json`) showed no
  accuracy difference and Pro was slower, so Pro is not used. `gemini-3-pro-preview` returned 404
  for this project. The global endpoint did not reduce 429s in a burst test, so the service stays
  on `us-central1`.
- **Tools**: `get_alert_context`, `graph` (P3 `ring_subgraph` / `neighbors`), `trades_for`,
  `account_profile`, `check_policy`, `related_rings`. Every call goes through
  `RunContext.call_tool`: call cap, timing, ledger, audit. The first three are prefetched in
  parallel by the orchestrator through the same wrappers (they count against the 8 calls).
- **Evidence ledger** (`ledger.py`): every id a tool returned in this run, in the P3 and detector
  formats (`UsesDevice:<a>><d>`, `FundedBy:..`, `TradedWith:..`, `TradedIn:<a>><m>`, `R_<lowest id>`,
  trade ids, `Account:`, `Market:`, `Score:<account>`, clause ids). An id that a detector only
  listed is replaced by the tool row once that row arrives.
- **Critic** (`critic.py`): stage 1 is deterministic: no evidence ids, an id not in this run's
  ledger, or a decimal / 3+ digit number in the claim that is not in the cited rows -> rejected
  and the LLM never sees it; merge proposals also need evidence from both rings and a ring that
  `related_rings` returned. Stage 2 (Flash) checks the rows support the claim. A rejected claim
  goes back to the Investigator once; if it fails again it is listed as unsupported.
- **Decision guards**: an escalation needs named accounts and two supported claims that cite more
  than policy clauses (SP-4.1), else it is downgraded to `uncertain`. `clear` always means
  `no_action`. Confidence is capped by the claim support rate and by an exhausted budget.
- **Hard limits** (`config.py`): 8 tool calls, `max_output_tokens` per agent, a token ceiling per
  agent, 60k tokens in total, **120 s per case** (the run ends as `timed_out` and is not cached).
  Hitting a token or call limit marks the run `budget_exhausted`.
- **Quota behaviour**: one Gemini client per model for the life of the process, one event loop for
  all ADK calls, 2 concurrent LLM calls, exponential backoff with jitter on 429/503 (Vertex sends no
  `Retry-After` header; the model client does not read one). Every retry is an audit event.
- **Engine and cache are never hidden**: graph tool events carry `engine` (`gql`/`fallback`) and
  `cached`; table tools report `engine: "table"` (stored detector output). `GraphService.warm()`
  and a one-token model warm-up run at start-up.
- **Audit**: every step, tool call, LLM call (latency, tokens in/out/thinking), retry and verdict
  is a row in `audit.agent_events`, written by a background worker so a response never waits for
  it. Cases are cached in `risk_curated.case_reports` (append only, latest wins).
- **Policy retrieval** is keyword (BM25-style) over `data/policies/`; Vertex AI Search
  (`discoveryengine`) is not enabled in this project. The mode is stored on every case.
- `account_profile` returns sharing counts, not raw device or funding ids, because generated ids
  such as `FE_<account>` would tell the model which attackers evaded.

## Tests and evals

`pytest tests/unit/test_agents_pipeline.py` runs the whole pipeline with a scripted backend (no
Gemini). Evals: `python -m evals.agents.run` (dry run) / `--apply --max-minutes 20`. Precompute:
`python scripts/precompute_cases.py` (dry run) / `--apply`. Both print a cost estimate first and
have a hard cap on investigations.

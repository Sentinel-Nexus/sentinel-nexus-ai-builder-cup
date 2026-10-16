# services/app

FastAPI service that runs the investigation agents (Google ADK 2.12 on Vertex AI Gemini).

```
uvicorn services.app.main:app --port 8080     # needs ADC; runs as sa-agent on Cloud Run
```

| Endpoint | What |
|---|---|
| `GET /health` | `ready` once the graph service and table caches are warm; model ids |
| `GET /alerts` | detector alerts (`flagged` and `needs_review`) and whether a case is cached |
| `GET /cases/{alert_id}` | cached case report (404 if none) |
| `POST /cases/{alert_id}/investigate?force=false` | server-sent events: every step, then `result`. Cached case unless `force=true` (rate limited: 6/h, 30/day, 429 with `Retry-After`) |
| `POST /cases/{alert_id}/approve` | analyst decision `{analyst, decision: approve\|reject, note}`; audited. The mocked webhook fires only on approval of an escalation, and never leaves the process |

## Pipeline

`Investigator` (tools, <= 8 calls) -> `Critic` -> one send-back for rejected claims -> `Policy`
(keyword retrieval, clause ids) -> `Drafter`. Orchestration is plain Python (`pipeline.py`), the
four agents are ADK `LlmAgent`s (`agents.py`).

- **Models** (verified by real calls in us-central1): `gemini-2.5-flash` for Investigator, Policy and
  Drafter, `gemini-2.5-pro` for the Critic. `gemini-3-pro-preview` returned 404 for this project.
- **Tools**: `get_alert_context`, `graph` (P3 `ring_subgraph` / `neighbors`), `trades_for`,
  `account_profile`, `check_policy`, `related_rings`. Every call goes through
  `RunContext.call_tool`: call cap, timing, ledger, audit.
- **Evidence ledger** (`ledger.py`): every id a tool returned in this run, in the P3 and detector
  formats (`UsesDevice:<a>><d>`, `FundedBy:..`, `TradedWith:..`, `TradedIn:<a>><m>`, `R_<lowest id>`,
  trade ids, `Account:`, `Market:`, `Score:`, clause ids). The Critic resolves claims against it.
- **Critic** (`critic.py`): stage 1 rejects any claim with no evidence ids or with an id not in
  this run's ledger (the LLM never sees it); merge proposals also need evidence from both rings and
  a ring that `related_rings` returned. Stage 2 (Pro) checks the rows support the claim. A
  rejected claim goes back to the Investigator once; if it fails again it is listed as unsupported.
- **Decision guards**: an escalation needs two supported claims that cite more than policy clauses
  (SP-4.1), else it is downgraded to `uncertain`. `clear` always means `no_action`. Confidence is
  capped by the claim support rate and by an exhausted budget.
- **Hard limits** (`config.py`): 8 tool calls, `max_output_tokens` per agent, a token ceiling per
  agent, 60k tokens in total, 180 s wall clock. Hitting one marks the run `budget_exhausted`.
- **Engine and cache are never hidden**: graph tool events carry `engine` (`gql`/`fallback`) and
  `cached`; table tools report `engine: "table"` (stored detector output). `GraphService.warm()`
  runs at start-up and each graph call keeps the P3 15 s budget.
- **Audit**: every step, tool call, LLM call and verdict is a row in `audit.agent_events`.
  Cases are cached in `risk_curated.case_reports` (append only, latest wins).
- **Policy retrieval** is keyword (BM25-style) over `data/policies/`; Vertex AI Search
  (`discoveryengine`) is not enabled in this project. The mode is stored on every case.
- `account_profile` returns sharing counts, not raw device or funding ids, because generated ids
  such as `FE_<account>` would tell the model which attackers evaded.

## Tests and evals

`pytest tests/unit/test_agents_pipeline.py` runs the whole pipeline with a scripted backend (no
Gemini). Evals: `python -m evals.agents.run` (dry run) / `--apply`. Precompute:
`python scripts/precompute_cases.py` (dry run) / `--apply`. Both print a cost estimate first and
have a hard cap on investigations.

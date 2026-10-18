# Pitch outline

Slide by slide. The source file for every number is in the last column. All data is synthetic; say so on slide 2.
No score or ranking is claimed anywhere.

| # | Slide | Content | Numbers and their source |
|---|---|---|---|
| 1 | Title | MarketSentinel: catch market manipulation, explain it, let a human decide. Team Sentinel Nexus. Live URL, repo URL | Live URL: `README.md` |
| 2 | The problem | Fake volume, buying together before resolution, mule funding; simple rules miss disguised rings and flag innocent groups. "Synthetic data, known answers" | 39 attackers (8 wash, 6 coordination, 25 mule), 23 decoy accounts, 2,039 accounts: `docs/benchmark.md` (Data) |
| 3 | Who it is for | Prediction-market operators and compliance analysts | none |
| 4 | The four steps | Trades arrive; map connections; AI investigates with evidence; analyst decides | none |
| 5 | Simple rule vs detector | Seed 42, per scenario | All scenarios: rule 71% / 51% / 60%, detector 100% / 97% / 99%; wash 38% / 62% vs 100% / 100%; coordinated 0% / 0% vs 100% / 83%; mule 65% / 60% vs 100% / 100%: `docs/benchmark.md` (Seed 42), `services/app/static/data/benchmark.json` |
| 6 | Innocent groups | Household 0 / 0, exchange cluster 8 / 0, busy honest 0 / 0 (rule / detector) | `docs/benchmark.md` (Innocent groups) |
| 7 | Is it fragile? | Ablation and robustness; caveats | 8 seeds: detector recall 95% to 100%, F1 98% to 100%; rule F1 32% to 74%: `docs/benchmark.md` (Robustness seeds, Ablation). Caveat, strongest innocent outscored weakest attacker on 2 seeds: `docs/benchmark.md` (Caveats) |
| 8 | The case page | Graph, evidence chips, removed claims. Screenshot `docs/img/live_case_graph_1920x1080.png` | Ring of 8, score 67.94, threshold 43, 2 claims removed: the stored case AL_584807441a on the live app (`risk_curated.case_reports`, served by `services/app`); threshold: `services/detector/threshold.json` |
| 9 | Agents and the Critic | ADK agents on Gemini 2.5 Flash; ledger, number check, LLM Critic, escalation guard | Adversarial test, 3 of 3 injected claims rejected: `docs/agent-evals.md`; caps of 8 tool calls and 120 s: `services/app/config.py` |
| 10 | Agent honesty | Decoy decisions unstable | First run 2026-10-16: 0 of 3 groups; re-run 2026-10-17: 2 of 3 groups (18 of 23 accounts); borderline attacker 0 of 1 then 1 of 1: `docs/agent-evals.md` (Results by run), `docs/injected-rerun.md` |
| 11 | Human in charge | Approval with a written reason, audit log, agents never act | `services/app/webhook.py`, Governance screen |
| 12 | Streaming | Near-real-time micro-batch: Pub/Sub, dead-letter queue, frozen detector; an idempotency demo | 310 published, 0 ingested, 310 duplicates dropped; delay 27.2 s, 34.3 s, 30.9 s; ring 44.37 vs 43.0: `docs/results/stream_eval.json`, `docs/STATUS.md` (P6) |
| 13 | Architecture and Google Cloud | BigQuery, property graph with GQL, Gemini and ADK, Pub/Sub, Cloud Run | `docs/architecture.md`, `README.md` (How Google Cloud and Gemini are used) |
| 14 | Limits and next steps | Synthetic only; no prompt-injection guard or PII redaction; keyword retrieval; per-instance state. Next: fresh-burst streaming (unverified branch), deploy pipeline | `docs/repo-map.md` (Planned, not built), `docs/ci-deploy.md` |

# ROADMAP

One phase = one branch. Tick a box only when its acceptance criteria are met and
`docs/STATUS.md` is updated.

Schedule: P1 and P2 Oct 14, P3 and P4 Oct 15, P5 and P6 Oct 16, P7 Oct 17. Submit Oct 18.

- [x] **P1 gcp-sync** (Oct 14)
  - Repo reproduces the live GCP state.
  - `scripts/verify_sync.py` compares live row counts, schemas, graph, Pub/Sub, Cloud Run
    and reservation against the repo, and exits non-zero on drift.
  - STATUS.md "Live GCP resources" is filled.

- [x] **P2 detector** (Oct 14)
  - Baseline flat-rule metrics recorded.
  - Graph-plus-behavior detector (device, funding and matched-trade links, account age,
    size, timing near resolution) writes `risk_curated.account_scores` and
    `eval.detector_results`.
  - Precision and recall per scenario in `docs/benchmark.md`.
  - Must clearly beat baseline. If it does not, report that honestly and iterate on
    scenario-agnostic features only.

- [x] **P2b detector hardening** (Oct 16, follow-up to P2)
  - Structure-based `scenario_hint` with accuracy per ring in `docs/benchmark.md`.
  - `needs_review` band from the validation margin and `status` column on `risk_curated.alerts`.
  - Ablation table, 8 robustness seeds with ranges and worst case, `docs/img/threshold_sweep.png`.
  - Threshold 43.0 unchanged; Caveats updated.

- [x] **P3 graph-tools** (Oct 15)
  - `services/graph` module: GQL first on `market_graph`; automatic fallback to SQL plus
    NetworkX if the reservation is off or errors.
  - Unit tests cover the fallback.

- [ ] **P4 agents** (Oct 15)
  - ADK agents on Gemini (Investigator, Critic, Drafter, Policy check) as one FastAPI
    service on Cloud Run under `sa-agent`.
  - Per-run token and tool-call caps.
  - Critic rejects any claim without evidence ids.
  - Every agent step written to the audit dataset.
  - Evals against ground-truth scenarios.

- [ ] **P5 web** (Oct 16)
  - Analyst workbench served by the same service: alert queue, ring graph view, agent
    timeline, case report with evidence links, approve/reject written to audit.
  - Publicly reachable.

- [ ] **P6 streaming-governance** (Oct 16)
  - Replay script publishing synthetic trades to Pub/Sub.
  - Idempotent consumer into BigQuery with the DLQ.
  - Model Armor or equivalent prompt guard, PII redaction.
  - Budget alert documented.

- [ ] **P7 release** (Oct 17)
  - GitHub Actions CI and tag-based deploy via Workload Identity Federation.
  - README with architecture diagram, `docs/setup.md`, `docs/demo-script.md`, benchmark chart.
  - gitleaks over the full history.
  - Tag `v1.0.0-submission`.

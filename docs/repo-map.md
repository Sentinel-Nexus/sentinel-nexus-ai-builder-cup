# Repository map

Every line below was checked against the tree on 2026-10-18 (branch `chore/release`).
Status: **implemented** (code exists and is tested or deployed), **partial**, **not built**.
All data is synthetic.

## Top level

| Path | What it contains | Status |
|---|---|---|
| `services/` | Python packages: `app`, `detector`, `graph`, `stream`, `ingest`, plus the `web` health-check starter | implemented |
| `sql/` | BigQuery DDL (`schema`), the property graph (`graph`), the detector SQL (`views`), one-off migrations (`migrations`) and table-level IAM grants (`iam`) | implemented |
| `infra/` | `deploy_app.py` (Cloud Run build and deploy), `stream_setup.py` (Pub/Sub grants and stream tables), `gcp/` (project setup script, inventory) | implemented |
| `scripts/` | Benchmark, sync check, smoke tests, screenshot and data builders, `check_repo.py` | implemented |
| `evals/` | `agents/` (agent evaluation), `scenarios/` (scenario definitions), `stream/` (stream burst ground truth), `robustness_profiles.json` | implemented |
| `data/` | `synthetic/sample` (small committed sample), `policies/` (synthetic surveillance policy), `stream/` (the replay burst), `injected_alerts.json` | implemented |
| `docs/` | Status, roadmap, benchmark, agent evaluation, architecture, setup, demo script, ADRs, stored results (`results/`), images (`img/`) | implemented |
| `tests/` | `unit/` (256 tests passing, 5 skipped) and `integration/` (live BigQuery checks, opt-in with `GRAPH_LIVE_TESTS=1`) | implemented |
| `.github/workflows/` | `ci.yml`: ruff, pytest, `scripts/check_repo.py` | implemented |

## Service folders

| Path | What it contains | Status |
|---|---|---|
| `services/app` | The one deployed service `sentinel-app`: FastAPI API, the ADK agents, the Critic and evidence ledger, audit, rate limits, and the static analyst workbench (`static/`) | implemented |
| `services/detector` | The frozen detector: scoring weights, threshold (`threshold.json`, 43.0), ablation, evaluation, report | implemented |
| `services/graph` | Graph tools: GQL on the BigQuery property graph, automatic SQL plus NetworkX fallback, read-only guard, cache | implemented |
| `services/stream` | The Pub/Sub path: schema validation, idempotent consumer, micro-batch scoring with the frozen detector, rate limits | implemented (one burst evaluated) |
| `services/ingest` | `synth/`: the synthetic data generator (seed 42 reproduces the live data) and its BigQuery loader. It does not read any real market feed | implemented for synthetic data only |
| `services/web` | A Flask health check, the `sentinel-hello` Cloud Run service. It is **not** the workbench | partial (starter, kept because `infra/gcp/setup.sh`, `scripts/verify_sync.py` and `tests/unit/test_web.py` use it) |

## Where the components of the original plan live

The first plan listed `services/agents`, `services/risk` and `services/web`. Those folders were
removed (the first two held only a `.gitkeep`). The work lives here:

| Planned name | Actual location |
|---|---|
| agents | `services/app/agents.py` (ADK Investigator, Critic, Drafter), `pipeline.py` (run order), `critic.py`, `ledger.py`, `tools.py`, `budget.py` (caps), `policy.py` (keyword retrieval), `audit.py`, `decisions.py`, `webhook.py` (mocked) |
| risk | `services/detector/` (scoring, threshold) and `sql/views/` (detector SQL), writing `risk_curated.account_scores` and `risk_curated.alerts` |
| graph tools | `services/graph/` and `sql/graph/market_graph.sql` |
| web | `services/app/workbench.py` (read endpoints) and `services/app/static/` (the single-page workbench) |
| ingest | `services/ingest/synth/` (synthetic generator) and `services/stream/` (Pub/Sub path) |

## Planned, not built

- Model Armor or any prompt-injection guard (the Governance screen lists it as not deployed).
- PII redaction (the data is synthetic and holds no personal data).
- Vertex AI Search retrieval (retrieval is keyword-based over `data/policies/`).
- Ingestion of real Polymarket data (only synthetic data exists).
- Fresh-burst streaming: branch `feat/stream-fresh`, unverified, not deployed, not merged.
- The Workload Identity Federation deploy pipeline (documented in `docs/ci-deploy.md`, not applied).
- Shared state across several Cloud Run instances (rate limits, caches and stream counters are per
  instance, so the service is pinned to one instance).

## Leftovers kept on purpose

- `sql/views/.gitkeep`: the deploy stages `sql/views` verbatim into the build context (85 files);
  removing it would change that context, so it stays. The folder holds real SQL files.
- Scripts `scripts/load_check.py`, `scripts/overview_popover_shots.py`, `scripts/ui_screens.py` are not
  referenced by tests or docs; they are working development tools (screenshots, a load check) and
  are kept.
- `_import/` and `.cache/` exist only on the author's machine and are git-ignored; they are not in the repository.

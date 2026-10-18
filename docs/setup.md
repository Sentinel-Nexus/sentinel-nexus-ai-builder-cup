# Setup from a clean clone

Verified on Windows 11, Python 3.12, on 2026-10-18 from a fresh `git clone` of branch `chore/release`.
What was and was not run is marked on each step. Nothing here changes cloud resources.

## 1. Install (verified)

```bash
git clone https://github.com/Sentinel-Nexus/sentinel-nexus-ai-builder-cup.git
cd sentinel-nexus-ai-builder-cup
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on macOS and Linux
pip install -e ".[dev,app]"       # about 90 seconds; installs dependencies only, no package
ruff check .
pytest -q
```

Result in a fresh clone and venv: `pip check` reported no broken requirements, `ruff` was clean and
`pytest` gave 245 passed, 16 skipped (5 skips are live-BigQuery tests; the rest need Playwright, which is not in
the extras; in the author's venv, which has Playwright, 256 passed and 5 skipped).
Keep the clone path short on Windows: a deep path (for example inside a long temp folder) made `pip` fail
with `WinError 206`.

`pip install -e .` used to fail on a clean clone (setuptools refused to pick a package among `services`,
`sql`, `scripts`, ...); `pyproject.toml` now declares `py-modules = []`.

## 2. Google credentials (needed for anything that reads BigQuery)

```bash
gcloud auth login
gcloud config set project project-f8544066-3df8-4f99-8ed
gcloud auth application-default login
gcloud auth application-default set-quota-project project-f8544066-3df8-4f99-8ed
gcloud auth application-default print-access-token > NUL     # must succeed
```

Your account needs read access to the project's BigQuery data (`roles/bigquery.dataViewer`) and permission to run
jobs (`roles/bigquery.jobUser`); live investigations also need `roles/aiplatform.user`. The project is the
author's, so a stranger without access can run the tests (step 1) and regenerate the data locally (step 4) but not step 3.
Never commit `.env` or key files; variable names are in `.env.example`.

## 3. Run the app locally (partly verified)

```bash
uvicorn services.app.main:app --port 8080
# wait for http://localhost:8080/health to return 200 {"ready": true}, then open http://localhost:8080/
```

What was run on 2026-10-18: the app started with the author's cached tables, `/health` turned 200 after 21.9 s,
and `/`, `/summary`, `/queue`, `/benchmark`, `/governance` and `/stream/config` answered 200 (6 ms to 1.4 s).
That run **blocked every BigQuery query job** (the release rules allow no query jobs), so the pages that need a
query, `/cases/{id}/graph` and `/audit`, returned 502 by design of that test harness and were **not**
exercised locally.

### Why local BigQuery reads hung, and what is required

The earlier report was that a local server's BigQuery reads hung. This was investigated without changing any code
(the deployed code is frozen for this release):

| Check | Result on 2026-10-18 |
|---|---|
| Load ADC, quota project | works, 2.7 s; quota project is the project itself |
| Refresh the access token | works, 0.6 s |
| `get_table` on `risk_curated.alerts` | works, 2.2 s |
| `list_rows(max_results=5)` | works, 0.7 s |
| Local server, cache present | ready after 21.9 s |

So credentials, quota project and the network were fine, and a hang was **not reproduced**. What the code does
(read from the source, not from a failed run):

- At start-up the service reads seven tables with `list_rows` into `.cache/agents/*.json` and reuses them for one
  hour. A start with a stale or empty cache downloads all seven tables (the cached files are about 3.7 MB; the
  cold download was not timed here, to stay within the no-bulk-read rule).
- Those reads have no per-call timeout and run under one lock, so while one read stalls, every request that needs
  table data waits behind it, and `/health` stays 503 until the warm-up finishes.
- Graph, audit and agent-case endpoints use query jobs, which run on the `gql-poc` reservation that is assigned
  to the whole project.

Most likely causes of a stall, in this order, **none confirmed**: a stale ADC session (run `print-access-token`
above; if it prompts or fails, run `gcloud auth application-default login` again), a slow or filtered network
during the cold download, or a missing `bigquery.jobUser` for the query endpoints. If you reproduce a hang, the first
thing to capture is the `uvicorn` console output (it prints `warm: ready after N s` or `warm: failed: ...`).
A per-call timeout on the table reads would be the code fix; it was not made because `services/app` is not to change in this release.

## 4. Regenerate the synthetic data and check sync (verified)

```bash
python -m services.ingest.synth --seed 42        # dry run: writes data/synthetic/full, BigQuery untouched
python scripts/verify_sync.py --no-query         # read-only; needs step 2
```

On 2026-10-18 the first printed `seed=42 accounts=2039 markets=20 trades=35116 ground_truth=39` and the second
`53 checks, 0 drift, 0 warnings`. `--apply` on the generator would truncate and reload BigQuery tables; it was not run.

## 5. Deploy (dry run verified)

```bash
python infra/deploy_app.py            # prints the plan; stages 85 files; builds nothing
python infra/deploy_app.py --apply    # builds and deploys: owner only, not run for this release
```

## Warning: ADK and OpenTelemetry versions

`google-adk==2.12.0` pins `opentelemetry-api` 1.42.1, while newer `google-api-core` releases ask for 1.44 or
later. In the author's long-lived venv `pip check` printed:
`google-api-core 2.42.0 has requirement opentelemetry-api<2.0.0,>=1.44.0, but you have opentelemetry-api 1.42.1`.
It is a pip metadata complaint; the tests and the app ran with it. In a fresh venv `pip` chose
`google-api-core 2.34.0` and `pip check` was clean, so the warning goes away by recreating the venv. The Cloud Run image
installs `services/app/requirements.txt` and resolves cleanly (`docs/STATUS.md`).

# MarketSentinel

Project for the Google Cloud AI Builder Cup 2026 (BFSI track). MarketSentinel is a
surveillance and investigation system for prediction markets. It detects wash trading,
coordinated pre-resolution buying and mule-funded accounts.

## Pipeline

Polymarket public data -> Pub/Sub -> BigQuery -> BigQuery property graph -> risk scoring
-> Gemini agents (ADK, Cloud Run) -> human analyst approval.

Evals use synthetic manipulation scenarios with labeled ground truth
(`data/synthetic`, `evals/scenarios`).

## Environment

- GCP project: `project-f8544066-3df8-4f99-8ed`
- Region: `us-central1`
- Artifact Registry repo: `sentinel`
- Agent service account: `sa-agent`
- Python 3.12
- Windows host: no `sed` or bash-only tooling in repo scripts. Use Python.

## Layout

- `services/{ingest,risk,agents,web}` - deployable services
- `sql/{schema,graph,views}` - BigQuery DDL, property graph, views
- `evals/scenarios`, `data/synthetic` - labeled eval scenarios and synthetic data
- `infra`, `scripts`, `docs/adr`, `tests/{unit,integration}`
- `docs/STATUS.md` - live state; `docs/ROADMAP.md` - phases and acceptance criteria

## Standing rules

### Repo and git
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, ...).
- Work on feature branches and open PRs. Never commit or push to `main` after the first scaffold.
- Run `ruff check . && pytest` before every commit; both must be green.
- Never commit secrets. Never read `.env` or key files; use `.env.example` for variable names.
- Only work built during the hackathon belongs in this repo.

### Reproducibility
- `docs/STATUS.md` is the live state of GCP and the repo. The repo must be able to
  reproduce what exists in GCP: every cloud change is a script under `infra/`, `sql/` or
  `scripts/` plus a STATUS.md update in the same commit. No console-only changes.
- SQL files use the `__P__` placeholder for the project ID, rendered by
  `scripts/run_sql.py` (no `sed`, this runs on Windows). Never hardcode credentials.
- Any script that changes cloud resources defaults to dry-run and needs `--apply`.

### Cost
- Reservation `gql-poc` is assigned to the whole project, so ALL queries use slots.
  No query loops, cache results, use partition filters and `LIMIT` while testing.
- Teardown after Nov 6 is documented in `docs/STATUS.md`.

### Data
- All data is synthetic. Nothing from any employer or client goes in this repo.

### Verify, do not recall
- Never rely on memory for current syntax or model names. Check the current docs
  (BigQuery GQL, ADK, Model Armor) and make a real test call to confirm the Gemini model
  ID available in `us-central1`.

### Long-running commands
- No command may run longer than 10 minutes without a timeout (`timeout N`, the tool's
  `timeout`, or a script wall-clock cap) and progress output.
- No background job may call Gemini without a hard cap on calls and a cost estimate shown first.
  Scripts that spend tokens default to dry-run, print the estimate, and need `--apply`.

### Agents
- Cap tokens and tool calls per agent run (explicit max output tokens and max tool-call count).
- Analysts approve every consequential action; agents never act on accounts autonomously.

### Workflow
- One roadmap phase is one branch. Small Conventional Commits.
- `ruff` and `pytest` green before each commit.
- Update `docs/STATUS.md` and tick the box in `docs/ROADMAP.md`.
- Push the branch (never `main`).
- Finish with a 5-line summary and a list of what the human must verify.

### Blocked?
- If blocked by a permission or org policy: stop, report the exact error and the one
  command the human should run in Cloud Shell as project owner.

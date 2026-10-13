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

## Layout

- `services/{ingest,risk,agents,web}` - deployable services
- `sql/{schema,graph,views}` - BigQuery DDL, property graph, views
- `evals/scenarios`, `data/synthetic` - labeled eval scenarios and synthetic data
- `infra`, `scripts`, `docs/adr`, `tests/{unit,integration}`

## Rules

- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, ...).
- Work on feature branches and open PRs. Never commit to `main` after the first scaffold.
- Run `ruff check . && pytest` before committing.
- Never commit secrets. Never read `.env` or key files; use `.env.example` for variable names.
- Cap tokens and tool calls per agent run (explicit max output tokens and max tool-call count).
- Only work built during the hackathon belongs in this repo.
- Analysts approve every consequential action; agents never act on accounts autonomously.

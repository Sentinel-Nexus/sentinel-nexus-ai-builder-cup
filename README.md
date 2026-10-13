# MarketSentinel

Surveillance and investigation for prediction markets, built for the Google Cloud AI Builder
Cup 2026 (BFSI track). Detects wash trading, coordinated pre-resolution buying and
mule-funded accounts.

## Pipeline

Polymarket public data -> Pub/Sub -> BigQuery -> BigQuery property graph -> risk scoring
-> Gemini agents (ADK, Cloud Run) -> human analyst approval.

Detection is evaluated against synthetic manipulation scenarios with labeled ground truth.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in locally
pre-commit install
ruff check . && pytest
```

## Layout

| Path | Purpose |
|------|---------|
| `services/` | ingest, risk, agents, web |
| `sql/` | BigQuery schema, property graph, views |
| `evals/`, `data/synthetic/` | labeled scenarios and synthetic data |
| `infra/`, `scripts/` | deployment and tooling |
| `docs/adr/` | architecture decision records |

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CLAUDE.md](CLAUDE.md).

# ADR 0001: One synthetic data generator, the one that built the live data

Status: accepted, 2026-10-14

## Context

Two generators existed:

- The repo generator (`services/ingest/synth`, seed 42): 460 baseline accounts, taker/maker
  trades, a `transfers` table, `ip_block`/`funding_source` fields, JSONL output.
- The Cloud Shell generator (`generate_data.py`): 2,000 normal accounts plus three scenarios,
  loaded straight to BigQuery. This produced all data in the live project.

The repo must reproduce live state (CLAUDE.md), so two generators with different schemas is
a drift risk.

## Decision

The Cloud Shell generator is canonical, ported to `services/ingest/synth` with:

- `random.Random(seed)` instead of the global RNG. The sequence of random calls is unchanged.
- A fixed `--now` anchor (default `2026-10-12T16:00:00Z`) instead of the wall clock, so
  timestamps are reproducible too. The Cloud Shell script used `now()`, so live timestamps
  differ from a rerun by a constant shift; counts, ids and relative timing do not.
- A BigQuery loader (`load.py`) that does nothing without `--apply`.
- Per-scenario ground-truth JSON in `evals/scenarios/` (same file names) built from the
  truth rows, plus the scenario trade ids.

The 500-account generator and its modules were removed in the same change.

## Evidence

- Offline run of the original script for seeds 7, 11, 23, 42 (BigQuery stubbed):
  all give 2,039 accounts and 39 truth accounts (8 wash, 6 coordination, 25 mule);
  trades are 34,306 (7), 34,710 (11), 34,532 (23), 35,073 (42).
- Live `risk_raw.trades` has 35,073 rows, so live was loaded with **seed 42**: the last
  iteration of the Cloud Shell multi-seed loop. The 34,306 figure in the P1 brief is seed 7.
- With one fixed `now`, the ported generator's accounts, markets, trades and truth rows equal
  the original's output exactly (checked row for row).
- `tests/unit/test_synth_scenarios.py` pins the live counts.

## Consequences

- Lost with the old generator: the `transfers` table and the `ip_block`, `signup_date`
  and `funding_source` fields. Nothing live uses them. Funding links are `funding_source_id`
  on accounts.
- `harden.sql` (hard negatives, partial links) is not part of the generator; it is applied after
  load (`sql/schema/harden.sql`) and is required to match live.
- Order to reproduce live data: generate/load seed 42, `harden.sql`, `market_graph.sql`.
- Later phases that need other seeds (benchmarks over seeds 7, 11, 23, 42) use `--seed`.

"""CLI: python -m services.ingest.synth --seed 42 [--apply]

Writes JSONL and per-scenario ground truth locally. With --apply it also loads BigQuery
(WRITE_TRUNCATE on trades, accounts, markets, ground_truth); without it nothing is sent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

from services.ingest.synth.generator import (
    DEFAULT_NOW,
    DEFAULT_SEED,
    generate,
    sample,
    write_dataset,
    write_ground_truth,
)
from services.ingest.synth.load import load


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m services.ingest.synth", description=__doc__)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="42 reproduces the live data")
    p.add_argument(
        "--now",
        type=dt.datetime.fromisoformat,
        default=DEFAULT_NOW,
        help="ISO timestamp anchoring all times (timezone-aware)",
    )
    p.add_argument(
        "--out", type=Path, default=Path("data/synthetic/full"), help="full output (gitignored)"
    )
    p.add_argument(
        "--sample-out",
        type=Path,
        default=Path("data/synthetic/sample"),
        help="small committed sample",
    )
    p.add_argument("--no-sample", action="store_true")
    p.add_argument("--scenarios-dir", type=Path, default=Path("evals/scenarios"))
    p.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    p.add_argument("--apply", action="store_true", help="load into BigQuery (default: no)")
    args = p.parse_args(argv)

    data = generate(args.seed, args.now)
    write_dataset(data, args.out)
    write_ground_truth(data, args.scenarios_dir)
    if not args.no_sample:
        write_dataset(sample(data), args.sample_out)
    print(
        f"seed={data.seed} accounts={len(data.accounts)} markets={len(data.markets)} "
        f"trades={len(data.trades)} ground_truth={len(data.ground_truth)} -> {args.out}"
    )
    if args.apply:
        if not args.project:
            raise SystemExit("error: set PROJECT_ID or pass --project with --apply")
        print("loaded:", load(data, args.project, apply=True))
    else:
        print("dry run: BigQuery not touched (pass --apply to load)")


if __name__ == "__main__":
    main()

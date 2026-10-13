"""CLI: python -m services.ingest.synth --seed 42"""

from __future__ import annotations

import argparse
from pathlib import Path

from services.ingest.synth.generator import generate, sample, write_dataset, write_ground_truth


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m services.ingest.synth", description=__doc__)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--accounts", type=int, default=460, help="baseline accounts (scenarios add more)"
    )
    p.add_argument("--markets", type=int, default=20)
    p.add_argument("--trades", type=int, default=20_000, help="baseline trades")
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
    args = p.parse_args(argv)

    data = generate(args.seed, args.accounts, args.markets, args.trades)
    write_dataset(data, args.out)
    write_ground_truth(data, args.scenarios_dir)
    if not args.no_sample:
        write_dataset(sample(data), args.sample_out)
    print(
        f"seed={data.seed} accounts={len(data.accounts)} markets={len(data.markets)} "
        f"trades={len(data.trades)} transfers={len(data.transfers)} -> {args.out}"
    )


if __name__ == "__main__":
    main()

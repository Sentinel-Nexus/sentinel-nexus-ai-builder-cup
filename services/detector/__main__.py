"""CLI for the P2 detector. Everything is dry-run unless --apply is given.

python -m services.detector val-load [--apply]            # validation seeds -> eval_val
python -m services.detector robust-load [--apply]         # robustness seeds -> eval_val
python -m services.detector pipeline --target live|7|101 [--apply]
python -m services.detector select --iteration 1 [--apply]  # threshold from validation
python -m services.detector score-live --iteration 1 [--apply]
python -m services.detector report                          # writes docs/results/hardening.json
python -m services.detector margin [--apply]               # review band from validation
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path

from services.detector import evaluate, pipeline, report, scoring
from services.ingest.synth.generator import generate
from services.ingest.synth.load import load, validation_destinations
from services.ingest.synth.profiles import load_profiles

THRESHOLD_FILE = Path(__file__).with_name("threshold.json")
RESULTS_FILE = Path(__file__).resolve().parents[2] / "docs" / "results" / "hardening.json"
VALIDATION_SEEDS = (7, 11, 23)
GRID = [x / 2 for x in range(0, 201)]  # thresholds 0..100 in 0.5 steps
METHOD_BASELINE, METHOD_DETECTOR = "baseline_flat_rule", "detector_v1"


def _client(project: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project, location="US")


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")


CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "detector"


def load_inputs(client, project: str, target: pipeline.Target) -> dict:
    """Features, baseline flags and labels of one target (free table reads, cached locally)."""

    def cache(name: str) -> Path:
        return CACHE_DIR / f"{target.name}_{name}.json"

    features = pipeline.fetch_rows(client, target.table("det_features"), cache("features"))
    baseline = {
        r["account_id"]
        for r in pipeline.fetch_rows(client, target.table("baseline_flags"), cache("baseline"))
    }
    accounts = pipeline.fetch_rows(client, target.accounts, cache("accounts"))
    truth, decoys = evaluate.fetch_labels(client, project, target)
    return {
        "features": features,
        "baseline": baseline,
        "accounts": accounts,
        "truth": truth,
        "decoys": decoys,
    }


def overall(rows: list[dict]) -> dict:
    return next(r for r in rows if r["group_kind"] == "overall")


def choose_threshold(inputs_by_seed: dict[int, dict]) -> tuple[float, dict]:
    """Plateau-midpoint threshold of the full detector on the validation seeds."""
    scores = {
        seed: {s["account_id"]: s["score"] for s in scoring.score_rows(inp["features"], 0.0)}
        for seed, inp in inputs_by_seed.items()
    }
    labels = {seed: (inp["truth"], inp["decoys"]) for seed, inp in inputs_by_seed.items()}
    return evaluate.select_threshold(scores, labels, GRID)


def result_rows(metric_rows, *, run_id, target, method, threshold, iteration) -> list[dict]:
    ts = _now()
    return [
        {
            "run_id": run_id,
            "split": target.split,
            "seed": target.seed,
            "method": method,
            **m,
            "threshold": threshold,
            "iteration": iteration,
            "run_ts": ts,
        }
        for m in metric_rows
    ]


def write_results(client, project, rows, iteration, apply) -> None:
    if not apply:
        return
    from google.cloud import bigquery

    table = f"{project}.eval.detector_results"
    for key in {(r["split"], r["seed"], r["method"]) for r in rows}:
        client.query(
            f"DELETE FROM `{table}` WHERE iteration = @i AND split = @s AND seed = @d "
            "AND method = @m",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("i", "INT64", iteration),
                    bigquery.ScalarQueryParameter("s", "STRING", key[0]),
                    bigquery.ScalarQueryParameter("d", "INT64", key[1]),
                    bigquery.ScalarQueryParameter("m", "STRING", key[2]),
                ]
            ),
        ).result()
    cfg = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND", schema=client.get_table(table).schema
    )
    client.load_table_from_json(rows, table, job_config=cfg).result()


def show(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    for m in rows:
        f = lambda x: "  -  " if x is None else f"{x:5.2f}"  # noqa: E731
        print(
            f"  {m['group_kind']:<10} {m['group_name']:<26} n={m['n_members']:<5} "
            f"flagged={m['flagged']:<4} tp={m['true_pos']:<3} fp={m['false_pos']:<3} "
            f"P={f(m['precision'])} R={f(m['recall'])} F1={f(m['f1'])}"
        )


def cmd_val_load(args, project) -> None:
    for seed in VALIDATION_SEEDS:
        data = generate(seed)
        counts = load(
            data,
            project,
            apply=args.apply,
            destinations=validation_destinations(seed),
        )
        print(f"seed {seed}: {'loaded' if args.apply else 'dry run'} {counts}")


def cmd_robust_load(args, project) -> None:
    """Generate the robustness seeds (varied parameters) into eval_val.s<seed>_*."""
    for seed, params in load_profiles().items():
        counts = load(
            generate(seed, params=params),
            project,
            apply=args.apply,
            destinations=validation_destinations(seed),
        )
        print(f"seed {seed}: {'loaded' if args.apply else 'dry run'} {counts}")


def cmd_pipeline(args, project) -> None:
    target = pipeline.parse_target(project, args.target)
    for entry in pipeline.run_pipeline(project, target, args.apply):
        print(entry)


def cmd_select(args, project) -> None:
    client = _client(project)
    inputs = {
        s: load_inputs(client, project, pipeline.validation_target(project, s))
        for s in VALIDATION_SEEDS
    }
    threshold, info = choose_threshold(inputs)
    run_id = f"val-{args.iteration}-{uuid.uuid4().hex[:8]}"
    print(f"selected threshold {threshold} on validation seeds {VALIDATION_SEEDS}: {info}")
    all_rows = []
    for seed, inp in inputs.items():
        target = pipeline.validation_target(project, seed)
        scored = scoring.score_rows(inp["features"], threshold)
        flagged = {s["account_id"] for s in scored if s["flagged"]}
        accounts = {s["account_id"] for s in scored}
        det = evaluate.metrics(flagged, inp["truth"], inp["decoys"], accounts)
        base = evaluate.metrics(inp["baseline"], inp["truth"], inp["decoys"], accounts)
        show(f"seed {seed} baseline", base)
        show(f"seed {seed} detector (threshold {threshold})", det)
        for method, rows in ((METHOD_BASELINE, base), (METHOD_DETECTOR, det)):
            all_rows += result_rows(
                rows,
                run_id=run_id,
                target=target,
                method=method,
                threshold=threshold if method == METHOD_DETECTOR else None,
                iteration=args.iteration,
            )
    THRESHOLD_FILE.write_text(
        json.dumps(
            {
                "threshold": threshold,
                "iteration": args.iteration,
                "validation_seeds": list(VALIDATION_SEEDS),
                **info,
                "weights": {f.name: f.weight for f in scoring.FEATURES},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    write_results(client, project, all_rows, args.iteration, args.apply)


def cmd_margin(args, project) -> None:
    """Derive the review margin from validation seeds only and store it next to the threshold.

    margin = mean over validation seeds of (weakest attacker score - strongest non-attacker
    score). The threshold itself is not re-selected.
    """
    frozen = json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))
    client = _client(project)
    gaps = {}
    for seed in VALIDATION_SEEDS:
        inp = load_inputs(client, project, pipeline.validation_target(project, seed))
        scores = {s["account_id"]: s["score"] for s in scoring.score_rows(inp["features"], 0.0)}
        gaps[seed] = evaluate.score_gap(scores, inp["truth"])
    margin = round(max(0.0, sum(gaps.values()) / len(gaps)), 2)
    print(f"gaps by validation seed: {gaps} -> review margin {margin}")
    if args.apply:
        frozen["review_margin"] = margin
        frozen["review_gap_by_seed"] = {str(k): round(v, 4) for k, v in gaps.items()}
        THRESHOLD_FILE.write_text(
            json.dumps(frozen, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print("wrote review_margin to threshold.json")
    else:
        print("dry run: threshold.json unchanged (pass --apply)")


def cmd_report(args, project) -> None:
    """Robustness, ablation, hint accuracy, review band and sweep -> docs/results/hardening.json.

    Reads cached detector inputs (free table reads); the frozen threshold is only read.
    """
    frozen = json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))
    profiles = load_profiles()
    client = _client(project)
    targets = {
        **{s: pipeline.validation_target(project, s) for s in VALIDATION_SEEDS},
        42: pipeline.live_target(project),
        **{s: pipeline.validation_target(project, s) for s in profiles},
    }
    inputs = {s: load_inputs(client, project, t) for s, t in targets.items()}
    data = report.build(
        inputs,
        threshold=frozen["threshold"],
        margin=frozen["review_margin"],
        validation_gaps=frozen["review_gap_by_seed"],
        validation=list(VALIDATION_SEEDS),
        evaluation=42,
        robustness=sorted(profiles),
        profiles={s: dataclasses.asdict(p) for s, p in profiles.items()},
    )
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(
        json.dumps(data, indent=1, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {RESULTS_FILE}")
    rob = data["robustness"]
    print("robustness precision", rob["precision"], "recall", rob["recall"], "f1", rob["f1"])


def cmd_score_live(args, project) -> None:
    frozen = json.loads(THRESHOLD_FILE.read_text(encoding="utf-8"))
    if frozen["iteration"] != args.iteration:
        sys.exit("error: threshold.json is from another iteration; run `select` first")
    threshold = frozen["threshold"]
    client = _client(project)
    target = pipeline.live_target(project)
    inp = load_inputs(client, project, target)
    run_id = f"live-{args.iteration}-{uuid.uuid4().hex[:8]}"
    created = _now()
    margin = frozen.get("review_margin", 0.0)
    scored = scoring.score_rows(inp["features"], threshold, margin)
    alerts = scoring.build_alerts(scored, run_id, created)
    flagged = {s["account_id"] for s in scored if s["flagged"]}
    accounts = {s["account_id"] for s in scored}
    det = evaluate.metrics(flagged, inp["truth"], inp["decoys"], accounts)
    base = evaluate.metrics(inp["baseline"], inp["truth"], inp["decoys"], accounts)
    show("seed 42 baseline (flat funding rule)", base)
    show(f"seed 42 detector (frozen threshold {threshold})", det)
    scores = {s["account_id"]: s["score"] for s in scored}
    band = evaluate.band_counts(scores, inp["truth"], inp["decoys"], threshold, margin)
    print(f"\n{len(flagged)} accounts flagged in {len(alerts)} alerts")
    print(f"review band [{threshold - margin:.2f}, {threshold}): {band}")
    print("hint accuracy:", evaluate.hint_accuracy(alerts, inp["truth"])["by_scenario"])
    if args.apply:
        from google.cloud import bigquery

        for table, rows in (
            ("risk_curated.account_scores", scoring.to_score_table_rows(scored, run_id, created)),
            ("risk_curated.alerts", alerts),
        ):
            cfg = bigquery.LoadJobConfig(
                write_disposition="WRITE_TRUNCATE",
                schema=client.get_table(f"{project}.{table}").schema,
            )
            client.load_table_from_json(rows, f"{project}.{table}", job_config=cfg).result()
        rows = []
        for method, metric_rows in ((METHOD_BASELINE, base), (METHOD_DETECTOR, det)):
            rows += result_rows(
                metric_rows,
                run_id=run_id,
                target=target,
                method=method,
                threshold=threshold if method == METHOD_DETECTOR else None,
                iteration=args.iteration,
            )
        write_results(client, project, rows, args.iteration, True)
        print("wrote account_scores, alerts, detector_results")
    else:
        print("dry run: nothing written (pass --apply)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m services.detector", description=__doc__)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("val-load", "pipeline", "select", "score-live", "margin", "robust-load", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--apply", action="store_true")
        if name == "pipeline":
            sp.add_argument("--target", required=True, help="live or a validation seed, e.g. 7")
        if name in ("select", "score-live"):
            sp.add_argument("--iteration", type=int, required=True)
    args = ap.parse_args(argv)
    if not args.project:
        sys.exit("error: set PROJECT_ID or pass --project")
    {
        "val-load": cmd_val_load,
        "pipeline": cmd_pipeline,
        "select": cmd_select,
        "score-live": cmd_score_live,
        "margin": cmd_margin,
        "robust-load": cmd_robust_load,
        "report": cmd_report,
    }[args.cmd](args, args.project)


if __name__ == "__main__":
    main()

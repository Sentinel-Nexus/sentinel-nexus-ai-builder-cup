# ruff: noqa: E501
"""Markdown sections and the threshold-sweep chart for the hardening pass.

Input is docs/results/hardening.json (written by ``python -m services.detector report``);
nothing here touches the cloud.
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Any

SCEN = ("wash_trading", "coordinated_accumulation", "mule_funding")
LIGHT = {"bg": "#fcfcfb", "ink": "#0b0b0b", "muted": "#52514e", "grid": "#f0efec"}
BLUE, ORANGE = "#2a78d6", "#eb6834"


def pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _rng(d: dict) -> str:
    if d["min"] is None:
        return "n/a"
    return f"{pct(d['min'])} / {pct(d['median'])} / {pct(d['max'])}"


def _spread(xs: list[float]) -> str:
    return _rng({"min": min(xs), "median": statistics.median(xs), "max": max(xs)})


def worst_seed(data: dict) -> str:
    """Robustness seed with the lowest detector F1 (ties: lowest recall)."""
    seeds = data["robustness_seeds"]
    return str(min(seeds, key=lambda s: (data["seeds"][str(s)]["detector"]["f1"] or 0, s)))


def sections(data: dict[str, Any]) -> list[str]:
    seeds = data["seeds"]
    ev = seeds[str(data["evaluation_seed"])]
    thr, margin = data["threshold"], data["review_margin"]
    lines: list[str] = []

    # ---- hint accuracy
    lines += [
        "## Scenario hint accuracy",
        "",
        "`scenario_hint` comes from ring structure (reciprocal matched fills, late one-sided",
        "buying near resolution, funding fan-in into fresh accounts), never from ids. It is",
        "scored against ground truth in the evaluation step only: an alert's true type is the",
        "most common scenario among its members. The rule was rewritten after the seed 42",
        "confusion was known (wash ring hinted as mule_funding), so seed 42 is not a clean",
        "held-out number for the hint; the other seeds were not looked at before the fix.",
        "",
        "Seed 42, per alert:",
        "",
        "| Alert true type | Members | Hint | Correct |",
        "|---|---:|---|---|",
    ]
    for a in sorted(ev["hint"]["alerts"], key=lambda a: (a["true_type"], -a["n_members"])):
        lines.append(
            f"| {a['true_type']} | {a['n_members']} | {a['hint']} | "
            f"{'yes' if a['correct'] else 'NO'} |"
        )
    lines += [
        "",
        "All other seeds, per attacker scenario (alerts hinted correctly / alerts; accounts in",
        "correctly hinted alerts / accounts in alerts of that type):",
        "",
        "| Seed | Split | " + " | ".join(SCEN) + " | decoy-only alerts |",
        "|---:|---|" + "---:|" * (len(SCEN) + 1),
    ]
    tot = {k: [0, 0] for k in SCEN}
    for s in [*data["validation_seeds"], *data["robustness_seeds"]]:
        by = seeds[str(s)]["hint"]["by_scenario"]
        split = "validation" if s in data["validation_seeds"] else "robustness"
        cells = []
        for k in SCEN:
            v = by.get(k)
            cells.append(f"{v['correct']}/{v['alerts']}" if v else "-")
            if v and split == "robustness":
                tot[k][0] += v["correct"]
                tot[k][1] += v["alerts"]
        none = by.get("none")
        lines.append(
            f"| {s} | {split} | " + " | ".join(cells) + f" | {none['alerts'] if none else 0} |"
        )
    lines += [
        "",
        "Robustness seeds combined: "
        + ", ".join(f"{k} {a}/{b}" for k, (a, b) in tot.items() if b)
        + ".",
        "",
    ]
    wrong = [
        (s, a)
        for s in data["robustness_seeds"]
        for a in seeds[str(s)]["hint"]["alerts"]
        if not a["correct"]
    ]
    if wrong:
        lines.append(
            "Wrong hints on robustness seeds. All are decoy-only needs_review alerts (true type"
            " 'none'), so they cannot be correct; the busy-but-honest decoys do show late one-sided"
            " buying, which is what the hint reports:"
        )
        lines.append("")
        for s, a in wrong:
            lines.append(
                f"- seed {s}: {a['n_members']}-member alert of type {a['true_type']} hinted "
                f"{a['hint']}"
            )
        lines.append("")

    # ---- review band
    lines += [
        "## Review band",
        "",
        f"Accounts scoring in [{thr - margin:.2f}, {thr}) are not dropped: their ring becomes an",
        "alert with `status = 'needs_review'` (or stays in a flagged ring as an unflagged member;",
        "`n_flagged` counts the members at or above the threshold). Metrics above still count",
        f"only accounts at or above {thr} as flagged. The margin ({margin}) is the mean over the",
        "validation seeds of the weakest attacker's score minus the strongest non-attacker's",
        "score, rule fixed before seed 42 was scored. Validation gaps: "
        + ", ".join(
            f"seed {s} {v}"
            for s, v in sorted(data["validation_gaps"].items(), key=lambda kv: int(kv[0]))
        )
        + ".",
        "",
        "| Seed | Split | Attackers in band | Decoys in band | Other accounts in band | Missed attackers in band / missed |",  # noqa: E501
        "|---:|---|---:|---:|---:|---:|",
    ]
    for s in [data["evaluation_seed"], *data["robustness_seeds"]]:
        r = seeds[str(s)]
        m = r["missed"]
        split = "held-out" if s == data["evaluation_seed"] else "robustness"
        lines.append(
            f"| {s} | {split} | {r['band']['attackers']} | {r['band']['decoys']} | "
            f"{r['band']['background']} | {sum(x['in_band'] for x in m)} / {len(m)} |"
        )
    lines += [
        "",
        f"Seed 42 alert queue: {ev['alerts']['flagged']} flagged, "
        f"{ev['alerts']['needs_review']} needs_review.",
        "",
    ]

    # ---- ablation
    lines += [
        "## Ablation",
        "",
        "Each variant gets its own cut-off from the validation seeds "
        f"{data['validation_seeds']} with the same plateau-midpoint procedure (mean F1, middle of",
        "the best plateau). Held-out columns use that cut-off unchanged. Graph features:",
        "link_size, link_density, fill_reciprocity, fresh_funding_fanin, fresh_cluster. Behavior",
        "features: account_freshness, late_concentration, size_zscore, timing_zscore. Subset",
        "weights are renormalised to span 0-100. For the two rule variants the cut-off is a group",
        "size (flat rule: funding group of k or more; components: component size). The published",
        "flat rule (k = 5) is in the tables above; here k is re-selected like every other cut-off.",
        "",
        "| Variant | Cut-off | Validation F1 | Seed 42 P / R / F1 | Robustness F1 min / median / max | Robustness recall min / median / max |",  # noqa: E501
        "|---|---:|---:|---|---|---|",
    ]
    for v in data["ablation"].values():
        s42 = v["seeds"][str(data["evaluation_seed"])]
        f1s = [v["seeds"][str(s)]["f1"] or 0.0 for s in data["robustness_seeds"]]
        rec = [v["seeds"][str(s)]["recall"] for s in data["robustness_seeds"]]
        lines.append(
            f"| {v['label']} | {v['cutoff']:g} | {pct(v['selection']['mean_f1'])} | "
            f"{pct(s42['precision'])} / {pct(s42['recall'])} / {pct(s42['f1'])} | "
            f"{_spread(f1s)} | {_spread(rec)} |"
        )
    lines.append("")

    # ---- robustness
    lines += [
        "## Robustness seeds (frozen threshold)",
        "",
        "Eight extra synthetic worlds with varied parameters (`evals/robustness_profiles.json`),",
        f"scored at the frozen threshold {thr}. Nothing was tuned on them.",
        "",
        "| Seed | Wash / coord / mule | Evasion | Decoys (hh / exch / busy) | Trades per account | Device noise | Attackers | Flagged | P | R | F1 | Flat rule P / R |",  # noqa: E501
        "|---:|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in data["robustness_seeds"]:
        p, r = data["profiles"][str(s)], seeds[str(s)]
        d = r["detector"]
        lines.append(
            f"| {s} | {p['wash_size']} / {p['coord_size']} / {p['mule_size']} | "
            f"{pct(p['evasion'])} | "
            f"{p['household_size']} / {p['exchange_size']} / {p['busy_size']} | "
            f"{p['bg_trades'][0]}-{p['bg_trades'][1]} | {pct(p['device_noise'])} | "
            f"{d['n_members']} | "
            f"{d['flagged']} | {pct(d['precision'])} | {pct(d['recall'])} | {pct(d['f1'])} | "
            f"{pct(r['baseline']['precision'])} / {pct(r['baseline']['recall'])} |"
        )
    rob = data["robustness"]
    lines += [
        "",
        "Range over the eight seeds (min / median / max):",
        "",
        "| Method | Precision | Recall | F1 |",
        "|---|---|---|---|",
        f"| Detector | {_rng(rob['precision'])} | {_rng(rob['recall'])} | {_rng(rob['f1'])} |",
        f"| Flat rule | {_rng(rob['baseline_precision'])} | {_rng(rob['baseline_recall'])} | "
        f"{_rng(rob['baseline_f1'])} |",
        "",
    ]
    w = worst_seed(data)
    wr = seeds[w]
    lines += [
        f"Worst case by F1: **seed {w}** (precision {pct(wr['detector']['precision'])}, recall "
        f"{pct(wr['detector']['recall'])}, F1 {pct(wr['detector']['f1'])}).",
        "",
    ]
    if wr["missed"]:
        lines.append(
            "Missed attackers there (scenario, score): "
            + "; ".join(f"{m['scenario']} {m['score']}" for m in wr["missed"][:12])
            + ("; ..." if len(wr["missed"]) > 12 else "")
            + ". Decoy and background accounts flagged: "
            + str(
                sum(g["detector"] for g in wr["decoys_flagged"].values()) + wr["background_flagged"]
            )
            + "."
        )
        lines.append("")
    lines += [
        "![threshold sweep](img/threshold_sweep.png)",
        "",
        "Precision, recall and F1 of the full detector over a threshold sweep (0-100). Thin lines:",
        "validation seeds, seed 42 and robustness seeds; bold: mean over all of them. Dashed",
        f"line: the frozen threshold {thr}. The sweep is descriptive; it did not choose anything.",
        "",
    ]
    return lines


def caveats(data: dict[str, Any]) -> list[str]:
    seeds, rob = data["seeds"], data["robustness"]
    ab = data["ablation"]
    w = worst_seed(data)
    missed = [m for s in data["robustness_seeds"] for m in seeds[str(s)]["missed"]]
    in_band = sum(m["in_band"] for m in missed)
    overlap = [
        s
        for s in data["robustness_seeds"]
        if seeds[str(s)]["weakest_attacker"] < seeds[str(s)]["strongest_nonattacker"]
    ]
    thr = data["threshold"]

    def f1_range(key: str) -> str:
        v = ab[key]["seeds"]
        return _spread([v[str(s)]["f1"] or 0.0 for s in data["robustness_seeds"]])

    return [
        "## Caveats",
        "",
        "This is a synthetic benchmark: the scenarios, decoys, generator and detector come from",
        "one project, and the labels say what the generator planted, not what a real market",
        "contains. Nothing below is evidence about real prediction markets.",
        "",
        "- **What the robustness seeds test.** Ring sizes, evasion rate (20-60%), decoy group sizes,",
        "  background trade volume and device-sharing noise were varied. The attack types were not:",
        "  the same three scenarios and the same three decoy kinds appear in every seed. The result",
        "  is stability to those parameters, not to a new manipulation tactic or a new kind of",
        "  innocent behaviour.",
        f"- **Robustness result.** At the frozen threshold {thr}, precision over the eight seeds was "
        f"{_rng(rob['precision'])} and recall {_rng(rob['recall'])} (min / median / max); worst case "
        f"seed {w}, F1 {pct(seeds[w]['detector']['f1'])}. Every one of the "
        f"{len(missed)} missed attackers scored below the threshold but inside the review band "
        f"({in_band} of {len(missed)}): "
        + ", ".join(f"{n} {k}" for k, n in sorted(Counter(m["scenario"] for m in missed).items()))
        + ".",
        "- **The margin is thin where it matters.** Across the robustness seeds the weakest "
        f"attacker scored {min(seeds[str(x)]['weakest_attacker'] for x in data['robustness_seeds'])}"
        f"-{max(seeds[str(x)]['weakest_attacker'] for x in data['robustness_seeds'])} and the "
        "strongest non-attacker "
        f"{min(seeds[str(x)]['strongest_nonattacker'] for x in data['robustness_seeds'])}"
        f"-{max(seeds[str(x)]['strongest_nonattacker'] for x in data['robustness_seeds'])} "
        f"against a threshold of {thr}. On robustness seeds {overlap or 'none'} the strongest "
        "non-attacker outscored the weakest attacker, so no single cut-off separates them "
        "perfectly; the frozen threshold happens to flag no decoy, but it is a few points from "
        "the strongest non-attackers and misses attackers in the same range.",
        "- **Weights and threshold.** Weights were set a priori and never fitted. Only the",
        "  threshold was chosen from data, from the validation seeds. Seed 42 and the robustness",
        "  seeds were not used for tuning; the validation seeds' 100% F1 is in-sample for the",
        "  threshold.",
        "- **Review band.** The margin is the mean validation gap, and that gap is dominated by two",
        f"  easy seeds (gaps {', '.join(f'{v:.1f}' for v in data['validation_gaps'].values())}). It",
        "  is a heuristic for routing cases to the agents, not a calibrated probability. It also",
        "  routes decoys: see the decoy column of the band table.",
        "- **Hints.** Rewritten after the seed 42 confusion was known, so seed 42 is not held out",
        "  for it. All attacker alerts on the other seeds were hinted correctly, but the hint is a",
        "  three-way decision list built for these three scenarios; a new pattern would be hinted as",
        "  one of them or `unclassified`. A needs_review alert made only of decoys has no correct",
        "  hint by construction.",
        f"- **Ablation.** Graph features alone reach robustness F1 {f1_range('graph_only')} "
        f"(precision stays 100%); behavior features alone {f1_range('behavior_only')}; "
        f"device/funding components only {f1_range('components_only')}; the flat funding rule "
        f"{f1_range('flat_funding')}; the full detector {f1_range('full')}. Graph structure carries "
        "most of the separation and behavior adds the last recall points; neither half is as "
        "good alone. The graph/behavior split of features is a judgement made before the runs, "
        "and the full-detector row's validation F1 is in-sample.",
        "- The flat rule scores the same on the original seeds because decoys and evasions are",
        "  generated identically there; only the background trades differ.",
        "",
    ]


def sweep_chart(data: dict[str, Any], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = data["sweep"]["thresholds"]
    groups = (
        ("Validation seeds", [str(s) for s in data["validation_seeds"]], "#8aa9c9"),
        ("Seed 42 (held out)", [str(data["evaluation_seed"])], ORANGE),
        ("Robustness seeds", [str(s) for s in data["robustness_seeds"]], "#b9c9db"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.9), facecolor=LIGHT["bg"], sharey=True)
    for ax, metric in zip(axes, ("precision", "recall", "f1"), strict=True):
        ax.set_facecolor(LIGHT["bg"])
        series = {}
        for _, keys, color in groups:
            for k in keys:
                ys = [float("nan") if y is None else y for y in data["sweep"]["seeds"][k][metric]]
                series[k] = ys
                ax.plot(ts, ys, color=color, linewidth=0.9, alpha=0.9, zorder=2)
        mean = []
        for col in zip(*series.values(), strict=True):
            defined = [v for v in col if v == v]  # precision is undefined when nothing is flagged
            mean.append(sum(defined) / len(defined) if defined else float("nan"))
        ax.plot(ts, mean, color=BLUE, linewidth=2.4, zorder=4)
        ax.axvline(
            data["threshold"], color=LIGHT["ink"], linestyle=(0, (4, 3)), linewidth=1, zorder=3
        )
        ax.set_title(
            {"precision": "Precision", "recall": "Recall", "f1": "F1"}[metric],
            loc="left",
            fontsize=11,
            color=LIGHT["ink"],
        )
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 1.04)
        ax.set_xlabel("score threshold", fontsize=9, color=LIGHT["muted"])
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(["0", "25%", "50%", "75%", "100%"], fontsize=8, color=LIGHT["muted"])
        ax.tick_params(axis="x", labelsize=8, colors=LIGHT["muted"], length=0)
        ax.tick_params(axis="y", length=0)
        ax.grid(color=LIGHT["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(LIGHT["grid"])
    from matplotlib.lines import Line2D

    handles = [
        Line2D([0], [0], color="#8aa9c9", lw=1.2, label="validation seeds"),
        Line2D([0], [0], color="#b9c9db", lw=1.2, label="robustness seeds"),
        Line2D([0], [0], color=ORANGE, lw=1.2, label="seed 42"),
        Line2D([0], [0], color=BLUE, lw=2.4, label="mean of all"),
        Line2D([0], [0], color=LIGHT["ink"], lw=1, linestyle=(0, (4, 3)), label="frozen threshold"),
    ]
    fig.legend(
        handles=handles,
        frameon=False,
        fontsize=9,
        loc="lower center",
        ncol=5,
        labelcolor=LIGHT["ink"],
    )
    fig.suptitle(
        "Full detector over a threshold sweep (synthetic benchmark)",
        x=0.01,
        ha="left",
        fontsize=12,
        color=LIGHT["ink"],
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, facecolor=LIGHT["bg"])

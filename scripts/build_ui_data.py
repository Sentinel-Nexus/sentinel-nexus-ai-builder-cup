"""Build services/app/static/data/benchmark.json from docs/benchmark.md and docs/agent-evals.md.

    python scripts/build_ui_data.py          # write the file
    python scripts/build_ui_data.py --check  # exit 1 if the committed file is out of date

The Benchmark screen shows these numbers, so they come from the same documents as the write-up
and the file is checked in a unit test. No cloud access.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "services" / "app" / "static" / "data" / "benchmark.json"


def sections(md: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", md, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def tables(text: str) -> list[list[dict]]:
    """Every markdown table in ``text`` as a list of {header: cell} rows."""
    out, cur = [], []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            cur.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif cur:
            head, rows = cur[0], [r for r in cur[2:] if len(r) == len(cur[0])]
            out.append([dict(zip(head, r, strict=True)) for r in rows])
            cur = []
    return out


def pct(s: str) -> float | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s*", s)
    return float(m.group(1)) if m else None


def triple(s: str) -> dict:
    return dict(zip(("min", "median", "max"), [pct(x) for x in s.split("/")], strict=True))


def prf(s: str) -> dict:
    return dict(zip(("precision", "recall", "f1"), [pct(x) for x in s.split("/")], strict=True))


def bullets(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("- "):
            out.append(line[2:].strip())
        elif out and line.startswith("  ") and line.strip():
            out[-1] += " " + line.strip()
    return [re.sub(r"[*`]", "", b) for b in out]


LIMITS = [
    {
        "title": "Borderline attacker EV_C004 was cleared",
        "text": "The one attacker that scored inside the review band (below the 43.0 threshold) "
        "was cleared by the agent, which also disagreed with the detector's hint. The threshold "
        "and the decision logic were not changed to catch it. Borderline attackers escalated: "
        "0 of 1.",
    },
    {
        "title": "The busy-but-honest decoy ended uncertain",
        "text": "EV_DG3_busy_honest was neither escalated nor cleared: the agent returned "
        "uncertain and the case sits in the needs-analyst lane. No decoy was escalated "
        "(0 of 3 groups, 0 of 23 accounts), but only 2 of 3 decoy groups were cleared.",
    },
    {
        "title": "Recall at ring level follows from escalating whole rings",
        "text": "Attacker recall of 39/39 holds because the agent decides for the whole alert and "
        "every member of an escalated ring counts. It no longer measures per-account judgement, "
        "and the exclusion path was never exercised on this data.",
    },
    {
        "title": "The data is synthetic",
        "text": "Scenarios, decoys, generator and detector come from one project. The labels say "
        "what the generator planted, not what a real market contains. Nothing here is evidence "
        "about real prediction markets.",
    },
    {
        "title": "Decoy and borderline cases are injected",
        "text": "The four EV_ cases are needs_review alerts built at the accounts' real scores, so "
        "they test the investigation, not the detector. Seed 42 has no detector needs_review "
        "alert, and these four were not re-run after the membership change.",
    },
    {
        "title": "The margin is thin",
        "text": "One evaded coordination account scored 42.99 against the 43.0 threshold and was "
        "missed by the detector. The weights are fixed a priori; only the threshold came from "
        "data.",
    },
]


def build() -> dict:
    bm_text = (REPO / "docs" / "benchmark.md").read_text(encoding="utf-8")
    bm = sections(bm_text)
    ev_text = (REPO / "docs" / "agent-evals.md").read_text(encoding="utf-8")
    ev = sections(ev_text)
    head_ev = ev_text.split("\n## ")[0]

    seed42 = [
        {
            "group": r["Group"],
            "method": r["Method"],
            "flagged": int(r["Flagged"]),
            "tp": int(r["True pos"]),
            "fp": int(r["False pos"]),
            "precision": pct(r["Precision"]),
            "recall": pct(r["Recall"]),
            "f1": pct(r["F1"]),
        }
        for r in tables(bm["Seed 42 (held-out evaluation)"])[0]
    ]
    overall = {r["method"]: r for r in seed42 if r["group"] == "all_scenarios"}
    detector = next(v for k, v in overall.items() if k.startswith("Detector"))
    baseline = next(v for k, v in overall.items() if k.startswith("Flat rule"))
    threshold = float(re.search(r"flag at score >= \*\*([\d.]+)\*\*", bm_text).group(1))

    innocent = [
        {
            "group": r["Group"],
            "accounts": int(r["Accounts"]),
            "flat_rule": int(r["Flat rule flagged"]),
            "detector": int(r["Detector flagged"]),
        }
        for r in tables(bm["Innocent groups (false positives)"])[0]
    ]
    ablation = [
        {
            "variant": r["Variant"],
            "cutoff": r["Cut-off"],
            "validation_f1": pct(r["Validation F1"]),
            "seed42": prf(r["Seed 42 P / R / F1"]),
            "robustness_f1": triple(r["Robustness F1 min / median / max"]),
            "robustness_recall": triple(r["Robustness recall min / median / max"]),
        }
        for r in tables(bm["Ablation"])[0]
    ]
    rob = [
        {
            "method": r["Method"],
            "precision": triple(r["Precision"]),
            "recall": triple(r["Recall"]),
            "f1": triple(r["F1"]),
        }
        for r in tables(bm["Robustness seeds (frozen threshold)"])[1]
    ]

    head_tables = tables(head_ev)
    per_case = [
        {
            "alert": r["Alert"],
            "kind": r["Kind"],
            "decision": r["Decision"],
            "tokens": int(r["Tokens"]),
            "latency_ms": int(r["Latency ms"]),
            "engines": r["Engines"],
        }
        for r in head_tables[2]
    ]
    adversarial = [
        {"claim": r["Injected claim"], "stage": r["Stage"], "verdict": r["Critic verdict"]}
        for r in tables(ev["Adversarial test (current pipeline)"])[0]
    ]
    run_line = re.search(r"^Run (.+?) on seed", head_ev, flags=re.M)
    return {
        "source": ["docs/benchmark.md", "docs/agent-evals.md"],
        "data_note": "All data is synthetic: generated scenarios with planted labels.",
        "agent_run": run_line.group(1) if run_line else "",
        "threshold": threshold,
        "seed42": seed42,
        "kpi": {"detector": detector, "baseline": baseline},
        "innocent_groups": innocent,
        "ablation": ablation,
        "robustness_range": rob,
        "benchmark_caveats": bullets(bm["Caveats"]),
        "agent_metrics": [{"metric": r["Metric"], "result": r["Result"]} for r in head_tables[0]],
        "agent_cases": per_case,
        "adversarial": adversarial,
        "agent_caveats": bullets(ev["Caveats"]),
        "limits": LIMITS,
    }


def render() -> str:
    return json.dumps(build(), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        have = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if have.replace("\r\n", "\n") != text:
            print(f"{OUT} is out of date: run python scripts/build_ui_data.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

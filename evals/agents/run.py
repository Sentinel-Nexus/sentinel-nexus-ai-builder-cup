"""Run the agent evals.

    python -m evals.agents.run                 # dry run: lists the cases and prints the estimate
    python -m evals.agents.run --apply         # spends Gemini tokens (hard cap: --max-runs)

Cases: every real detector alert (wash ring, coordination rings, mule ring), the review-band
attacker and the three decoy groups as needs_review alerts (see cases.py), plus one adversarial
run that injects unsupported claims. Results go to docs/results/agent_evals.json and
docs/agent-evals.md. Every run is also written to audit.agent_events.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from evals.agents import cases as eval_cases
from evals.agents import metrics
from services.app.agents import AdkBackend
from services.app.cases import BigQueryCases, MemoryCases
from services.app.config import Settings
from services.app.cost import estimate, format_estimate
from services.app.main import default_deps
from services.app.models import Claim
from services.app.pipeline import investigate

REPO = Path(__file__).resolve().parents[2]
OUT_JSON = REPO / "docs" / "results" / "agent_evals.json"
OUT_MD = REPO / "docs" / "agent-evals.md"
INJECTED_TEXTS = (
    "W001 received funds from a mixer wallet shared with 40 other accounts",
    "Every account in the ring is KYC tier 3 and older than 400 days",
)


class RoutingCases:
    """Real alerts go to risk_curated.case_reports, injected eval alerts stay in memory."""

    def __init__(self, real: BigQueryCases):
        self.real, self.mem = real, MemoryCases()

    def _pick(self, alert_id: str):
        return self.mem if alert_id.startswith("EV_") else self.real

    def get(self, alert_id):
        return self._pick(alert_id).get(alert_id)

    def put(self, row):
        self._pick(row["alert_id"]).put(row)

    def latest(self):
        return {**self.real.latest(), **self.mem.latest()}


class InjectingBackend(AdkBackend):
    """Adds two unsupported claims to every Investigator answer. The Critic must reject both."""

    def investigate(self, ctx, tools, brief, feedback):
        findings = super().investigate(ctx, tools, brief, feedback)
        real_ids = [i for i in ctx.ledger.ids() if i.startswith("UsesDevice:")][:1]
        findings.claims += [
            Claim(id="x1", text=INJECTED_TEXTS[0], evidence_ids=["FundedBy:W001>FX_NOT_REAL"]),
            Claim(id="x2", text=INJECTED_TEXTS[1], evidence_ids=real_ids or ["Account:W001"]),
        ]
        return findings


def plan(deps, labels) -> list[dict]:
    real = [a for a in deps.store.alerts() if not a["alert_id"].startswith("EV_")]
    injected = eval_cases.injected_alerts(deps.store, labels)
    deps.store.extra_alerts = injected
    out = [{"alert_id": a["alert_id"], "kind": "real", "status": a["status"]} for a in real]
    for a in injected:
        kind = "borderline_attacker" if a["injected"] == "borderline_attacker" else "decoy"
        out.append({"alert_id": a["alert_id"], "kind": kind, "status": a["status"]})
    return out


def run_case(deps, item: dict) -> dict:
    run = investigate(deps, item["alert_id"], force=True, source="eval")
    t0 = time.monotonic()
    repeat = investigate(deps, item["alert_id"])  # cache hit: no agents, no tokens
    return {**item, "run": run, "repeat_ms": int((time.monotonic() - t0) * 1000),
            "repeat_cached": repeat["cached"]}  # fmt: skip


def adversarial(deps, alert_id: str) -> dict:
    deps.backend = InjectingBackend(deps.settings)
    deps.cases = MemoryCases()
    res = investigate(deps, alert_id, force=True, source="eval-adversarial")
    report = res["report"]
    table_text = " ".join(row["claim"] for row in report["evidence_table"])
    rejected = [t for t in INJECTED_TEXTS if any(t in u for u in report["unsupported_claims"])]
    leaked = [t for t in INJECTED_TEXTS if t in table_text]
    return {"alert_id": alert_id, "injected": len(INJECTED_TEXTS), "rejected": len(rejected),
            "leaked_into_report": len(leaked), "passed": len(rejected) == len(INJECTED_TEXTS)
            and not leaked, "tokens": res["tokens"], "tool_calls": res["tool_calls"]}  # fmt: skip


def render_md(summary: dict) -> str:
    m = summary["metrics"]
    rec = m["attacker_recall"]
    lines = [
        "# Agent evals (P4)",
        "",
        f"Run {summary['run_ts']} on seed 42 live data, models {summary['models']}, "
        f"policy retrieval `{summary['retrieval']}`.",
        "",
        "Cases: the real detector alerts, the review-band attacker and the three decoy groups as "
        "`needs_review` alerts (injected at their real detector scores, see `evals/agents/cases.py`), "
        "and one adversarial run. Labels are used only for scoring.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Attacker recall (per account) | {rec['caught']}/{rec['of']} ({_pct(rec['rate'])}) |",
    ]
    for s, v in rec["by_scenario"].items():
        lines.append(f"| - {s} | {v['caught']}/{v['of']} ({_pct(v['rate'])}) |")
    e, c, d, b = m["evasion_caught"], m["claim_support_rate"], m["decoys"], m["borderline"]
    lines += [
        f"| Evasion cases caught (left the shared funding source) | {e['caught']}/{e['of']} ({_pct(e['rate'])}) |",
        f"| Claim support rate (final / first pass) | {_pct(c['final'])} / {_pct(c['first_pass'])} of {c['claims']} claims |",
        f"| Critic rejection rate | {m['critic_rejection_rate']['rejected']}/{m['critic_rejection_rate']['reviewed']} ({_pct(m['critic_rejection_rate']['rate'])}) |",
        f"| False escalations, decoy groups | {d['alerts_escalated']}/{d['groups']} groups, {d['accounts_escalated']}/{d['accounts']} accounts |",
        f"| Borderline attackers escalated | {b['attackers_escalated']}/{b['attackers_in_band']} |",
        f"| Decoy groups cleared | {b['decoy_groups_cleared']}/{b['decoy_groups']} |",
        f"| Alerts with an accepted merge proposal | {m['merge']['alerts_with_accepted_merge']} |",
        f"| Disagreed with detector hint | {m['hint']['disagrees']} ({', '.join(m['hint']['disagreeing_alerts']) or 'none'}) |",
        f"| Avg tokens / tool calls per run | {m['cost']['avg_tokens']} / {m['cost']['avg_tool_calls']} |",
        f"| Runs that hit a budget | {m['cost']['budget_exhausted']} |",
    ]
    if rec["missed"]:
        lines.append(f"| Missed attackers | {', '.join(rec['missed'])} |")
    adv = summary.get("adversarial")
    if adv:
        lines.append(
            f"| Adversarial: injected unsupported claims rejected | {adv['rejected']}/{adv['injected']}"
            f", leaked into report {adv['leaked_into_report']} ({'PASS' if adv['passed'] else 'FAIL'}) |"
        )
    lines += ["", "Latency (ms) by graph engine and cache state:", "",
              "| Group | n | avg | max |", "|---|---|---|---|"]  # fmt: skip
    for k, v in m["latency_ms"].items():
        lines.append(f"| {k} | {v['n']} | {v['avg']} | {v['max']} |")
    lines += ["", "Per case:", "", "| Alert | Kind | Decision | Tokens | Latency ms | Engines |",
              "|---|---|---|---|---|---|"]  # fmt: skip
    for r in summary["cases"]:
        lines.append(
            f"| {r['alert_id']} | {r['kind']} | {r['decision']} | {r['tokens']} | {r['latency_ms']} | {r['engines']} |"
        )
    return "\n".join(lines) + "\n"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="spend Gemini tokens (default: dry run)")
    ap.add_argument("--max-runs", type=int, default=12, help="hard cap on investigations")
    ap.add_argument("--no-adversarial", action="store_true")
    args = ap.parse_args(argv)

    settings = Settings()
    deps = default_deps(settings)
    deps.cases = RoutingCases(deps.cases)
    from google.cloud import bigquery

    labels = eval_cases.load_labels(
        bigquery.Client(project=settings.project, location="US"), settings.project
    )
    items = plan(deps, labels)
    n = len(items) + (0 if args.no_adversarial else 1)
    print(f"cases ({n} investigations):")
    for it in items:
        print(f"  {it['alert_id']:<22} {it['kind']:<20} {it['status']}")
    if not args.no_adversarial:
        print("  (adversarial run on the wash ring alert)")
    print(format_estimate(estimate(n, settings)))
    if n > args.max_runs:
        print(f"refusing: {n} investigations > --max-runs {args.max_runs}", file=sys.stderr)
        return 2
    if not args.apply:
        print("dry run: nothing was sent to Gemini. Re-run with --apply.")
        return 0

    deps.graph.warm()
    results = []
    for it in items:
        print(f"running {it['alert_id']} ...", flush=True)
        r = run_case(deps, it)
        results.append(r)
        rep = r["run"]["report"]
        print(f"  {rep['decision']} tokens={r['run']['tokens']} {r['run']['latency_ms']} ms "
              f"engines={r['run']['engines']}", flush=True)  # fmt: skip
    real_backend, real_cases = deps.backend, deps.cases
    adv = None
    if not args.no_adversarial:
        wash = next(
            a["alert_id"] for a in deps.store.alerts() if a["scenario_hint"] == "wash_trading"
        )
        print(f"adversarial run on {wash} ...", flush=True)
        adv = adversarial(deps, wash)
        print(f"  {adv}", flush=True)
    deps.backend, deps.cases = real_backend, real_cases

    summary = {
        "run_ts": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "models": deps.backend.models,
        "retrieval": "keyword",
        "metrics": metrics.compute(results, labels),
        "adversarial": adv,
        "cases": [
            {
                "alert_id": r["alert_id"],
                "kind": r["kind"],
                "decision": r["run"]["report"]["decision"],
                "tokens": r["run"]["tokens"],
                "latency_ms": r["run"]["latency_ms"],
                "engines": r["run"]["engines"],
                "tool_calls": r["run"]["tool_calls"],
                "escalate_accounts": r["run"]["report"]["escalate_accounts"],
                "unsupported_claims": r["run"]["report"]["unsupported_claims"],
                "summary": r["run"]["report"]["summary"],
            }  # fmt: skip
            for r in results
        ],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_md(summary), encoding="utf-8", newline="\n")
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_MD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

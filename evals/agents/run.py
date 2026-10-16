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
from services.app.audit import drain_all
from services.app.cases import BigQueryCases, MemoryCases
from services.app.config import Settings
from services.app.cost import estimate, format_estimate
from services.app.main import default_deps
from services.app.models import Claim
from services.app.pipeline import investigate

REPO = Path(__file__).resolve().parents[2]
OUT_JSON = REPO / "docs" / "results" / "agent_evals.json"
OUT_MD = REPO / "docs" / "agent-evals.md"
CASE_DIR = (
    REPO / "docs" / "results" / "agent_eval_cases"
)  # one file per case, rebuilt into the summary
INJECTED_TEXTS = (
    # 1: invented evidence id -> must die at the ledger check
    "W001 received funds from a mixer wallet shared with 40 other accounts",
    # 2: a real id, but the claim states a number the cited rows do not contain
    "Every account in the ring is KYC tier 3 and older than 400 days",
    # 3: real ids, no numbers: only the LLM Critic can catch that the rows do not show this fact
    "Accounts W001 and W002 were created within one hour of each other",
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
    """Adds unsupported claims to every Investigator answer. The Critic must reject all of them."""

    def investigate(self, ctx, tools, brief, feedback):
        findings = super().investigate(ctx, tools, brief, feedback)
        real_ids = [i for i in ctx.ledger.ids() if i.startswith("UsesDevice:")]
        findings.claims += [
            Claim(id="x1", text=INJECTED_TEXTS[0], evidence_ids=["FundedBy:W001>FX_NOT_REAL"]),
            Claim(id="x2", text=INJECTED_TEXTS[1], evidence_ids=real_ids[:1] or ["Account:W001"]),
            Claim(id="x3", text=INJECTED_TEXTS[2], evidence_ids=real_ids[:2] or ["Account:W001"]),
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


def trace_summary(events: list[dict]) -> dict:
    """Per-agent wall time, model calls, tokens in/out and retries from one run's events."""
    out: dict[str, dict] = {}
    for e in events:
        a = out.setdefault(e["agent"], {"llm_calls": 0, "tokens_in": 0, "tokens_out": 0,
                                         "llm_ms": 0, "retries": 0, "tool_calls": 0})  # fmt: skip
        d = json.loads(e["detail"]) if e.get("detail") else {}
        if e["event_type"] == "llm_call":
            a["llm_calls"] += 1
            a["tokens_in"] += d.get("tokens_in", 0)
            a["tokens_out"] += d.get("tokens_out", 0) + d.get("tokens_thinking", 0)
            a["llm_ms"] += e.get("latency_ms") or 0
        elif e["event_type"] == "retry":
            a["retries"] += 1
        elif e["event_type"] == "tool_call":
            a["tool_calls"] += 1
    return out


def run_case(deps, item: dict) -> dict:
    events: list[dict] = []
    run = investigate(deps, item["alert_id"], force=True, source="eval", listeners=[events.append])
    if run["status"] != "ok":
        return {**item, "run": run, "trace": trace_summary(events)}
    t0 = time.monotonic()
    repeat = investigate(deps, item["alert_id"])  # cache hit: no agents, no tokens
    return {**item, "run": run, "repeat_ms": round((time.monotonic() - t0) * 1000, 3),
            "repeat_cached": repeat["cached"], "trace": trace_summary(events)}  # fmt: skip


def adversarial(deps, alert_id: str) -> dict:
    deps.backend = InjectingBackend(deps.settings)
    deps.cases = MemoryCases()
    events: list[dict] = []
    res = investigate(
        deps, alert_id, force=True, source="eval-adversarial", listeners=[events.append]
    )
    base = {"alert_id": alert_id, "injected": len(INJECTED_TEXTS), "tokens": res["tokens"],
            "tool_calls": res["tool_calls"], "latency_ms": res["latency_ms"]}  # fmt: skip
    if res["status"] != "ok":
        return {**base, "status": res["status"], "error": res.get("error"), "rejected": 0,
                "leaked_into_report": 0, "passed": False}  # fmt: skip
    report = res["report"]
    # claim ids are renumbered after the Investigator answers: find the injected ones by text
    passes = [
        e for e in events if e["event_type"] == "step" and e["summary"].startswith("decision")
    ]
    verdicts = [e for e in events if e["event_type"] == "verdict"]
    detail = []
    for text in INJECTED_TEXTS:
        per_pass = []
        for i, p in enumerate(passes):
            claims = json.loads(p["detail"])["claims"]
            cid = next((c["id"] for c in claims if c["text"] == text), None)
            ev_ids = next((c["evidence_ids"] for c in claims if c["text"] == text), None)
            start = p["seq"]
            end = passes[i + 1]["seq"] if i + 1 < len(passes) else 10**9
            for v in verdicts:
                d = json.loads(v["detail"])
                if cid and start < v["seq"] < end and v["summary"].startswith(f"{cid}:"):
                    per_pass.append({"pass": i + 1, "evidence_ids": ev_ids, "accepted": d["accepted"],
                                     "stage": d["stage"], "verdict": v["summary"]})  # fmt: skip
        detail.append({"claim": text, "verdicts": per_pass})
    table_text = " ".join(row["claim"] for row in report["evidence_table"])
    rejected = [t for t in INJECTED_TEXTS if any(t in u for u in report["unsupported_claims"])]
    leaked = [t for t in INJECTED_TEXTS if t in table_text]
    return {**base, "rejected": len(rejected), "leaked_into_report": len(leaked),
            "passed": len(rejected) == len(INJECTED_TEXTS) and not leaked,
            "claims": detail, "report_decision": report["decision"],
            "unsupported_in_report": report["unsupported_claims"]}  # fmt: skip


def save_case(r: dict) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    out = {**r, "saved_ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}
    (CASE_DIR / f"{r['alert_id']}.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8", newline="\n"
    )


def load_cases() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(CASE_DIR.glob("*.json"))
        if not p.name.startswith("_")
    ]


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
        f"| Investigator claims kept by the Critic (final / first pass) | {_pct(c['final'])} / {_pct(c['first_pass'])} of {c['claims']} claims |",
        f"| Claims in the final reports with resolvable evidence ids | {c['report']['resolvable']}/{c['report']['claims']} ({_pct(c['report']['rate'])}), measured on {c['report']['cases_measured']} of {c['report']['cases']} cases |",
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


def build_summary(deps, labels, failures, stopped, expected_ids) -> dict:
    stored = load_cases()
    done = [r for r in stored if r["run"]["status"] == "ok"]
    done_ids = [r["alert_id"] for r in done]
    adv_path = CASE_DIR / "_adversarial.json"
    adv = json.loads(adv_path.read_text(encoding="utf-8")) if adv_path.exists() else None
    finished = {g for g in labels["decoy_groups"] if f"EV_{g}" in done_ids}
    lab = {**labels, "decoy_groups": {g: labels["decoy_groups"][g] for g in finished}}
    missing = [i for i in expected_ids if i not in done_ids]
    return {
        "run_ts": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "status": "complete" if not missing and adv else "partial",
        "models": deps.backend.models,
        "retrieval": "keyword",
        "completed": done_ids,
        "not_completed": [{"alert_id": i, "reason": "not run"} for i in missing]
        + ([] if adv else [{"alert_id": "adversarial", "reason": "not run"}]),
        "metrics": metrics.compute(done, lab),
        "adversarial": adv,
        "failures": failures,
        "stopped": stopped,
        "cases": [
            {
                "alert_id": r["alert_id"],
                "kind": r["kind"],
                "trace": r["trace"],
                "decision": r["run"]["report"]["decision"],
                "tokens": r["run"]["tokens"],
                "latency_ms": r["run"]["latency_ms"],
                "repeat_ms": r.get("repeat_ms"),
                "repeat_cached": r.get("repeat_cached"),
                "engines": r["run"]["engines"],
                "tool_calls": r["run"]["tool_calls"],
                "escalate_accounts": r["run"]["report"]["escalate_accounts"],
                "unsupported_claims": r["run"]["report"].get("unsupported_claims", []),
                "summary": r["run"]["report"].get("summary", ""),
                "saved_ts": r.get("saved_ts"),
            }
            for r in done
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="spend Gemini tokens (default: dry run)")
    ap.add_argument("--max-runs", type=int, default=12, help="hard cap on investigations")
    ap.add_argument("--no-adversarial", action="store_true")
    ap.add_argument("--adversarial-only", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated alert ids to run")
    ap.add_argument("--max-minutes", type=float, default=20.0, help="total wall-clock cap")
    args = ap.parse_args(argv)

    settings = Settings()
    deps = default_deps(settings)
    deps.cases = RoutingCases(deps.cases)
    from google.cloud import bigquery

    labels = eval_cases.load_labels(
        bigquery.Client(project=settings.project, location="US"), settings.project
    )
    all_items = plan(deps, labels)
    items = all_items
    if args.only:
        wanted = set(args.only.split(","))
        items = [it for it in all_items if it["alert_id"] in wanted]
    if args.adversarial_only:
        items = []
    do_adv = not args.no_adversarial and (args.adversarial_only or not args.only)
    n = len(items) + (1 if do_adv else 0)
    print(f"cases ({n} investigations):")
    for it in items:
        print(f"  {it['alert_id']:<22} {it['kind']:<20} {it['status']}")
    if do_adv:
        print(f"  (adversarial run, {len(INJECTED_TEXTS)} injected claims, on the wash ring alert)")
    print(format_estimate(estimate(n, settings)))
    print(f"per-case deadline {settings.limits.wall_clock_s:.0f} s, wall-clock cap "
          f"{args.max_minutes:.0f} min, call cap {n} investigations")  # fmt: skip
    if n > args.max_runs:
        print(f"refusing: {n} investigations > --max-runs {args.max_runs}", file=sys.stderr)
        return 2
    if not args.apply:
        print("dry run: nothing was sent to Gemini. Re-run with --apply.")
        return 0

    deps.graph.warm()
    deps.store.warm()
    print(f"model warm-up {deps.backend.warm():.1f}s", flush=True)
    started = time.monotonic()
    failures, stopped = [], None
    for it in items:
        if time.monotonic() - started > args.max_minutes * 60:
            stopped = (
                f"wall-clock cap of {args.max_minutes:.0f} min reached before {it['alert_id']}"
            )
            print(stopped, flush=True)
            break
        print(f"running {it['alert_id']} ...", flush=True)
        r = run_case(deps, it)
        run = r["run"]
        save_case(r)  # a failed case is stored too, with its status
        if run["status"] != "ok":
            failures.append({"alert_id": it["alert_id"], "status": run["status"],
                             "error": run.get("error"), "latency_ms": run["latency_ms"]})  # fmt: skip
            print(f"  {run['status']}: {run.get('error')} after {run['latency_ms']} ms", flush=True)
            continue
        print(f"  {run['report']['decision']} tokens={run['tokens']} {run['latency_ms']} ms "
              f"repeat_ms={r.get('repeat_ms')} engines={run['engines']} "
              f"trace={json.dumps(r['trace'])}", flush=True)  # fmt: skip
    real_backend, real_cases = deps.backend, deps.cases
    if do_adv and stopped is None:
        wash = next(
            a["alert_id"] for a in deps.store.alerts() if a["scenario_hint"] == "wash_trading"
        )
        print(f"adversarial run on {wash} ...", flush=True)
        adv = adversarial(deps, wash)
        CASE_DIR.mkdir(parents=True, exist_ok=True)
        (CASE_DIR / "_adversarial.json").write_text(
            json.dumps({**adv, "saved_ts": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())},
                       indent=1), encoding="utf-8", newline="\n")  # fmt: skip
        print(f"  passed={adv['passed']} rejected={adv['rejected']}/{adv['injected']} "
              f"leaked={adv['leaked_into_report']}", flush=True)  # fmt: skip
    deps.backend, deps.cases = real_backend, real_cases

    drain_all()
    summary = build_summary(deps, labels, failures, stopped, [it["alert_id"] for it in all_items])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_md(summary), encoding="utf-8", newline="\n")
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_MD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

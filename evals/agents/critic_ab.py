"""Does the Pro Critic beat the Flash Critic? Measure it on labelled claims.

    python -m evals.agents.critic_ab            # dry run: prints the claims and the call budget
    python -m evals.agents.critic_ab --apply    # 2 models x 2 repeats = 4 Gemini calls

The claims are built from a real tool run on the wash ring (5 true claims taken straight from the
tool rows, 5 false claims that differ from the rows in one way each: wrong account, wrong count,
wrong direction, a fact the rows do not contain, an overclaim). Both models see identical input.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from services.app.audit import MemoryAudit
from services.app.config import Settings
from services.app.main import default_deps
from services.app.runctx import RunContext
from services.app.tools import build_tools

OUT = Path(__file__).resolve().parents[2] / "docs" / "results" / "critic_ab.json"
MODELS = ("gemini-2.5-flash", "gemini-2.5-pro")
REPEATS = 2


def build_items(deps) -> list[dict]:
    alert = next(a for a in deps.store.alerts() if a["scenario_hint"] == "wash_trading")
    ctx = RunContext(alert=alert, settings=deps.settings, audit=MemoryAudit())
    tools = {t.__name__: t for t in build_tools(ctx, deps.store, deps.graph, deps.policy)}
    graph = tools["graph"]("ring_subgraph", alert["ring_id"])
    market = deps.store.dominant_market(alert["member_accounts"])
    trades = tools["trades_for"](alert["member_accounts"][:200], market)
    shared = [n for n in graph["shared_nodes"] if n["node"].startswith("Device:")]
    d1, d2 = shared[0], shared[1]

    def edges(node, accts):
        return [f"UsesDevice:{a}>{node.split(':', 1)[1]}" for a in accts]

    first = trades["edges"][0]
    buy = next(t for t in trades["trades"] if t["side"] == "BUY")
    rows = {"c": []}

    def item(cid, text, ids, label):
        found, _ = ctx.ledger.resolve(ids)
        assert not _, f"missing ids {_}"
        rows["c"].append(
            {
                "claim_id": cid,
                "claim": text,
                "label": label,
                "evidence": [{"id": i, "row": r["row"]} for i, r in found.items()][:12],
            }  # fmt: skip
        )

    a1, a2 = d1["accounts"][0], d1["accounts"][1]
    b1 = d2["accounts"][0]
    item("t1", f"Accounts {', '.join(d1['accounts'])} share device {d1['node']}.",
         edges(d1["node"], d1["accounts"]), True)  # fmt: skip
    item("t2", f"Accounts {', '.join(d2['accounts'])} share device {d2['node']}.",
         edges(d2["node"], d2["accounts"]), True)  # fmt: skip
    item("t3", f"Account {first['account_id']} made {first['n_trades']} trades in {market}.",
         [first["id"]], True)  # fmt: skip
    item("t4", f"A BUY of {buy['size_usd']:.2f} USD by {buy['account_id']} exists in {market}.",
         [buy["id"]], True)  # fmt: skip
    item("t5", f"Accounts {a1} and {a2} are linked through {d1['node']}.",
         edges(d1["node"], [a1, a2]), True)  # fmt: skip
    item(
        "f1",
        f"Account {b1} shares device {d1['node']} with {a1}.",
        edges(d1["node"], [a1]) + edges(d2["node"], [b1]),
        False,
    )  # wrong account
    item("f2", f"Account {first['account_id']} made {first['n_trades'] + 7} trades in {market}.",
         [first["id"]], False)  # wrong count  # fmt: skip
    item("f3", f"Account {buy['account_id']} SOLD {buy['size_usd']:.2f} USD in {market}.",
         [buy["id"]], False)  # wrong direction  # fmt: skip
    item("f4", f"Accounts {a1} and {a2} were created within one hour of each other.",
         edges(d1["node"], [a1, a2]), False)  # a fact the rows do not contain  # fmt: skip
    item("f5", f"All {alert['n_members']} ring accounts share the single device {d1['node']}.",
         edges(d1["node"], d1["accounts"]), False)  # overclaim  # fmt: skip
    return rows["c"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    print(f"{len(MODELS)} models x {REPEATS} repeats = {len(MODELS) * REPEATS} Gemini calls, "
          f"10 claims each, ~3k tokens per call (about $0.03 in total)")  # fmt: skip
    if not args.apply:
        print("dry run: nothing was sent to Gemini. Re-run with --apply.")
        return 0

    from services.app.agents import AdkBackend

    deps = default_deps(Settings())
    deps.graph.warm()
    deps.store.warm()
    items = build_items(deps)
    labels = {i["claim_id"]: i["label"] for i in items}
    payload = [{k: v for k, v in i.items() if k != "label"} for i in items]
    results = {}
    for model in MODELS:
        backend = AdkBackend(Settings(model_critic=model))
        runs = []
        for rep in range(REPEATS):
            ctx = RunContext(
                alert=deps.store.alerts()[0], settings=deps.settings, audit=MemoryAudit()
            )
            t0 = time.monotonic()
            out = backend.critic_check(ctx, payload)
            ms = int((time.monotonic() - t0) * 1000)
            verdict = {r["claim_id"]: bool(r.get("supported")) for r in out}
            correct = sum(verdict.get(c) == labels[c] for c in labels)
            wrong = [c for c in labels if verdict.get(c) != labels[c]]
            runs.append({"latency_ms": ms, "tokens": ctx.budget.total_tokens, "correct": correct,
                         "of": len(labels), "wrong": wrong})  # fmt: skip
            print(f"{model} run {rep + 1}: {correct}/{len(labels)} correct, {ms} ms, "
                  f"{ctx.budget.total_tokens} tokens, wrong={wrong}", flush=True)  # fmt: skip
        results[model] = runs
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"claims": payload, "labels": labels, "results": results}, indent=1),
                   encoding="utf-8", newline="\n")  # fmt: skip
    print(f"wrote {OUT.relative_to(OUT.parents[2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

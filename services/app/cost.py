"""Gemini cost estimate, printed before any script spends tokens.

Token profile: measured on one live run (wash ring R_W001, 2026-10-16, 33,246 tokens: investigator
23.0k over 5 calls incl. one revision, critic 7.3k on gemini-2.5-pro, policy 1.1k, drafter 1.8k).
Rings with more members read more tool output, so the expected figure is scaled up. Prices are USD
per 1M tokens, from third-party calculators that agree with each other; Google's own pricing page
was only partly readable, so treat dollar figures as an estimate (+-30%). Thinking tokens are
billed as output.
"""

from __future__ import annotations

from services.app.config import Limits, Settings

PRICES = {  # model: (input, output) USD per 1M tokens
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}
# measured tokens per run by agent, scaled by 1.3 for larger rings
EXPECTED_TOKENS = {"investigator": 30_000, "policy": 1_500, "critic": 9_500, "drafter": 2_400}
OUTPUT_SHARE = {"expected": 0.25, "worst": 0.5}  # share of tokens billed at the output rate


def _usd(model: str, tokens: int, out_share: float) -> float:
    pin, pout = PRICES.get(model, PRICES["gemini-2.5-pro"])
    return tokens / 1e6 * ((1 - out_share) * pin + out_share * pout)


def models_by_agent(s: Settings) -> dict[str, str]:
    return {
        "investigator": s.model_fast,
        "policy": s.model_fast,
        "drafter": s.model_fast,
        "critic": s.model_strong,
    }


def estimate(n_runs: int, settings: Settings | None = None) -> dict:
    s = settings or Settings()
    lim: Limits = s.limits
    models = models_by_agent(s)
    exp_tokens = sum(EXPECTED_TOKENS.values())
    cap_tokens = min(lim.total_tokens, sum(lim.agent_tokens.values()))
    exp_usd = sum(_usd(models[a], t, OUTPUT_SHARE["expected"]) for a, t in EXPECTED_TOKENS.items())
    worst_usd = sum(
        _usd(models[a], lim.agent_tokens[a], OUTPUT_SHARE["worst"]) for a in lim.agent_tokens
    )
    worst_usd = min(worst_usd, worst_usd * cap_tokens / sum(lim.agent_tokens.values()))
    return {
        "runs": n_runs,
        "models": models,
        "tokens_per_run_expected": exp_tokens,
        "tokens_per_run_cap": cap_tokens,
        "tokens_expected": exp_tokens * n_runs,
        "tokens_cap": cap_tokens * n_runs,
        "usd_expected": round(exp_usd * n_runs, 2),
        "usd_worst_case": round(worst_usd * n_runs, 2),
        "tool_calls_cap": lim.max_tool_calls * n_runs,
        "llm_calls_expected": 12 * n_runs,
        "note": "prices unverified against Google's page; +-30%",
    }


def format_estimate(e: dict) -> str:
    return (
        f"runs: {e['runs']} (hard cap on investigations)\n"
        f"models: {e['models']}\n"
        f"tokens: expected {e['tokens_expected']:,}, hard cap {e['tokens_cap']:,} "
        f"({e['tokens_per_run_cap']:,}/run)\n"
        f"LLM calls: ~{e['llm_calls_expected']}; tool calls capped at {e['tool_calls_cap']}\n"
        f"cost: expected ${e['usd_expected']:.2f}, worst case ${e['usd_worst_case']:.2f} "
        f"({e['note']})"
    )

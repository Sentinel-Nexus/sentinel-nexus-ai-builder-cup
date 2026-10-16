"""The four LLM agents on Google ADK (Vertex AI Gemini).

``AdkBackend`` is the only place that talks to ADK. The orchestrator depends on the small
``Backend`` protocol, so tests run the whole pipeline with a fake backend and no Gemini spend.
Every LLM call is token-counted against the run budget and written to the audit stream.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Protocol

from services.app.budget import BudgetExceeded
from services.app.models import Findings, PolicyRef
from services.app.runctx import RunContext

ACTIONS = (
    "analyst_review",
    "temporary_trading_limit",
    "enhanced_due_diligence",
    "regulatory_referral",
    "no_action",
)


class AgentOutputError(ValueError):
    """The model did not return usable JSON."""


class Backend(Protocol):
    models: dict[str, str]

    def investigate(
        self, ctx: RunContext, tools: list, brief: dict, feedback: dict | None
    ) -> Findings: ...
    def choose_policy(
        self, ctx: RunContext, summary: dict, candidates: list[dict]
    ) -> list[PolicyRef]: ...
    def critic_check(self, ctx: RunContext, items: list[dict]) -> list[dict]: ...
    def draft(self, ctx: RunContext, material: dict) -> dict: ...


def parse_json(text: str) -> Any:
    """Parse a model reply that should be JSON (tolerates ```json fences and prose around it)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise AgentOutputError(f"not JSON: {t[:200]!r}")


INVESTIGATOR = """You are the Investigator in a prediction-market surveillance system. You \
investigate ONE detector alert and report structured findings. Data is synthetic.

Tools (all read-only): get_alert_context, graph, trades_for, account_profile, check_policy, \
related_rings. You have at most {max_calls} tool calls in total, failed calls included, so plan: \
start with get_alert_context, then graph (kind=ring_subgraph), then trades_for on the main market, \
account_profile only for accounts that are unclear, related_rings once. If a tool returns \
tool_call_limit_reached, stop calling tools and answer.

Rules:
1. Every claim carries evidence_ids copied VERBATIM from tool results of THIS run (edge ids as \
UsesDevice:A>D, trade ids such as T0001234, ring ids R_x, Account:x, Market:x, Score:x, clause ids \
SP-x.y). Never invent or reformat an id. A claim without valid ids is rejected by the Critic.
2. The detector's scenario_hint is a HYPOTHESIS. State whether you agree. If the structure says \
otherwise, disagree with evidence (claim kind "disagreement").
3. Escalate only with at least two independent signals (link structure, behaviour such as timing \
or reciprocal fills, trade evidence), per policy SP-4.1. A shared household device or a shared \
exchange funding wallet, or heavy but independent trading by aged accounts is NOT enough: clear \
those \
(SP-3.1, SP-3.2).
4. status "needs_review" means every member scored just below the alert threshold. Decide \
escalate or clear and say why. Use "uncertain" only when the evidence cannot decide.
5. Every member account of the alert must be in exactly one list: escalate_accounts or \
cleared_accounts. Accounts that left a shared funding source but still share a device, market and \
timing stay in the ring (SP-4.2).
6. If related_rings shows another ring in the same market or window, you may propose a merge: set \
merge_with to the ring ids and add a claim of kind "merge_proposal" whose evidence_ids come from \
BOTH rings. Do not merge without such evidence.
7. Keep claims short and factual (at most 8 claims). Kinds: observation, pattern, disagreement, \
merge_proposal.

Reply with ONLY this JSON, no prose:
{{"decision": "escalate|clear|uncertain", "pattern": "wash_trading|coordinated_accumulation|\
mule_funding|none|other", "agrees_with_hint": true, "escalate_accounts": ["..."], \
"cleared_accounts": ["..."], "merge_with": [], "notes": "one or two sentences", \
"claims": [{{"id": "c1", "kind": "observation", "text": "...", "evidence_ids": ["..."]}}]}}"""

POLICY = """You are the Policy agent. Given an investigation summary and candidate policy \
clauses, pick the clauses that support or limit the finding. Use ONLY clause ids from the \
candidates. Reply with ONLY JSON: {"refs": [{"clause_id": "SP-x.y", "why": "one sentence"}]} \
(at most 5)."""

CRITIC = """You are the Critic. You receive claims, each with the ledger rows behind its evidence \
ids (rows the tools actually returned in this run). A claim is supported only if those rows \
substantiate the specific facts it states (who, what, numbers, direction). Reject a claim when \
the rows are unrelated, too thin, or contradict it. Do not be lenient about numbers or \
relationships the rows do not show. Reply with ONLY JSON: {"results": [{"claim_id": "c1", \
"supported": true, "reason": "short"}]} with one entry per claim."""

DRAFTER = """You are the Drafter. Write the case report text for a human analyst from the \
accepted claims only. You never take action: you recommend, an analyst approves.
- summary: 3 to 5 sentences. Cite claims as [c1], [c2]. State the detector scenario_hint as a \
hypothesis and say whether the investigation agrees.
- pattern: one sentence.
- hint_note: one sentence on the detector hint versus the finding.
- recommended_action: one of {actions}. Use no_action when the decision is clear.
- confidence: number 0 to 1, lower when claims were rejected or the budget was exhausted.
Reply with ONLY JSON: {{"summary": "...", "pattern": "...", "hint_note": "...", \
"recommended_action": "...", "confidence": 0.0}}"""


class AdkBackend:
    """Runs each agent as a Google ADK ``LlmAgent`` on Vertex AI Gemini."""

    def __init__(self, settings):
        self.settings = settings
        self.models = {
            "investigator": settings.model_fast,
            "policy": settings.model_fast,
            "drafter": settings.model_fast,
            "critic": settings.model_strong,
        }
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.location)

    # ---- core runner ----

    def _run(
        self, ctx: RunContext, agent: str, instruction: str, prompt: str, tools=(), json_mode=False
    ):
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        limits = self.settings.limits
        ctx.budget.check_tokens(agent)
        cfg = types.GenerateContentConfig(
            max_output_tokens=limits.out_tokens[agent],
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_budget=self.settings.thinking_budget),
            **({"response_mime_type": "application/json"} if json_mode and not tools else {}),
        )
        model = self.models[agent]
        from google.adk.models.google_llm import Gemini

        # Vertex returns 429/503 under shared quota; retry with backoff instead of failing the run
        retry = types.HttpRetryOptions(
            attempts=5, initial_delay=3.0, max_delay=40.0, http_status_codes=[429, 503]
        )
        llm = LlmAgent(
            name=agent,
            model=Gemini(model=model, retry_options=retry),
            instruction=instruction,
            tools=list(tools),
            generate_content_config=cfg,
        )

        async def go() -> tuple[str, int, int]:
            sessions = InMemorySessionService()
            runner = Runner(app_name="sentinel", agent=llm, session_service=sessions)
            await sessions.create_session(
                app_name="sentinel", user_id="analyst", session_id=ctx.run_id
            )
            text, tokens, calls = "", 0, 0
            msg = types.Content(role="user", parts=[types.Part(text=prompt)])
            async for ev in runner.run_async(
                user_id="analyst", session_id=ctx.run_id, new_message=msg
            ):
                usage = ev.usage_metadata
                if usage and not ev.partial:
                    n = int(usage.total_token_count or 0)
                    tokens += n
                    calls += 1
                    ctx.budget.add_tokens(agent, n)
                    ctx.emit(agent, "llm_call", f"{model} call", tokens=n, model=model)
                if ev.is_final_response() and ev.content and ev.content.parts:
                    text = "".join(
                        p.text or "" for p in ev.content.parts if not getattr(p, "thought", False)
                    )
                if ctx.budget.exhausted or ctx.elapsed_ms() > limits.wall_clock_s * 1000:
                    break
            await runner.close()  # release the model client before asyncio.run closes the loop
            return text, tokens, calls

        t0 = time.monotonic()
        text, tokens, calls = asyncio.run(go())
        ctx.emit(
            agent,
            "step",
            f"{agent} finished",
            tokens=tokens,
            llm_calls=calls,
            model=model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return text

    # ---- agents ----

    def investigate(self, ctx, tools, brief, feedback):
        prompt = "Investigate this alert.\n" + json.dumps(brief)
        if feedback:
            prompt += (
                "\n\nThe Critic rejected some of your claims. Fix each (new evidence from the "
                "ledger "
                "or tools if you still have calls) or withdraw it. Return the COMPLETE JSON."
                "\nYour previous findings:\n"
                + json.dumps(feedback["previous"])
                + "\nRejections:\n"
                + json.dumps(feedback["rejections"])
                + "\nEvidence ids available in this run: "
                + json.dumps(feedback["ledger_ids"][:300])
            )
        instr = INVESTIGATOR.format(max_calls=self.settings.limits.max_tool_calls)
        try:
            text = self._run(ctx, "investigator", instr, prompt, tools=tools)
        except BudgetExceeded:
            text = ""
        if not text:
            return Findings(decision="uncertain", notes="no answer within the budget")
        return Findings.model_validate(parse_json(text))

    def choose_policy(self, ctx, summary, candidates):
        prompt = json.dumps({"summary": summary, "candidates": candidates})
        text = self._run(ctx, "policy", POLICY, prompt, json_mode=True)
        refs = parse_json(text).get("refs", [])
        titles = {c["clause_id"]: c["title"] for c in candidates}
        return [
            PolicyRef(clause_id=r["clause_id"], title=titles[r["clause_id"]], why=r.get("why", ""))
            for r in refs
            if r.get("clause_id") in titles
        ][:5]

    def critic_check(self, ctx, items):
        text = self._run(ctx, "critic", CRITIC, json.dumps({"claims": items}), json_mode=True)
        return parse_json(text).get("results", [])

    def draft(self, ctx, material):
        instr = DRAFTER.format(actions=", ".join(ACTIONS))
        text = self._run(ctx, "drafter", instr, json.dumps(material), json_mode=True)
        return parse_json(text)

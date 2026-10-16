"""The LLM agents on Google ADK (Vertex AI Gemini): Investigator, Critic and Drafter.

``AdkBackend`` is the only place that talks to ADK. The orchestrator depends on the small
``Backend`` protocol, so tests run the whole pipeline with a fake backend and no Gemini spend.
Every LLM call is token-counted against the run budget and written to the audit stream.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import json
import logging
import os
import re
import threading
import time
from typing import Any, Protocol

from services.app.budget import BudgetExceeded, CaseTimeout
from services.app.models import Findings
from services.app.runctx import RunContext

ACTIONS = (
    "analyst_review",
    "temporary_trading_limit",
    "enhanced_due_diligence",
    "regulatory_referral",
    "no_action",
)

_CURRENT: contextvars.ContextVar = contextvars.ContextVar("llm_call", default=None)


class RetryLog(logging.Handler):
    """Counts and reports the model client's 429/503 retries (it logs them at INFO).

    One handler for the process; the contextvar says which agent call a record belongs to, so
    concurrent calls are not mixed up."""

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        cur = _CURRENT.get()
        if cur is not None and "Retrying" in msg:
            ctx, agent, counter = cur
            counter[0] += 1
            ctx.emit(agent, "retry", msg[:200], retry_no=counter[0])


_retry_installed = False


def install_retry_log() -> None:
    global _retry_installed
    if not _retry_installed:
        log = logging.getLogger("google_genai._api_client")
        log.setLevel(logging.INFO)
        log.addHandler(RetryLog(logging.INFO))
        _retry_installed = True


class _LoopThread:
    """One long-lived event loop for every ADK call. Model clients live as long as the process,
    so a call no longer pays a fresh client and TLS set-up (15 to 20 s measured per agent run)."""

    _lock = threading.Lock()
    _loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def run(cls, coro, remaining):
        """Wait for the coroutine, polling a wall-clock deadline (``remaining()`` seconds left).
        A plain ``result(timeout)`` uses a timer that does not advance while the machine is
        suspended, so a stuck or suspended call could outlive the case deadline."""
        with cls._lock:
            if cls._loop is None:
                cls._loop = asyncio.new_event_loop()
                threading.Thread(target=cls._loop.run_forever, daemon=True, name="adk-loop").start()
        fut = asyncio.run_coroutine_threadsafe(coro, cls._loop)
        while True:
            try:
                return fut.result(1.0)
            except concurrent.futures.TimeoutError:
                if remaining() <= 0:
                    fut.cancel()
                    raise CaseTimeout("llm call exceeded the case deadline") from None


class AgentOutputError(ValueError):
    """The model did not return usable JSON."""


class Backend(Protocol):
    models: dict[str, str]

    def investigate(
        self, ctx: RunContext, tools: list, brief: dict, feedback: dict | None
    ) -> Findings: ...
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

Three read-only tool calls were already made for you in parallel and are in `prefetched`: \
get_alert_context, graph (ring_subgraph) and trades_for (main market). They count against your \
{max_calls} tool calls in total. Use further tools only for what is missing: account_profile \
(one account), related_rings (other rings in the same market or window), check_policy, or another \
trades_for. Do not repeat a prefetched call. If a tool returns tool_call_limit_reached, stop \
calling tools and answer.

Rules:
1. Every claim carries evidence_ids copied VERBATIM from tool results of THIS run: edge ids \
(UsesDevice:A>D, FundedBy:A>F, TradedWith:S>B, TradedIn:A>M), trade ids (T0001234), ring ids \
(R_x), Account:x, Market:x, Score:<account id> (never a number), clause ids (SP-x.y). \
Statistics such as counts, sides, first_ts, prices or scores are NOT ids: cite the edge or \
trade ids behind them. \
Never invent or reformat an id. A claim without valid ids is rejected.
2. State only numbers that appear in the tool results. Counts must match the member and edge lists.
3. The detector's scenario_hint is a HYPOTHESIS. State whether you agree. If the structure says \
otherwise, disagree with evidence (claim kind "disagreement").
4. Escalate only with at least two independent signals (link structure, behaviour such as timing \
or reciprocal fills, trade evidence), per policy SP-4.1. A shared household device or a shared \
exchange funding wallet, or heavy but independent trading by aged accounts is NOT enough: clear \
those (SP-3.1, SP-3.2).
5. status "needs_review" means every member scored just below the alert threshold. Decide \
escalate or clear and say why. Use "uncertain" only when the evidence cannot decide.
6. Your decision covers the WHOLE ring: every member of the alert and of the ring subgraph. \
You do not list accounts. The ring includes accounts that left a shared funding source but \
still share a device, market or timing (SP-4.2); do not drop them. Only if you have a \
specific, evidenced reason that one account is NOT part of the pattern (for example an \
independent household member), add its id to "exclusions" and add a claim of kind \
"exclusion" that names the account and cites the evidence. Otherwise leave "exclusions" \
empty.
7. If related_rings shows another ring in the same market or window, you may propose a merge: set \
merge_with to the ring ids and add a claim of kind "merge_proposal" whose evidence_ids come from \
BOTH rings. Do not merge without such evidence.
8. At most 6 claims, short and factual. Kinds: observation, pattern, disagreement, \
merge_proposal, exclusion.

Reply with ONLY this JSON, no prose:
{{"decision": "escalate|clear|uncertain", "pattern": "wash_trading|coordinated_accumulation|\
mule_funding|none|other", "agrees_with_hint": true, "exclusions": [], \
"merge_with": [], "notes": "one or two sentences", \
"claims": [{{"id": "c1", "kind": "observation", "text": "...", "evidence_ids": ["..."]}}]}}"""

CRITIC = """You are the Critic. You receive claims, each with the ledger rows behind its evidence \
ids (rows the tools actually returned in this run). edge_ids are graph edges written \
type:source>target (UsesDevice:A>D means account A uses device D): count and compare them to check \
who shares what and how many. A claim is supported only if those rows \
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
- policy_refs: up to 4 clauses chosen ONLY from policy_candidates, each with a one-sentence why.
Reply with ONLY JSON: {{"summary": "...", "pattern": "...", "hint_note": "...", \
"recommended_action": "...", "confidence": 0.0, \
"policy_refs": [{{"clause_id": "SP-x.y", "why": "..."}}]}}"""


class AdkBackend:
    """Runs each agent as a Google ADK ``LlmAgent`` on Vertex AI Gemini."""

    def __init__(self, settings):
        self.settings = settings
        self.models = {
            "investigator": settings.model_fast,
            "drafter": settings.model_fast,
            "critic": settings.model_critic,
        }
        self._clients: dict[str, Any] = {}
        self._clients_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(settings.llm_concurrency)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.location)
        install_retry_log()

    def _client(self, model: str):
        """One Gemini client per model for the life of the process."""
        from google.adk.models.google_llm import Gemini
        from google.genai import types

        with self._clients_lock:
            if model not in self._clients:
                # Vertex answers 429/503 under shared capacity: exponential backoff with jitter.
                # It sends no Retry-After header (checked), so there is nothing else to honour.
                retry = types.HttpRetryOptions(
                    attempts=5,
                    initial_delay=2.0,
                    max_delay=30.0,
                    exp_base=2.0,
                    jitter=1.0,
                    http_status_codes=[429, 503],
                )
                self._clients[model] = Gemini(model=model, retry_options=retry)
            return self._clients[model]

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
        remaining = ctx.remaining_s()
        if remaining <= 2:
            raise CaseTimeout("case deadline reached before the call")
        thinking = (
            self.settings.thinking_budget
            if agent == "investigator"
            else self.settings.thinking_small
        )
        cfg = types.GenerateContentConfig(
            max_output_tokens=limits.out_tokens[agent],
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_budget=thinking),
            **({"response_mime_type": "application/json"} if json_mode and not tools else {}),
        )
        model = self.models[agent]
        llm = LlmAgent(
            name=agent,
            model=self._client(model),
            instruction=instruction,
            tools=list(tools),
            generate_content_config=cfg,
        )
        counter = [0]

        async def go() -> tuple[str, int, int]:
            _CURRENT.set((ctx, agent, counter))
            sessions = InMemorySessionService()
            runner = Runner(app_name="sentinel", agent=llm, session_service=sessions)
            sid = f"{ctx.run_id}-{agent}-{int(time.monotonic() * 1000)}"
            await sessions.create_session(app_name="sentinel", user_id="analyst", session_id=sid)
            text, tokens, calls = "", 0, 0
            last = time.monotonic()
            msg = types.Content(role="user", parts=[types.Part(text=prompt)])
            async for ev in runner.run_async(user_id="analyst", session_id=sid, new_message=msg):
                usage = ev.usage_metadata
                if usage and not ev.partial:
                    n = int(usage.total_token_count or 0)
                    tokens += n
                    calls += 1
                    ctx.budget.add_tokens(agent, n)
                    ctx.emit(
                        agent,
                        "llm_call",
                        f"{model} call",
                        tokens=n,
                        model=model,
                        latency_ms=int((time.monotonic() - last) * 1000),
                        tokens_in=int(usage.prompt_token_count or 0),
                        tokens_out=int(usage.candidates_token_count or 0),
                        tokens_thinking=int(usage.thoughts_token_count or 0),
                        finish=str(getattr(ev, "finish_reason", None)),
                    )
                last = time.monotonic()
                if ev.is_final_response() and ev.content and ev.content.parts:
                    text = "".join(
                        p.text or "" for p in ev.content.parts if not getattr(p, "thought", False)
                    )
                if ctx.budget.exhausted or ctx.remaining_s() <= 0:
                    break
            return text, tokens, calls

        t0 = time.monotonic()
        if not self._slots.acquire(timeout=remaining):
            raise CaseTimeout("waited too long for an LLM slot")
        try:
            text, tokens, calls = _LoopThread.run(go(), ctx.remaining_s)
        finally:
            self._slots.release()
        ctx.emit(
            agent,
            "step",
            f"{agent} finished",
            retries=counter[0],
            tokens=tokens,
            llm_calls=calls,
            model=model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return text

    def warm(self) -> float:
        """One tiny call per model so the first real investigation does not pay the cold start
        (client, auth and TLS set-up cost 15 to 20 s on the first call of a process)."""
        from services.app.audit import MemoryAudit

        t0 = time.monotonic()
        done = set()
        for agent in ("investigator", "critic", "drafter"):
            if self.models[agent] in done:
                continue
            done.add(self.models[agent])
            ctx = RunContext(
                alert={"alert_id": "warmup"}, settings=self.settings, audit=MemoryAudit()
            )
            with contextlib.suppress(Exception):  # warm-up is best effort
                self._run(ctx, agent, "Reply with the word ok.", "ok")
        return time.monotonic() - t0

    # ---- agents ----

    def investigate(self, ctx, tools, brief, feedback):
        prompt = "Investigate this alert.\n" + json.dumps(brief)
        if feedback:
            prompt += (
                "\n\nThe Critic rejected some of your claims. Fix each using the prefetched "
                "results and the ids in them (or one more tool call if you really need it) or "
                "withdraw it. Return the COMPLETE JSON."
                "\nYour previous findings:\n"
                + json.dumps(feedback["previous"])
                + "\nRejections:\n"
                + json.dumps(feedback["rejections"])
            )
        instr = INVESTIGATOR.format(max_calls=self.settings.limits.max_tool_calls)
        try:
            text = self._run(ctx, "investigator", instr, prompt, tools=tools)
        except BudgetExceeded:
            text = ""
        if not text:
            return Findings(decision="uncertain", notes="no answer within the budget")
        try:
            return Findings.model_validate(parse_json(text))
        except ValueError as e:  # includes AgentOutputError and pydantic validation errors
            ctx.emit(
                "investigator", "error", f"unusable output ({type(e).__name__}): {text[-120:]!r}"
            )
            return Findings(
                decision="uncertain", notes="investigator output was cut off or invalid"
            )

    def critic_check(self, ctx, items):
        text = self._run(ctx, "critic", CRITIC, json.dumps({"claims": items}), json_mode=True)
        return parse_json(text).get("results", [])

    def draft(self, ctx, material):
        instr = DRAFTER.format(actions=", ".join(ACTIONS))
        text = self._run(ctx, "drafter", instr, json.dumps(material), json_mode=True)
        return parse_json(text)

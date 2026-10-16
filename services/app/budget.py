"""Per-run hard limits: tool calls, tokens per agent and a total token budget."""

from __future__ import annotations

import threading

from services.app.config import Limits


class BudgetExceeded(RuntimeError):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class RunBudget:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.tool_calls = 0
        self.tokens: dict[str, int] = {}
        self.exhausted: str | None = None
        self._lock = threading.Lock()

    def charge_tool(self) -> int:
        with self._lock:
            if self.tool_calls >= self.limits.max_tool_calls:
                self.exhausted = self.exhausted or "tool_calls"
                raise BudgetExceeded("tool_calls")
            self.tool_calls += 1
            return self.tool_calls

    def add_tokens(self, agent: str, n: int) -> None:
        with self._lock:
            self.tokens[agent] = self.tokens.get(agent, 0) + int(n or 0)
            if self.total_tokens > self.limits.total_tokens:
                self.exhausted = self.exhausted or "total_tokens"
            cap = self.limits.agent_tokens.get(agent)
            if cap is not None and self.tokens[agent] > cap:
                self.exhausted = self.exhausted or f"agent_tokens:{agent}"

    def check_tokens(self, agent: str | None = None) -> None:
        """Call before each LLM call; raises once the total or the agent's budget is spent."""
        if self.total_tokens >= self.limits.total_tokens:
            self.exhausted = self.exhausted or "total_tokens"
            raise BudgetExceeded("total_tokens")
        cap = self.limits.agent_tokens.get(agent or "")
        if cap is not None and self.tokens.get(agent, 0) >= cap:
            self.exhausted = self.exhausted or f"agent_tokens:{agent}"
            raise BudgetExceeded(f"agent_tokens:{agent}")

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())

    @property
    def status(self) -> str:
        return "budget_exhausted" if self.exhausted else "ok"

"""Typed data passed between agents and stored in the case report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["escalate", "clear", "uncertain"]


class Claim(BaseModel):
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    kind: str = "observation"  # observation | pattern | merge_proposal | disagreement


class Findings(BaseModel):
    decision: Decision
    pattern: str = "unclassified"  # wash_trading | coordinated_accumulation | mule_funding | ...
    agrees_with_hint: bool | None = None
    escalate_accounts: list[str] = Field(default_factory=list)
    cleared_accounts: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    merge_with: list[str] = Field(default_factory=list)  # ring ids proposed to merge
    notes: str = ""


class ClaimVerdict(BaseModel):
    claim_id: str
    accepted: bool
    stage: Literal["ledger", "llm", "both_ok"]
    reason: str = ""


class PolicyRef(BaseModel):
    clause_id: str
    title: str
    why: str = ""


class CaseReport(BaseModel):
    alert_id: str
    ring_id: str
    alert_status: str
    scenario_hint: str
    decision: Decision
    summary: str
    pattern: str
    hint_note: str  # the detector hint stated as a hypothesis, and whether the agent agrees
    evidence_table: list[dict]
    policy_references: list[PolicyRef]
    recommended_action: str
    confidence: float
    escalate_accounts: list[str]
    cleared_accounts: list[str]
    unsupported_claims: list[str] = Field(default_factory=list)
    merge_proposals: list[dict] = Field(default_factory=list)
    requires_analyst_approval: bool = True

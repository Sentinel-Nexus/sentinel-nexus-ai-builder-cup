"""Typed data passed between agents and stored in the case report."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["escalate", "clear", "uncertain"]
# Lane in the analyst workbench: where a case is shown. An analyst can override an agent clear.
LANES = {"escalate": "escalated", "clear": "agent_cleared", "uncertain": "needs_analyst"}


class Claim(BaseModel):
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    kind: str = "observation"  # observation | pattern | merge_proposal | disagreement | exclusion


class Findings(BaseModel):
    """What the Investigator contributes: the decision, rationale (claims) and evidence ids.

    Which accounts a decision covers is not the model's call: the case's members come from the
    detector alert and the ring subgraph (pipeline.py). The model may only propose exclusions,
    and an exclusion counts only when a supported claim of kind "exclusion" names the account."""

    decision: Decision
    pattern: str = "unclassified"  # wash_trading | coordinated_accumulation | mule_funding | ...
    agrees_with_hint: bool | None = None
    exclusions: list[str] = Field(default_factory=list)
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
    scenario_hint: str  # the detector's hypothesis, never stated as a fact
    decision: Decision
    lane: str  # escalated | agent_cleared | needs_analyst
    summary: str
    pattern: str
    hint_note: str  # the detector hint stated as a hypothesis, and whether the agent agrees
    evidence_table: list[dict]  # only claims whose evidence ids all resolve in this run's ledger
    policy_references: list[PolicyRef]
    recommended_action: str
    confidence: float
    # deterministic membership: alert members plus ring_subgraph members (evaded accounts linked
    # through devices or fills). escalate/cleared follow from the decision, not from the model.
    members_alert: list[str]
    members_linked: list[str] = Field(default_factory=list)  # in ring_subgraph, not in the alert
    member_basis: str = ""
    exclusions: list[dict] = Field(default_factory=list)  # {account_id, accepted, reason}
    escalate_accounts: list[str]
    cleared_accounts: list[str]
    clear_reason: str = ""  # why the agent cleared (decision == clear), for the analyst lane
    clear_evidence_ids: list[str] = Field(default_factory=list)
    # claims the Critic did not accept are never part of the report body: they are listed here
    removed_claims: list[dict] = Field(default_factory=list)  # {claim_id, text, reason}
    unverified_note: str = ""
    unsupported_claims: list[str] = Field(default_factory=list)  # same claims as flat text
    merge_proposals: list[dict] = Field(default_factory=list)
    requires_analyst_approval: bool = True
    analyst_override_allowed: bool = True

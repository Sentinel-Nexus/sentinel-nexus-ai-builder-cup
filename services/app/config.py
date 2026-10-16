"""Settings. Model IDs confirmed by real generateContent calls in us-central1 (2026-10-16)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
POLICY_DIR = REPO / "data" / "policies"

# Verified in us-central1: gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-pro.
# gemini-3-pro-preview returned 404 for this project.
MODEL_FAST = "gemini-2.5-flash"
MODEL_STRONG = "gemini-2.5-pro"


@dataclass(frozen=True)
class Limits:
    """Hard per-run limits. Hitting one ends the run as budget_exhausted, never silently."""

    max_tool_calls: int = 8
    total_tokens: int = 60_000
    # max_output_tokens per agent. Gemini 2.5 spends part of it on thinking, so these are generous
    # but still bounded.
    out_tokens: dict[str, int] = field(
        default_factory=lambda: {
            "investigator": 8192,
            "critic": 2048,
            "drafter": 3072,
        }
    )
    # token ceiling per agent over the whole run (all its LLM calls); total_tokens bounds the sum
    agent_tokens: dict[str, int] = field(
        default_factory=lambda: {
            "investigator": 40_000,
            "critic": 14_000,
            "drafter": 8_000,
        }
    )
    graph_budget_s: float = 15.0
    wall_clock_s: float = 120.0  # hard per-case timeout: the run ends as timed_out


@dataclass(frozen=True)
class Settings:
    project: str = field(
        default_factory=lambda: os.environ.get("PROJECT_ID", "project-f8544066-3df8-4f99-8ed")
    )
    location: str = field(default_factory=lambda: os.environ.get("GCP_REGION", "us-central1"))
    model_fast: str = field(default_factory=lambda: os.environ.get("MODEL_FAST", MODEL_FAST))
    model_strong: str = field(default_factory=lambda: os.environ.get("MODEL_STRONG", MODEL_STRONG))
    # The Critic runs on Flash unless MODEL_CRITIC says otherwise (see docs/agent-evals.md for the
    # Flash vs Pro measurement).
    model_critic: str = field(default_factory=lambda: os.environ.get("MODEL_CRITIC", MODEL_FAST))
    limits: Limits = field(default_factory=Limits)
    force_per_hour: int = 6  # rate limit for force=true re-investigations
    force_per_day: int = 30
    thinking_budget: int = 512  # caps Gemini 2.5 thinking tokens per call (Investigator)
    thinking_small: int = 256  # Critic and Drafter
    llm_concurrency: int = 2  # in-flight Gemini calls per process
    run_concurrency: int = 2  # investigations running at once; the rest wait

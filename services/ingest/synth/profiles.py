"""Robustness profiles: generator parameters for the extra evaluation seeds (eval_val.s<seed>_*)."""

from __future__ import annotations

import json
from pathlib import Path

from services.ingest.synth.generator import GenParams

PROFILES_FILE = Path(__file__).resolve().parents[3] / "evals" / "robustness_profiles.json"


def load_profiles(path: Path = PROFILES_FILE) -> dict[int, GenParams]:
    raw = json.loads(path.read_text(encoding="utf-8"))["profiles"]
    return {
        int(seed): GenParams(**{**p, "bg_trades": tuple(p["bg_trades"])}) for seed, p in raw.items()
    }

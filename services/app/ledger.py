"""The tool-call ledger: every evidence id a tool returned in this run, with the row behind it.

The Critic resolves a claim's evidence ids against this ledger. An id that no tool returned in
this run is rejected, however plausible it looks.
"""

from __future__ import annotations

import json
import threading
from typing import Any

# keys whose string value is an evidence id as it is
_ID_KEYS = {"id", "trade_id", "alert_id", "ring_id", "clause_id", "score_id"}
# keys holding a list of ready-made evidence ids
_ID_LIST_KEYS = {"evidence_trade_ids", "evidence_edge_ids", "edge_ids", "trade_ids", "clause_ids"}
# keys holding account ids -> Account:<id>
_ACCOUNT_KEYS = {"account_id", "members", "member_accounts", "escalate_candidates"}
_MAX_SNIPPET = 600


def harvest(tool: str, result: Any) -> dict[str, dict]:
    """Walk a tool result and return {evidence id: small row that backs it}."""
    found: dict[str, dict] = {}

    def add(eid: str, row: Any) -> None:
        if not isinstance(eid, str) or not eid or eid in found:
            return
        text = json.dumps(row, default=str) if not isinstance(row, str) else row
        found[eid] = {
            "tool": tool,
            "row": text[:_MAX_SNIPPET],
            # an id only listed by the detector has no content yet; a later tool row replaces it
            "placeholder": isinstance(row, dict) and "listed_in" in row,
        }

    def walk(node: Any, parent: dict | None) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _ID_KEYS and isinstance(value, str):
                    add(value, node)
                elif key in _ID_LIST_KEYS and isinstance(value, list):
                    for v in value:
                        add(v, {"listed_in": key, "of": parent and parent.get("alert_id")})
                elif key == "market_id" and isinstance(value, str):
                    add(f"Market:{value}", {"market_id": value})
                elif key in _ACCOUNT_KEYS:
                    for v in value if isinstance(value, list) else [value]:
                        if isinstance(v, str):
                            add(f"Account:{v}", node if key == "account_id" else {"member": v})
                walk(value, node)
        elif isinstance(node, list):
            for item in node:
                walk(item, parent)

    walk(result, None)
    return found


class Ledger:
    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def record(self, seq: int, tool: str, result: Any, extra: dict[str, dict] | None = None):
        ids = harvest(tool, result)
        ids.update(extra or {})
        with self._lock:
            for eid, entry in ids.items():
                old = self._entries.get(eid)
                if old is None or (old.get("placeholder") and not entry["placeholder"]):
                    self._entries[eid] = {**entry, "seq": seq}
        return sorted(ids)

    def has(self, evidence_id: str) -> bool:
        with self._lock:
            return evidence_id in self._entries

    def resolve(self, ids: list[str]) -> tuple[dict[str, dict], list[str]]:
        with self._lock:
            found = {i: self._entries[i] for i in ids if i in self._entries}
        return found, [i for i in ids if i not in found]

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

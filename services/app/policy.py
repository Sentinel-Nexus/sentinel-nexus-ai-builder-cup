"""Policy retrieval over the synthetic surveillance policy in data/policies/.

Vertex AI Search (Discovery Engine) is not enabled in this project, so retrieval is keyword
scoring (BM25-style) over clauses. ``RETRIEVAL`` is recorded on every result and case report.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from services.app.config import POLICY_DIR

RETRIEVAL = "keyword"
_CLAUSE = re.compile(r"^##\s+(SP-\d+\.\d+)\s+(.+)$", re.M)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "and", "or", "the", "of", "to", "in", "is", "are", "by", "for", "with", "not"}


@dataclass(frozen=True)
class Clause:
    clause_id: str
    title: str
    text: str


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def load_clauses(policy_dir: Path = POLICY_DIR) -> list[Clause]:
    clauses: list[Clause] = []
    for path in sorted(policy_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        marks = list(_CLAUSE.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            clauses.append(Clause(m.group(1), m.group(2).strip(), text[m.end() : end].strip()))
    return clauses


class PolicyIndex:
    def __init__(self, clauses: list[Clause] | None = None):
        self.clauses = clauses if clauses is not None else load_clauses()
        self._docs = [Counter(_tokens(f"{c.title} {c.title} {c.text}")) for c in self.clauses]
        df: Counter = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = max(len(self._docs), 1)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self._avg = sum(sum(d.values()) for d in self._docs) / n if self._docs else 1.0

    def search(self, query: str, k: int = 4) -> list[dict]:
        q = _tokens(query)
        scored = []
        for clause, doc in zip(self.clauses, self._docs, strict=True):
            norm = 1.2 * (0.25 + 0.75 * sum(doc.values()) / self._avg)
            s = sum(self._idf.get(w, 0.0) * doc[w] * 2.2 / (doc[w] + norm) for w in q if w in doc)
            if s > 0:
                scored.append((s, clause))
        scored.sort(key=lambda t: (-t[0], t[1].clause_id))
        return [
            {
                "clause_id": c.clause_id,
                "title": c.title,
                "text": c.text,
                "score": round(s, 3),
                "retrieval": RETRIEVAL,
            }
            for s, c in scored[:k]
        ]

    def get(self, clause_id: str) -> Clause | None:
        return next((c for c in self.clauses if c.clause_id == clause_id), None)

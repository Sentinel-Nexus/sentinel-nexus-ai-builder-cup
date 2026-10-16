"""The detector must not be keyed to ids, id patterns or the evaluation tables.

Scans detector SQL (sql/views) and code (services/detector). Evaluation scripts are the only
files allowed to read eval.ground_truth and eval.decoy_groups: ``services/detector/evaluate.py``
and ``sql/views/eval_*.sql``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.ingest.synth.generator import generate

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [ROOT / "sql" / "views", ROOT / "services" / "detector"]
SUFFIXES = {".sql", ".py"}

ID_COLUMNS = r"(?:account_id|device_id|funding_source_id|seller_id|buyer_id)"
FORBIDDEN_PATTERNS = {
    "id-prefix LIKE": re.compile(rf"{ID_COLUMNS}\s+(?:NOT\s+)?LIKE", re.I),
    "id-prefix STARTS_WITH": re.compile(rf"STARTS_WITH\s*\(\s*(?:\w+\.)?{ID_COLUMNS}", re.I),
    "id-prefix ENDS_WITH": re.compile(rf"ENDS_WITH\s*\(\s*(?:\w+\.)?{ID_COLUMNS}", re.I),
    "id regex": re.compile(rf"REGEXP_\w+\s*\(\s*(?:\w+\.)?{ID_COLUMNS}", re.I),
    "id substring": re.compile(rf"(?:SUBSTR|LEFT|RIGHT)\s*\(\s*(?:\w+\.)?{ID_COLUMNS}", re.I),
    "id range": re.compile(rf"{ID_COLUMNS}\s*(?:BETWEEN|>=|<=)\s*['\"]", re.I),
    "python startswith": re.compile(r"(?:account|device|funding)\w*\s*\.\s*startswith", re.I),
}
EVAL_TABLES = re.compile(r"ground_truth|decoy_groups", re.I)
# A quoted literal shaped like one of our ids: A0100, W003, DH1, DC_EV, FE_C004, FEX1, ...
ID_LITERAL = re.compile(r"""['"]((?:[A-Z]{1,3}_?)[A-Z]*\d{1,4}|[A-Z]{1,3}_[A-Z0-9]+)['"]""")


def _is_evaluation_script(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return rel == "services/detector/evaluate.py" or (
        rel.startswith("sql/views/eval_") and path.suffix == ".sql"
    )


def _files() -> list[Path]:
    return sorted(p for d in SCAN_DIRS if d.exists() for p in d.rglob("*") if p.suffix in SUFFIXES)


def _known_ids() -> set[str]:
    ids: set[str] = set()
    for seed in (42, 7):
        data = generate(seed)
        for a in data.accounts:
            ids |= {a["account_id"], a["device_id"], a["funding_source_id"]}
    return ids


def violations(path: Path, text: str, known: set[str]) -> list[str]:
    found = []
    for name, pattern in FORBIDDEN_PATTERNS.items():
        if pattern.search(text):
            found.append(name)
    if EVAL_TABLES.search(text) and not _is_evaluation_script(path):
        found.append("reads evaluation tables")
    for literal in ID_LITERAL.findall(text):
        found.append(f"id-like literal {literal!r}")
    for token in set(re.findall(r"[A-Za-z0-9_]+", text)) & known:
        found.append(f"known id {token!r}")
    return found


@pytest.fixture(scope="module")
def known_ids():
    return _known_ids()


def test_detector_files_have_no_id_keys_or_eval_table_reads(known_ids):
    bad = {}
    for path in _files():
        found = violations(path, path.read_text(encoding="utf-8"), known_ids)
        if found:
            bad[path.relative_to(ROOT).as_posix()] = found
    assert not bad, f"detector is keyed to ids or reads evaluation tables: {bad}"


@pytest.mark.parametrize(
    "snippet",
    [
        "SELECT * FROM t WHERE account_id LIKE 'W%'",
        "SELECT * FROM t WHERE STARTS_WITH(account_id, 'A01')",
        "SELECT * FROM t WHERE a.device_id IN ('DW1', 'DW2')",
        "SELECT * FROM t WHERE funding_source_id = 'FM1'",
        "SELECT * FROM t WHERE account_id BETWEEN 'A0100' AND 'A0105'",
        "SELECT * FROM `p.eval.ground_truth`",
        "JOIN `p.eval.decoy_groups` USING (account_id)",
        "if acct.account_id.startswith('M'):",
    ],
)
def test_the_scanner_catches_known_bad_patterns(snippet, known_ids):
    assert violations(ROOT / "sql" / "views" / "bad.sql", snippet, known_ids)


def test_evaluation_scripts_may_read_eval_tables(known_ids):
    path = ROOT / "services" / "detector" / "evaluate.py"
    assert not violations(path, "SELECT * FROM `p.eval.ground_truth`", known_ids)

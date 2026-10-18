"""Repository checks for the public release. Read-only; run in CI.

    python scripts/check_repo.py              # CI mode
    python scripts/check_repo.py --release    # also fail on unresolved placeholder tokens

Fails on:
  1. a tracked directory whose only file is .gitkeep (an empty placeholder folder)
  2. a Markdown link or image in README.md / docs / service READMEs that points to a missing file
  3. banned wording: bare "real-time"; Model Armor, a prompt-injection guard, PII redaction or
     Vertex AI Search described without a negation ("not", "no", "planned", ...) nearby
  4. a number in the README results sections that appears in no stored result
     (services/app/static/data/benchmark.json, docs/results/*.json, docs/benchmark.md,
     docs/agent-evals.md, docs/injected-rerun.md, docs/STATUS.md)
  5. with --release: any placeholder token such as VIDEO_URL_TODO, DECK_URL_TODO, TBD
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# Files whose wording and links are checked (CLAUDE.md is instructions, not a claim).
def doc_files() -> list[Path]:
    out = [ROOT / "README.md"]
    out += sorted((ROOT / "docs").rglob("*.md"))
    out += sorted((ROOT / "services").glob("*/README.md"))
    out += [ROOT / "infra" / "gcp" / "README.md", ROOT / "sql" / "views" / "README.md"]
    return [p for p in out if p.exists()]


def tracked() -> list[str]:
    res = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
    )
    return res.stdout.splitlines()


def check_gitkeep(files: list[str]) -> list[str]:
    by_dir: dict[str, list[str]] = {}
    for f in files:
        d, _, name = f.rpartition("/")
        by_dir.setdefault(d, []).append(name)
    return [
        f"{d or '.'}: only a .gitkeep (remove it, or add real content and a README)"
        for d, names in sorted(by_dir.items())
        if names == [".gitkeep"]
    ]


LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*```")


def check_links(paths: list[Path]) -> list[str]:
    errs = []
    for p in paths:
        in_fence = False
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE.match(line):
                in_fence = not in_fence
            if in_fence:
                continue
            for target in LINK.findall(line):
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                rel = target.split("#", 1)[0]
                if rel and not (p.parent / rel).resolve().exists():
                    errs.append(f"{p.relative_to(ROOT)}:{n}: link to missing file {target}")
    return errs


BARE_REALTIME = re.compile(r"(?<!near-)(?<!near )real[- ]time", re.I)
NEGATION = re.compile(
    r"\b(not|no|never|none|planned|cut|absent|without|nothing|isn't|aren't|neither|unbuilt)\b"
    r"|n't\b|keyword",
    re.I,
)
NOT_BUILT = re.compile(
    r"model armor|prompt[- ]injection guard|prompt guard|pii redaction|vertex ai search", re.I
)


def check_wording(paths: list[Path]) -> list[str]:
    errs = []
    for p in paths:
        lines = p.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            where = f"{p.relative_to(ROOT)}:{n}"
            if BARE_REALTIME.search(line):
                errs.append(f'{where}: bare "real-time" (say "near-real-time micro-batch")')
            if NOT_BUILT.search(line):
                window = " ".join(lines[max(0, n - 2) : n + 1])
                if not NEGATION.search(window):
                    errs.append(f"{where}: names something that is not built without saying so")
    return errs


NUM = re.compile(r"(?<![\w.-])\d[\d,]*(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\bNov \d+|\bOct \d+")


def numbers(text: str) -> set[str]:
    text = DATE.sub(" ", text)
    return {m.group(0).replace(",", "").rstrip(".") for m in NUM.finditer(text)}


def stored_numbers() -> set[str]:
    sources = [
        ROOT / "services/app/static/data/benchmark.json",
        ROOT / "services/app/static/data/stream.json",
        ROOT / "services/app/static/data/overview.json",
        ROOT / "docs/benchmark.md",
        ROOT / "docs/agent-evals.md",
        ROOT / "docs/injected-rerun.md",
        ROOT / "docs/STATUS.md",
        *sorted((ROOT / "docs/results").glob("*.json")),
    ]
    found: set[str] = set()
    for s in sources:
        if s.exists():
            found |= numbers(s.read_text(encoding="utf-8"))
    # percentages are stored as 71.0 in JSON and 71% in Markdown: accept both spellings
    return found | {n[:-2] for n in found if n.endswith(".0")}


SECTIONS = ("Results at a glance", "Streaming", "Governance and safety")


def section_text(readme: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\b.*?$(.*?)(?=^## |\Z)", readme, re.S | re.M)
    return m.group(1) if m else ""


def check_numbers() -> list[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    known = stored_numbers()
    errs = []
    for title in SECTIONS:
        body = section_text(readme, title)
        if not body:
            errs.append(f'README.md: section "## {title}" not found')
            continue
        body = re.sub(r"\(([^)]*\.(md|png|json|py))\)", "", body)  # link targets are not numbers
        body = re.sub(r"`[^`]*`", lambda m: m.group(0) if "." not in m.group(0) else "", body)
        for num in sorted(numbers(body)):
            if num not in known:
                errs.append(f'README.md "{title}": number {num} is in no stored result')
    return errs


ROWS = {
    "All scenarios": "all_scenarios",
    "Wash trading": "wash_trading",
    "Coordinated buying": "coordinated_accumulation",
    "Mule funding": "mule_funding",
}


def check_results_table() -> list[str]:
    """Each row of the README results table must equal benchmark.json (seed 42), digit for digit."""
    bench = json.loads((ROOT / "services/app/static/data/benchmark.json").read_text("utf-8"))
    cell = {(r["group"], r["method"].split()[0]): r for r in bench["seed42"]}  # Flat / Detector
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    errs = []
    for label, group in ROWS.items():
        m = re.search(rf"^\| {label} \| ([^|]+) \| ([^|]+) \|", readme, re.M)
        if not m:
            errs.append(f"README.md: results row '{label}' not found")
            continue
        for got, who in ((m.group(1), "Flat"), (m.group(2), "Detector")):
            r = cell[(group, who)]
            want = f"{r['precision']:.0f}% / {r['recall']:.0f}% / {r['f1']:.0f}%"
            if got.strip() != want:
                errs.append(f"README.md: row '{label}' {who}: has '{got.strip()}', stored '{want}'")
    return errs


PLACEHOLDER = re.compile(
    r"\b[A-Z][A-Z0-9_]*_TODO\b|\bTBD\b|\bTODO_[A-Z_]+\b|\bXXX\b|<[A-Z_]*URL[A-Z_]*>"
)
# the checklist names the tokens on purpose
PLACEHOLDER_EXEMPT = {"docs/submission-checklist.md"}


def check_placeholders(paths: list[Path]) -> list[str]:
    errs = []
    for p in paths:
        rel = p.relative_to(ROOT).as_posix()
        if rel in PLACEHOLDER_EXEMPT:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in PLACEHOLDER.finditer(line):
                errs.append(f"{rel}:{n}: unresolved placeholder {m.group(0)}")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--release", action="store_true", help="also fail on placeholder tokens")
    a = ap.parse_args(argv)
    paths = doc_files()
    results = {
        "no .gitkeep-only directories": check_gitkeep(tracked()),
        "Markdown links and images resolve": check_links(paths),
        "banned wording": check_wording(paths),
        "README numbers trace to stored results": check_numbers() + check_results_table(),
    }
    if a.release:
        results["no unresolved placeholders"] = check_placeholders(paths)
    failed = 0
    for name, errs in results.items():
        print(f"[{'FAIL' if errs else ' ok '}] {name}")
        for e in errs:
            print(f"       {e}")
        failed += bool(errs)
    print(
        f"{len(results) - failed}/{len(results)} checks passed"
        + (" (release mode)" if a.release else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Read-only guard: only a single SELECT (or WITH ... SELECT) or GRAPH query may run."""

from __future__ import annotations

import re

ALLOWED_FIRST = {"SELECT", "WITH", "GRAPH"}
FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT",
    "REVOKE", "CALL", "EXECUTE", "EXPORT", "LOAD", "DECLARE", "SET", "BEGIN", "COMMIT",
    "ROLLBACK", "ASSERT", "UNDROP", "COPY", "CLONE", "RAISE",
}  # fmt: skip


class UnsafeQueryError(ValueError):
    """The statement is not a plain read."""


_STRING = re.compile(
    r"""'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`[^`]*`""", re.S
)
_COMMENT = re.compile(r"--[^\n]*|#[^\n]*|/\*.*?\*/", re.S)


def _strip(sql: str) -> str:
    """Remove comments and quoted text (strings, identifiers) so only keywords remain."""
    sql = _STRING.sub("''", sql)
    return _COMMENT.sub(" ", sql)


def assert_read_only(sql: str) -> None:
    if not isinstance(sql, str) or not sql.strip():
        raise UnsafeQueryError("empty query")
    body = _strip(sql).strip()
    if "/*" in body or "*/" in body:
        raise UnsafeQueryError("unterminated comment")
    statements = [s for s in body.split(";") if s.strip()]
    if len(statements) != 1:
        raise UnsafeQueryError("exactly one statement is allowed")
    words = re.findall(r"[A-Za-z_]+", statements[0].upper())
    if not words or words[0] not in ALLOWED_FIRST:
        raise UnsafeQueryError(f"only SELECT and GRAPH queries are allowed, got {words[:1]}")
    bad = sorted(set(words) & FORBIDDEN)
    if bad:
        raise UnsafeQueryError(f"forbidden keyword(s): {', '.join(bad)}")

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import run_sql

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def test_render_substitutes_all_placeholders():
    out = run_sql.render("SELECT * FROM `__P__.a.b` JOIN `__P__.c.d`", "proj-1")
    assert "__P__" not in out
    assert out.count("proj-1") == 2


@pytest.mark.parametrize("bad", ["", "a b", "a`b", "a;b"])
def test_render_rejects_bad_project(bad):
    with pytest.raises(ValueError):
        run_sql.render("SELECT 1", bad)


def test_default_is_dry_run():
    client = MagicMock()
    run_sql.run("SELECT 1", "p", apply=False, client=client)
    config = client.query.call_args.kwargs["job_config"]
    assert config.dry_run is True
    client.query.return_value.result.assert_not_called()


def test_apply_executes():
    client = MagicMock()
    out = run_sql.run("SELECT 1", "p", apply=True, client=client)
    assert client.query.call_args.kwargs["job_config"].dry_run is False
    client.query.return_value.result.assert_called_once()
    assert out["applied"] is True


def test_all_repo_sql_files_use_placeholder_not_project_id():
    files = list(SQL_DIR.rglob("*.sql"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "project-f8544066" not in text, f
        assert "__P__" in text, f

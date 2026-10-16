"""The deploy script is a dry run by default and stages only what the image needs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "deploy_app", Path(__file__).resolve().parents[2] / "infra" / "deploy_app.py"
)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def test_dry_run_runs_nothing(capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a dry run must not run commands")

    monkeypatch.setattr(deploy.subprocess, "run", boom)
    assert deploy.main(["--tag", "t1"]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "--min-instances 1" in out and "--max-instances 3" in out
    assert "--allow-unauthenticated" in out and "sa-agent@" in out and "1Gi" in out


def test_stage_has_the_service_and_no_secrets_or_caches(tmp_path):
    n = deploy.stage(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert n == len(files)
    assert {"Dockerfile", "services/app/main.py", "services/graph/service.py"} <= files
    assert "services/app/static/index.html" in files
    assert "data/policies/surveillance_policy.md" in files
    assert not any(f.startswith((".cache", ".env", "evals", "docs")) for f in files)
    assert not any("__pycache__" in f for f in files)

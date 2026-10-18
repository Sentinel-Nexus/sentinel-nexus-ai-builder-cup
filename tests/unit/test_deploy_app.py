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
    assert (
        "DRY-RUN" in out
        and "--min-instances 1" in out
        and "--max-instances 1" in out
        and "--startup-probe" in out
        and "/health" in out
    )
    assert (
        "LIVE_PER_CLIENT_HOUR=3" in out and "LIVE_PER_HOUR=20" in out and "LIVE_PER_DAY=120" in out
    )
    assert "--allow-unauthenticated" in out and "sa-agent@" in out and "1Gi" in out


def test_stage_has_the_service_and_no_secrets_or_caches(tmp_path):
    n = deploy.stage(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert n == len(files)
    assert {"Dockerfile", "services/app/main.py", "services/graph/service.py"} <= files
    assert "services/app/static/index.html" in files and "data/injected_alerts.json" in files
    assert "data/policies/surveillance_policy.md" in files
    assert not any(f.startswith((".cache", ".env", "evals", "docs")) for f in files)
    assert not any("__pycache__" in f for f in files)


def test_teardown_is_a_dry_run_by_default_and_stops_idle_billing(capsys, monkeypatch):
    monkeypatch.setattr(
        deploy.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert deploy.main(["--teardown"]) == 0
    out = capsys.readouterr().out
    assert (
        "--min-instances 0" in out
        and "--cpu-throttling" in out
        and "--no-cpu-throttling" not in out
    )


def test_stage_includes_what_streaming_needs(tmp_path):
    deploy.stage(tmp_path)
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert {
        "services/stream/service.py",
        "services/detector/scoring.py",
        "services/detector/threshold.json",
        "sql/views/det_40_features.sql",
        "scripts/run_sql.py",
        "scripts/__init__.py",
        "data/stream/burst_01.jsonl",
    } <= files
    assert not any(f.startswith("evals") or "_truth" in f for f in files)  # labels stay out


def test_candidate_gets_no_traffic_and_promote_is_a_separate_dry_run(capsys, monkeypatch):
    monkeypatch.setattr(
        deploy.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )
    assert deploy.main(["--no-traffic"]) == 0
    out = capsys.readouterr().out
    assert "--no-traffic --tag candidate" in out and "STREAMING_ENABLED=true" in out
    assert "STREAM_PER_CLIENT_10MIN=1" in out and "STREAM_PER_DAY=30" in out
    assert deploy.main(["--promote"]) == 0
    out = capsys.readouterr().out
    assert "--to-tags candidate=100" in out and "--to-revisions sentinel-app-00009-lgx=100" in out
    assert deploy.main(["--streaming", "off"]) == 0
    assert "STREAMING_ENABLED=false" in capsys.readouterr().out

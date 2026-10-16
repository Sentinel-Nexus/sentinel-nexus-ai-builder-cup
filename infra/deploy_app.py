"""Build and deploy the workbench service `sentinel-app` to Cloud Run.

    python infra/deploy_app.py              # dry run: prints the plan and the commands
    python infra/deploy_app.py --apply      # build the image, deploy, print the URL
    python infra/deploy_app.py --apply --teardown         # Nov 6: stop idle billing

Image: us-central1-docker.pkg.dev/<project>/sentinel/sentinel-app:<tag> (built by Cloud Build from a
staged context: only services/app, services/graph, data/policies). Runs as sa-agent, public,
max 1 instance (caches and live limits are per instance), 1Gi, min 1 instance with CPU always
allocated for the judging window (`--teardown` on Nov 6 sets min 0 and request-only CPU), startup
probe on /health. It deletes
nothing and changes no IAM except what `--allow-unauthenticated` does for this one service.
Every command has a timeout. Build logs are not streamed (the operator may lack log-bucket
viewer rights): the script polls the build status instead.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROJECT = os.environ.get("PROJECT_ID", "project-f8544066-3df8-4f99-8ed")
REGION = os.environ.get("GCP_REGION", "us-central1")
SERVICE = "sentinel-app"
SA = f"sa-agent@{PROJECT}.iam.gserviceaccount.com"
BUILD_TIMEOUT_S = 540
DEPLOY_TIMEOUT_S = 540
# /health answers 503 until the service is warm (about 60 s after it starts listening), so no
# traffic reaches an instance that is up but not ready. 5 s * 48 = up to 240 s to become ready.
STARTUP_PROBE = (
    "httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=5,"
    "periodSeconds=5,timeoutSeconds=3,failureThreshold=48"
)


def stage(dest: Path) -> int:
    """Copy exactly what the image needs into ``dest``; returns the file count."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copy(REPO / "infra" / "app" / "Dockerfile", dest / "Dockerfile")
    (dest / "services").mkdir()
    shutil.copy(REPO / "services" / "__init__.py", dest / "services" / "__init__.py")
    for rel in ("services/app", "services/graph", "data/policies"):
        shutil.copytree(REPO / rel, dest / rel, ignore=ignore)
    shutil.copy(REPO / "data" / "injected_alerts.json", dest / "data" / "injected_alerts.json")
    return sum(1 for p in dest.rglob("*") if p.is_file())


def run(cmd: list[str], timeout: int, apply: bool) -> int:
    print("+", " ".join(cmd))
    if not apply:
        return 0
    try:
        return subprocess.run(cmd, timeout=timeout, check=False, shell=(os.name == "nt")).returncode
    except subprocess.TimeoutExpired:
        print(f"timed out after {timeout}s: {' '.join(cmd[:4])}")
        return 124


def build(context: str, image: str, apply: bool) -> int:
    """Submit asynchronously and poll: the operator cannot stream Cloud Build logs."""
    cmd = ["gcloud", "builds", "submit", context, "--tag", image, "--project", PROJECT,
           "--region", REGION, "--timeout", f"{BUILD_TIMEOUT_S - 30}s", "--async",
           "--format", "value(id)", "--quiet"]  # fmt: skip
    print("+", " ".join(cmd))
    if not apply:
        return 0
    shell = os.name == "nt"
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=shell)
    except subprocess.TimeoutExpired:
        print("build submit timed out")
        return 124
    build_id = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    if out.returncode or not build_id:
        print(out.stderr[-600:])
        return out.returncode or 1
    print(f"build {build_id} started (console: cloud-build/builds;region={REGION}/{build_id})")
    deadline = time.monotonic() + BUILD_TIMEOUT_S
    while time.monotonic() < deadline:
        st = subprocess.run(
            ["gcloud", "builds", "describe", build_id, "--project", PROJECT, "--region", REGION,
             "--format", "value(status)"],
            capture_output=True, text=True, timeout=60, shell=shell,
        ).stdout.strip()  # fmt: skip
        print(f"  build status: {st or 'unknown'}")
        if st == "SUCCESS":
            return 0
        if st in ("FAILURE", "TIMEOUT", "CANCELLED", "INTERNAL_ERROR", "EXPIRED"):
            return 1
        time.sleep(10)
    print("gave up waiting for the build (the build itself may still finish)")
    return 124


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="really build and deploy")
    ap.add_argument("--min-instances", type=int, default=1)
    ap.add_argument(
        "--max-instances",
        type=int,
        default=1,
        help="1 for the judging window: caches and live limits are per instance",
    )
    ap.add_argument("--live-per-client-hour", type=int, default=3)
    ap.add_argument("--live-per-hour", type=int, default=20)
    ap.add_argument("--live-per-day", type=int, default=120)
    ap.add_argument(
        "--teardown",
        action="store_true",
        help="stop idle billing: min instances 0 and CPU only during requests (no build)",
    )
    ap.add_argument("--tag", default=dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--skip-build", action="store_true", help="redeploy an existing --tag")
    a = ap.parse_args(argv)
    if a.teardown:
        print(f"mode: {'APPLY' if a.apply else 'DRY-RUN (nothing is changed)'} (teardown)")
        return run(
            ["gcloud", "run", "services", "update", SERVICE, "--project", PROJECT,
             "--region", REGION, "--min-instances", "0", "--cpu-throttling", "--quiet"],
            DEPLOY_TIMEOUT_S, a.apply,
        )  # fmt: skip
    image = f"{REGION}-docker.pkg.dev/{PROJECT}/sentinel/{SERVICE}:{a.tag}"
    print(f"mode: {'APPLY' if a.apply else 'DRY-RUN (nothing is built or changed)'}")
    print(f"project {PROJECT}, region {REGION}, service {SERVICE}, image {image}")
    print(f"min instances {a.min_instances}, max {a.max_instances}, CPU always allocated")
    print(
        f"live limits: {a.live_per_client_hour}/client/h, {a.live_per_hour}/h, "
        f"{a.live_per_day}/day (per instance)"
    )

    rc = 0
    if not a.skip_build:
        with tempfile.TemporaryDirectory(prefix="sentinel-app-ctx-") as tmp:
            n = stage(Path(tmp))
            print(f"staged build context: {n} files")
            rc = build(tmp, image, a.apply)
        if rc:
            print("build failed: not deploying")
            return rc
    env = (
        f"PROJECT_ID={PROJECT},GCP_REGION={REGION},LIVE_PER_CLIENT_HOUR={a.live_per_client_hour},"
        f"LIVE_PER_HOUR={a.live_per_hour},LIVE_PER_DAY={a.live_per_day}"
    )
    rc = run(
        ["gcloud", "run", "deploy", SERVICE, "--image", image, "--project", PROJECT,
         "--region", REGION, "--service-account", SA, "--allow-unauthenticated",
         "--min-instances", str(a.min_instances), "--max-instances", str(a.max_instances),
         "--memory", "1Gi", "--cpu", "1", "--concurrency", "40", "--timeout", "300",
         "--no-cpu-throttling", "--cpu-boost", "--startup-probe", STARTUP_PROBE,
         "--set-env-vars", env, "--quiet"],
        DEPLOY_TIMEOUT_S, a.apply,
    )  # fmt: skip
    if rc == 0 and a.apply:
        run(["gcloud", "run", "services", "describe", SERVICE, "--project", PROJECT,
             "--region", REGION, "--format", "value(status.url,status.latestReadyRevisionName)"],
            60, True)  # fmt: skip
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

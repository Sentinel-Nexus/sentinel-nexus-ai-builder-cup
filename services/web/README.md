# services/web

The `sentinel-hello` Cloud Run service: a Flask health check that returns
`{"status": "ok", "service": "marketsentinel"}`, deployed from Cloud Shell with
`gcloud run deploy --source` (buildpacks via `Procfile`, no Dockerfile).

This is **not** the analyst workbench. The workbench is `services/app` (service `sentinel-app`).
`services/web` is kept because `infra/gcp/setup.sh` (step 7) deploys it, `scripts/verify_sync.py`
checks the live body against it and `tests/unit/test_web.py` tests it.

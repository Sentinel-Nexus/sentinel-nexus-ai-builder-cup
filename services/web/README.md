# services/web

Starting point imported from the live `sentinel-hello` Cloud Run service (deployed from
Cloud Shell with `gcloud run deploy --source`). Flask app served by gunicorn via `Procfile`
(buildpacks, no Dockerfile). P5 turns this into the analyst workbench.

Deploy: see `infra/gcp/setup.sh` (last step).

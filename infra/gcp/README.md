# infra/gcp

> **The live project was built manually in Cloud Shell.** `setup.sh`, `sql/` and the
> generator are the reproducible record of that state, not the thing that created it.
> Live-vs-repo drift is checked by `scripts/verify_sync.py` and tracked in
> `docs/STATUS.md` ("Known drift"). Snapshot of what exists: [inventory.md](inventory.md).

## What `setup.sh` covers

APIs, BigQuery datasets and empty tables, service accounts `sa-agent` and `sa-ingest` with
their project roles, Pub/Sub `trades` / `trades-dlq` with subscriptions and the dead-letter
policy, Artifact Registry `sentinel`, the `gql-poc` reservation and its project-wide
assignment, and the `sentinel-hello` Cloud Run deploy.

It is idempotent (read-only existence check, then create only if missing) and **dry-run by
default**.

```
bash infra/gcp/setup.sh            # prints what it would do
bash infra/gcp/setup.sh --apply    # creates what is missing
```

Run it with the personal gcloud profile as a project owner. On Windows use Git Bash; set
`CLOUDSDK_PYTHON` to the repo `.venv` python if `bq` complains that `python3.12` is missing.

## Rebuild order

1. `bash infra/gcp/setup.sh --apply` (APIs, datasets, empty tables, IAM, Pub/Sub, registry,
   reservation, Cloud Run).
2. Load data: `python -m services.ingest.synth --seed 42 --apply` (see ADR 0001; seed 42
   is what the live tables hold).
3. `python scripts/run_sql.py sql/schema/harden.sql --apply` (hard negatives, partial links).
4. `python scripts/run_sql.py sql/graph/market_graph.sql --apply` (graph tables + `market_graph`).
5. `python scripts/verify_sync.py` should exit 0.

`scripts/run_sql.py` renders `__P__` and is dry-run unless `--apply` is given.

## Cost warning

The reservation `gql-poc` is assigned to the whole project, so every BigQuery query uses
autoscaled slots (max 50). Do not loop queries; teardown is in `docs/STATUS.md`.

## Not reproduced here

See "Known drift" in `docs/STATUS.md` (hand-granted human IAM roles, the auto-created
`cloud-run-source-deploy` repo and source bucket, tables written by the Cloud Shell
`detect.py`).

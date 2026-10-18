# CI and the Workload Identity deploy (documented, not applied)

## What runs today

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`: `ruff check .`,
`pytest -q` and `python scripts/check_repo.py`. It has read-only repository permission and no cloud
credentials. It has not run on GitHub yet at the time of writing; the same three commands pass locally.

## What does not exist: a tag-based deploy

Deploys are manual: `python infra/deploy_app.py --apply` run by an operator who is logged in with
`gcloud`. The roadmap's "tag-based deploy via Workload Identity Federation" is **planned, not built and not
applied**. Nothing below has been run; every command is a proposal for the project owner.

## Proposed setup (project owner, once)

Placeholders: `PROJECT`, `PROJECT_NUMBER`, `REPO=Sentinel-Nexus/sentinel-nexus-ai-builder-cup`.

```bash
gcloud iam workload-identity-pools create github --project PROJECT --location global \
  --display-name "GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc github-oidc --project PROJECT \
  --location global --workload-identity-pool github \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition "assertion.repository == 'REPO'"

# a dedicated deployer identity; do not reuse sa-agent (the runtime identity)
gcloud iam service-accounts create sa-deployer --project PROJECT
# roles it needs: roles/run.admin, roles/cloudbuild.builds.editor,
# roles/artifactregistry.writer, and roles/iam.serviceAccountUser on sa-agent only

gcloud iam service-accounts add-iam-policy-binding sa-deployer@PROJECT.iam.gserviceaccount.com \
  --project PROJECT --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/REPO"
```

## Proposed workflow (`.github/workflows/deploy.yml`, not committed)

```yaml
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc
          service_account: sa-deployer@PROJECT.iam.gserviceaccount.com
      - uses: google-github-actions/setup-gcloud@v2
      - run: python infra/deploy_app.py --apply --no-traffic   # candidate first; promote by hand
```

Keep `--no-traffic`: a human checks the tagged candidate URL and then runs `--promote`. The pool is bound to
one repository by the attribute condition, so no other repository can impersonate `sa-deployer`.
Before this is applied, `infra/deploy_app.py` needs a dry-run review and the `sa-deployer` role list needs
the owner's sign-off (the Cloud Run admin role is broad).

# Submission checklist

Status as of 2026-10-18. "Done" means checked in this repository or on the live URL on that date.

| Deliverable | Status | Where / what is left |
|---|---|---|
| Repository link | Done (branch `chore/release` pushed; merge to `main` by PR) | https://github.com/Sentinel-Nexus/sentinel-nexus-ai-builder-cup |
| Live link | Done, HTTP 200 on 2026-10-18 | https://sentinel-app-578558579917.us-central1.run.app (revision `sentinel-app-00013-tek`) |
| Demo video | Not made | Script: `docs/demo-script.md`. Replace the video token in `README.md` |
| Slide deck | Not made | Outline: `docs/pitch-outline.md`. Replace the deck token in `README.md` |
| Documentation | Done | `README.md`, `docs/setup.md`, `docs/architecture.md`, `docs/repo-map.md`, `docs/benchmark.md`, `docs/agent-evals.md` |
| License | MIT added; confirm the contest rules allow it | `LICENSE` |
| CI | Workflow added; has not run on GitHub yet | `.github/workflows/ci.yml` |
| Tag `v1.0.0-submission` | Not created | After merge: `git tag v1.0.0-submission && git push origin v1.0.0-submission` |
| Release check | Run before submitting | `python scripts/check_repo.py --release` must pass once both placeholders in `README.md` are replaced |

## Before submitting

1. Replace the two placeholder tokens in `README.md` (VIDEO and DECK links), then run `python scripts/check_repo.py --release`.
2. Open the PR from `chore/release`, wait for CI, merge.
3. Decide what to do about the personal email in the first commit (see the hygiene report in the release summary).

## Teardown after Nov 6 (not run; run only after the owner confirms)

Stop idle billing and the streaming consumer first. One command sets `STREAMING_ENABLED=false`, min instances 0 and
CPU throttling back on:

```bash
gcloud run services update sentinel-app --project project-f8544066-3df8-4f99-8ed --region us-central1 \
  --min-instances 0 --cpu-throttling --update-env-vars STREAMING_ENABLED=false --quiet
```

`python infra/deploy_app.py --apply --teardown` sets min instances 0 and CPU throttling only; it does not change
`STREAMING_ENABLED`, so use the command above. Verify:

```bash
gcloud run services describe sentinel-app --project project-f8544066-3df8-4f99-8ed --region us-central1 \
  --format "value(spec.template.metadata.annotations)"
```

Then the rest, from `docs/STATUS.md` ("Teardown after Nov 6" and the P6 additions): delete the reservation assignment and
`gql-poc`; delete the Cloud Run services; delete the Pub/Sub topics, subscriptions and the dead-letter queue; delete the
four stream tables and remove the two Pub/Sub grants for `sa-agent`; delete BigQuery datasets if desired (`eval_val` and
the `eval.*_pre_p2` backups first); delete the Artifact Registry repo `sentinel` if desired. STATUS.md says each of these
needs a script with `--apply` under `infra/` first; only `--teardown` in `infra/deploy_app.py` exists today.

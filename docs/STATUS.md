# STATUS

Live state of GCP and the repo. Update in the same commit as any cloud change.

Last updated: 2026-10-14

## Live GCP resources

To be filled by phase 1 (P1 gcp-sync).

Project `project-f8544066-3df8-4f99-8ed`, region `us-central1`.

## Repo state

- Branch `chore/claude-setup`: Claude Code settings, CLAUDE.md, STATUS and ROADMAP.
- Scaffold, docs and scenario generator are merged to `main`.
- `feat/bq-schema-graph` exists locally with no commits beyond `main`; its relation to
  live GCP is unverified until P1.

## Known risks

- Reservation `gql-poc` is assigned to the whole project: every query consumes slots and cost.
- Live GCP state may contain console-only changes not yet captured in the repo (P1 resolves this).
- Gemini model ID in `us-central1` is unconfirmed; verify with a real call before use (P4).
- Tight schedule: submission Oct 18.

## Teardown after Nov 6

Run only after the human confirms. Each step needs a script under `infra/` with `--apply`.

1. Delete the reservation assignment, then reservation `gql-poc`.
2. Delete Cloud Run services.
3. Delete Pub/Sub topics, subscriptions and the DLQ.
4. Delete BigQuery datasets if desired.
5. Delete Artifact Registry repo `sentinel` if desired.

# Contributing

1. Branch from `main`: `git switch -c feat/short-description`. Never commit to `main` directly.
2. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
   `test:`, `refactor:`, `chore:`.
3. Before committing, run `ruff check . && pytest`. Pre-commit runs ruff and gitleaks.
4. Never commit secrets, keys or `.env`. Add new variables to `.env.example` with no values.
5. Open a PR into `main`. Keep PRs focused.
6. Cap tokens and tool calls for every agent run.
7. Only work built during the hackathon belongs in this repo.

# Contributing

Thanks for contributing to `Pigbibi` repositories.

## Workflow

1. Branch from `origin/main` using a short prefix such as `feat/`, `fix/`,
   `docs/`, `refactor/`, `test/`, or `chore/`.
2. Keep pull requests focused and verify changes locally before pushing.
3. For documentation and repository hygiene checks here, run:

```sh
python3 scripts/check_hygiene.py --strict
```

## Pull Request Standards

- Use English Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `test`,
  `chore`, `perf`, `ci`) with commit headers at most 72 characters.
- Fill in every section of `.github/pull_request_template.md`.
- Never commit `.local/`, `ops-notes/`, `docs/plans/`, `docs/audits/`,
  `docs/changes/`, `docs/acceptance-policy.md`, or real credentials.

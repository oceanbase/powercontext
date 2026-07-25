# Repository Guidelines

## Project Structure & Module Organization

`powercontext` is a Python package built with `uv` and Hatchling. Runtime code lives in `src/powercontext/`; keep public package exports in `src/powercontext/__init__.py` and place new modules beside related code. Tests live in `tests/` and should mirror the package behavior they validate; keep end-to-end scenarios in `tests/e2e/`. Documentation is split by locale under `docs/en/` and `docs/zh/`, with RFC material in each locale's `rfcs/` directory. Site configuration is in `zensical.toml`; generated output in `site/` is not source material.

## Build, Test, and Development Commands

- `make install`: run `uv sync` and install `prek` hooks.
- `make check`: verify the lock file, run all pre-commit hooks, and run `ty check`.
- `make test`: run `pytest` with doctest support.
- `tox`: run tests and type checks across Python 3.11, 3.12, 3.13, and 3.14.
- `make build`: clean `dist/` and build the wheel.
- `make docs-test`: build documentation strictly.
- `make docs`: serve documentation locally with Zensical.

## Coding Style & Naming Conventions

Target Python 3.11+. Use PEP 8 naming: modules and functions in `snake_case`, classes in `PascalCase`, constants in `UPPER_SNAKE_CASE`. Ruff enforces formatting and linting with a 120-character line length; run `uv run prek run -a` before committing if you do not use the installed hooks. Keep comments focused on non-obvious intent rather than restating code.

## Testing Guidelines

Use `pytest`; name test files `test_*.py` and test functions `test_*`. Tests must protect observable behavior or a
specific regression:

- Put focused behavior tests in `tests/` and cross-component acceptance scenarios in `tests/e2e/`.
- Add a regression test when fixing a defect that is likely to recur.
- Do not mirror import graphs, module ownership, dependency lists, private call order, or call counts that do not
  express an external budget or idempotency guarantee.
- A refactor or rewrite that preserves behavior should not require tests to be rewritten.
- Do not test a straightforward script solely to increase coverage; test it only when it has meaningful external
  behavior or a known regression.

Include doctests when public examples are useful. For changes that affect supported Python versions, prefer `tox`
before opening a PR. Tests may use plain `assert`; Ruff allows `S101` under `tests/`.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style subjects, such as `feat: init powercontext`, `docs: init zensical i18n`, and `chore(github): add more templates for PRs and issues`. Keep the subject concise and scoped.

Pull requests should link the relevant issue or RFC, explain the rationale, summarize behavior/API/docs/test changes, call out user-facing or breaking changes, list validation commands, and include the AI usage statement requested by `.github/pull_request_template.md`.

## Security & Configuration Tips

Do not commit secrets, local virtual environments, caches, or generated build artifacts. Keep dependency changes reflected in `uv.lock`, and use `make check` to catch lock drift before review.

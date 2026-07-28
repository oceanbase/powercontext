# Repository Guidelines

## Project Structure & Module Organization

`powercontext` is a Python package built with `uv` and Hatchling. Runtime code lives in `src/powercontext/`; keep public package exports in `src/powercontext/__init__.py` and place new modules beside related code. Tests live in `tests/`; organize focused tests around observable behavior and keep cross-component acceptance scenarios in `tests/e2e/`. Documentation is split by locale under `docs/en/` and `docs/zh/`, with RFC material in each locale's `rfcs/` directory. Site configuration is in `zensical.toml`; generated output in `site/` is not source material.

## Build, Test, and Development Commands

- `make install`: run `uv sync` and install `prek` hooks.
- `make skills-install`: install the recommended agent skills pinned in `skills-lock.json`; requires `npx` and a Codex restart.
- `make check`: verify the lock file, run all pre-commit hooks, and run `ty check`.
- `make test`: run `pytest` with doctest support.
- `tox`: run tests and type checks across Python 3.11, 3.12, 3.13, and 3.14.
- `make build`: clean `dist/` and build the wheel.
- `make docs-test`: build documentation strictly.
- `make docs`: serve documentation locally with Zensical.

## Agent Skills

Use an available skill only when it matches the current task, and read its `SKILL.md` before applying it. Recommended
skills are pinned in `skills-lock.json`; contributors can install them before a new Codex session with
`make skills-install`.

## Coding Style & Naming Conventions

Target Python 3.11+. Use PEP 8 naming: modules and functions in `snake_case`, classes in `PascalCase`, constants in `UPPER_SNAKE_CASE`. Ruff enforces formatting and linting with a 120-character line length; run `uv run prek run -a` before committing if you do not use the installed hooks. Keep comments focused on non-obvious intent rather than restating code.

## Generated API Contract

`openapi/powercontext.yaml` is the source of truth for the HTTP API contract. The Python sources in
`src/powercontext/http/_generated/` are checked in; do not edit them by hand. After changing the contract, run
`make api-generate` and `make contract-test`.

## Testing Guidelines

Use `pytest`; name test files `test_*.py` and test functions `test_*`. Tests must protect observable behavior or a
specific regression; complexity alone is not a reason to add one:

- Put focused behavior tests in `tests/` and assert through public interfaces.
- Put cross-component acceptance scenarios in `tests/e2e/`; they should remain valid across internal rewrites.
- Add a regression test when fixing a defect that is likely to recur. Reproduce the failure and preserve the
  externally observable behavior that was previously wrong.
- Do not freeze implementation details such as import graphs, module ownership, dependency lists, buffer sizes,
  private call order, or call counts unless they express an external budget or idempotency guarantee.
- Do not exhaustively mock and test internal errors that an abstraction deliberately normalizes. Cover an internal
  error separately only when it produces distinct observable behavior.
- Do not test a straightforward script solely to increase coverage. If its correctness is evident from reading it
  end to end and it has no meaningful external behavior or known regression, extra tests add noise rather than
  confidence.

Include doctests when public examples are useful. For changes that affect supported Python versions, prefer `tox`
before opening a PR. Tests may use plain `assert`; Ruff allows `S101` under `tests/`.

## Deliverables

Write deliverables as self-contained, final-state artifacts. Incorporate requested feedback directly into the
artifact. Do not refer to drafts, versions, review rounds, prior wording, superseded decisions, or the editing
process unless the user explicitly requests a changelog, history, or decision record.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style subjects, such as `feat: init powercontext`, `docs: init zensical i18n`, and `chore(github): add more templates for PRs and issues`. Keep the subject concise and scoped.

Pull requests should link the relevant issue or RFC, explain the rationale, summarize behavior/API/docs/test changes, call out user-facing or breaking changes, list validation commands, and include the AI usage statement requested by `.github/pull_request_template.md`.

## Security & Configuration Tips

Do not commit secrets, local virtual environments, caches, `.powercontext/`, `dist/`, or `site/`. Keep dependency changes reflected in `uv.lock`, and use `make check` to catch lock drift before review.

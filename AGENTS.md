# Repository Guidelines

## Project Structure & Module Organization

`powercontext` is a Python package built with `uv` and Hatchling. Runtime code lives in `src/powercontext/`; keep public package exports in `src/powercontext/__init__.py` and place new modules beside related code. Tests live in `tests/`; organize focused tests around observable behavior and keep cross-component acceptance scenarios in `tests/e2e/`. Documentation is split by locale under `docs/en/` and `docs/zh/`, with RFC material in each locale's `rfcs/` directory. The Fumadocs site lives in `website/`; its generated `content/docs/`, `.generated/`, and `out/` directories are not source material. `gateway/` and `desktop/` are separate Cargo workspaces that the Python build never touches; see [Rust Gateway](#rust-gateway) below.

## Build, Test, and Development Commands

- `make install`: run `uv sync` and install `prek` hooks.
- `make skills-install`: install the recommended agent skills pinned in `skills-lock.json`; requires `npx` and a Codex restart.
- `make check`: verify the lock file, run all pre-commit hooks, and run `ty check`.
- `make test`: run `pytest` with doctest support.
- `tox`: run tests and type checks across Python 3.11, 3.12, 3.13, and 3.14.
- `make build`: clean `dist/` and build the wheel.
- `make docs-test`: lint and build the static website, including API references.
- `make docs`: serve the Fumadocs website locally.
- `make gateway-tck`: run the pinned upstream protocol compatibility suites against the real gateway binary; requires Node, Go, Python 3.11, and network access.

## Rust Gateway

`gateway/` is an independent Cargo workspace of seven crates behind its own manifest. Every command needs `--manifest-path gateway/Cargo.toml`; `gateway/Cargo.lock` is checked in, so add `--locked` to every build, test, and lint invocation. CI pins the toolchain to 1.95.0, and the workspace is edition 2021.

```bash
cargo fmt --manifest-path gateway/Cargo.toml --all --check
cargo clippy --locked --manifest-path gateway/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path gateway/Cargo.toml --workspace
```

`gateway/README.md` is the authoritative description of the per-request pipeline, the crate layout, the environment contract, and the test boundaries. Read it before changing ingress, transport, or the two-phase gate.

The gateway owns the model traffic boundary and nothing else. These invariants are not implementation preferences:

- **No policy in the gateway.** Core decides admission, privacy, and capacity; the gateway executes a verdict and never infers one. HTTP success from an upstream call is not approval, and an unknown or unparseable verdict never becomes allow.
- **Two-phase gating with bound identities.** `check(before_send)` gates the bytes that leave and `check(before_release)` gates what the client sees; the release call must carry the binding the send phase returned. A block or withhold sends zero bytes, and a refuse releases nothing and cancels upstream.
- **Retries are deterministic-failure-only.** Connection-refused-class failures retry under a single owner. An indeterminate outcome after hand-off (timeout, truncated response) is surfaced as indeterminate and never re-sent, because a re-send may be billed twice.
- **Injected context is byte-exact.** Prepared context is injected verbatim and its exact byte range declared; changing one byte invalidates Core's reuse binding. Injection stays tail-only so the client-owned history prefix remains byte-stable for upstream prefix caching.
- **Credentials are server-side only.** They come from environment variables, are never read from request fields, and never enter a check payload. `PC_GATEWAY_API_KEY` protects only the two model routes and does not replace Core authentication.
- **Evidence is body-free.** Never add request or response bodies, prompts, model output, vectors, credentials, or socket paths to evidence records or tracing.

A panic on a request path is a defect: a malformed upstream or Core response must produce a handled error, never a crash. Prefer `?`, or an `expect` whose message states the invariant. Return upstream status codes and `Content-Type` unchanged; masking a provider error as 200 is a correctness failure.

Scripted Core verdicts and fixture provider responses verify local handling only. They do not establish real-Core, real-provider, or host acceptance, so state which of these a change was actually exercised against.

## Agent Skills

Use an available skill only when it matches the current task, and read its `SKILL.md` before applying it. Recommended skills are pinned in `skills-lock.json`; contributors can install them before a new Codex session with `make skills-install`.

## Coding Style & Naming Conventions

Target Python 3.11+. Use PEP 8 naming: modules and functions in `snake_case`, classes in `PascalCase`, constants in `UPPER_SNAKE_CASE`. Ruff enforces formatting and linting with a 120-character line length; run `uv run prek run -a` before committing if you do not use the installed hooks. Keep comments focused on non-obvious intent rather than restating code.

Rust formatting is plain `rustfmt` with no project overrides, and clippy must pass with `-D warnings`. Every Rust source file carries the Apache-2.0 header that `.licenserc.yaml` checks. Keep module boundaries aligned with the crate responsibilities in `gateway/README.md` instead of reaching across crates.

## Observability Changes

Before adding or changing operational logs, metrics, or spans, read the developer guide:
[English](docs/en/development/observability.md) / [中文](docs/zh/development/observability.md).
The operational field contract is documented in [Logs, metrics, and tracing](docs/en/docs/operate/observability.md).

- Use the owning boundary's stable operation, unit, outcome, and error vocabulary. Emit operational logs through `log_safely`; logging failure must not change business results or suppress cancellation.
- Reuse bound request/trace/worker identity. Do not invent request IDs for scheduled work or put execution identities in metric labels.
- Register new structured fields in the appropriate allowlist, document their meaning and units in both locales, and verify their JSON/console output. Arbitrary extras are not a supported output contract.
- Exclude content, queries, prompts, model responses, vectors, credentials, and sensitive paths. Review exception values before including a traceback; do not dump provider or database errors indiscriminately.
- Keep logging configuration owned by the Server or embedding application. Preserve unconfigured SDK behavior; verify process-boundary claims with real spawned workers.

## Design and Implementation

- Start from the current contract and relevant RFC. Change public APIs only as required by the task and avoid unrelated refactors. Do not weaken behavior tests to accommodate an implementation shortcut.
- Keep domain rules in the runtime or Artifact service that owns them. Transport and host adapters should translate their inputs and outputs without inventing different domain behavior.
- Extract helpers for cohesive responsibilities, such as an invariant, algorithm, protocol encoding, or state transition. Share code when callers have the same contract and failure semantics. Similar syntax or a few repeated lines is not enough; avoid forwarding helpers and intermediate types that only scatter an operation across files.
- Keep operation-specific validation, request construction, and error handling near their owning boundary. Do not add unrelated branches to a shared helper just to reuse it.
- Make identity and state transitions explicit. Keep retries, persisted state, and recovery consistent with the promised idempotency and delivery behavior. A successful write must remain usable through its supported read path.
- Choose compatibility deliberately. Unsupported formats may be rejected clearly before acceptance or writes; supported formats must work through serialization, persistence, readback, and reuse. Define the handling of existing records and version conflicts. Keep documentation and compatibility claims consistent with that choice.
- Keep authoritative records distinct from derived projections. Rebuilding derived state must preserve exact identities and historical evidence rather than silently assigning today's interpretation to old data.
- Account for work and storage costs at the expected scale. Identify whole-collection scans or rewrites on incremental operations, and measure their impact before claiming a performance improvement or acceptable cost.

Use [REVIEW.md](REVIEW.md) when reviewing changes.

## Generated API Contract

`openapi/powercontext.yaml` is the source of truth for the HTTP API contract. The Python sources in `src/powercontext/http/_generated/` are checked in; do not edit them by hand. After changing the contract, run `make api-generate` and `make contract-test`.

## Generated Integration Helpers

`src/powercontext_integration_lifecycle/` is the source of the helpers that copy-installed Python plugins ship. The helpers may import only the standard library and must run on Python 3.9, because Claude Code runs its hooks with the `python3` on `PATH`. `integrations/lifecycle/` is the source of the TypeScript helpers that the DSH, OpenClaw, OpenCode, and Pi plugins ship. They may import only Node.js built-in modules, because the plugins do not share their dependencies. Their copies under `integrations/` are checked in; do not edit them by hand. After changing the source, run `make integration-lifecycle`; `make check` rejects a stale copy.

## Testing Guidelines

Use `pytest`; name test files `test_*.py` and test functions `test_*`. Tests must protect observable behavior or a specific regression; complexity alone is not a reason to add one:

- Put focused behavior tests in `tests/` and assert through public interfaces.
- Put cross-component acceptance scenarios in `tests/e2e/`; they should remain valid across internal rewrites.
- Add a regression test when fixing a defect that is likely to recur. Reproduce the failure and preserve the externally observable behavior that was previously wrong.
- Do not freeze implementation details such as import graphs, module ownership, dependency lists, buffer sizes, private call order, or call counts unless they express an external budget or idempotency guarantee.
- Do not exhaustively mock and test internal errors that an abstraction deliberately normalizes. Cover an internal error separately only when it produces distinct observable behavior.
- Do not test a straightforward script solely to increase coverage. If its correctness is evident from reading it end to end and it has no meaningful external behavior or known regression, extra tests add noise rather than confidence.

Include doctests when public examples are useful. For changes that affect supported Python versions, prefer `tox` before opening a PR. Tests may use plain `assert`; Ruff allows `S101` under `tests/`.

Run focused checks first and expand based on affected behavior. For claims about an external backend or host, distinguish actual execution from mocks, emulators, and direct hook invocation. Simulated inputs verify local handling; they do not establish that a real service produces those inputs or that a host supports the complete workflow.

## Blog Publishing and i18n

Keep blog changes scoped to the blog. Preserve the existing appearance, navigation,
content, and behavior of non-blog pages. Load blog styles and scripts only on blog
pages, and keep blog publishing instructions under `docs/{en,zh}/blog/`.

When the user supplies a blog post as Markdown, prepare both `docs/zh/blog/posts/` and
`docs/en/blog/posts/` versions. Translate the supplied language faithfully; preserve code,
commands, links, and technical meaning. The user need not supply a translation or complete
front matter. Derive the summary and stable slug from the article, reuse known author profiles,
and ask only for missing facts such as a new author's identity or photo; never invent them.

Keep counterpart posts at matching relative paths with identical slugs, publication/update
dates, author IDs, draft status, and pin status. Localize titles, summaries, body text,
category names, and author biographies; share author photos and article assets. Apply edits
to both languages together. Check blog i18n and pagination with
`uv run pytest tests/e2e/test_blog_site.py -q` and run `make docs-test` before delivery.
Preparing content does not by itself authorize a production deployment.

## Deliverables

Write page metadata and content as literal UTF-8 text. Use Chinese characters directly instead of Unicode escape sequences. Preserve non-ASCII text when serializing documentation metadata to JSON.

Write deliverables as self-contained, final-state artifacts. Incorporate requested feedback directly into the artifact. Do not refer to drafts, versions, review rounds, prior wording, superseded decisions, or the editing process unless the user explicitly requests a changelog, history, or decision record.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style subjects, such as `feat: init powercontext`, `feat(website): establish new site style and structure`, and `chore(github): add more templates for PRs and issues`. Keep the subject concise and scoped.

Pull requests should link the relevant issue or RFC, explain the rationale, summarize behavior/API/docs/test changes, call out user-facing or breaking changes, list validation commands, and include the AI usage statement requested by `.github/pull_request_template.md`.

## Security & Configuration Tips

Do not commit secrets, local virtual environments, caches, `.powercontext/`, `dist/`, or generated website output. Keep dependency changes reflected in `uv.lock`, and use `make check` to catch lock drift before review.

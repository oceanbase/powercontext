# Contributing to `powercontext`

Contributions are welcome, and they are greatly appreciated!
Every little bit helps, and credit will always be given.

You can contribute in many ways:

# Types of Contributions

## Report Bugs

Report bugs at https://github.com/oceanbase/powercontext/issues

If you are reporting a bug, please include:

- Your operating system name and version.
- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

## Fix Bugs

Look through the GitHub issues for bugs.
Anything tagged with "bug" and "help wanted" is open to whoever wants to implement a fix for it.

## Implement Features

Look through the GitHub issues for features.
Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

Dashboard contributors should follow the [implementation and verification baseline](docs/en/development/dashboard.md) ([中文](docs/zh/development/dashboard.md)).

For release preparation, use the [version update and release guide](docs/en/development/releasing.md)
([中文](docs/zh/development/releasing.md)). It documents the Makefile commands and the versions managed independently.

## Write Documentation

powercontext could always use more documentation, whether as part of the official docs, in docstrings, or even on the web in blog posts, articles, and such.

This is a public repository, and merged content becomes a permanent public record. Documentation, RFCs, examples, code comments, and PR/issue descriptions must all be readable and meaningful to external contributors:

- Do not name external customers, users, partners, or their internal projects. Describe the requirement in neutral terms instead, such as "a deployment that needs manual topic writes". Do not include such a name in one locale while omitting it from the other.
- Do not link to Ant Group / Alibaba internal documents or any resource that requires corporate SSO, in particular links to `yuque.antfin.com/...`. Other intranet domains, private document IDs, and internal chat or ticket links follow the same rule.
- Reference sources everyone can open: a public GitHub issue, PR, RFC, or upstream public documentation. If a requirement is not public, summarize the relevant part in the document instead of linking to it.
- Keep `docs/en/` and `docs/zh/` in sync, including headings, field labels, and link text. Apply every redaction or wording change to both locales.

The link rule is enforced automatically: `make check` runs `scripts/check_public_content.py` through prek and rejects URLs whose hostname matches the fixed suffix list in `INTERNAL_HOST_SUFFIXES` (`antfin.com`, `alibaba-inc.com`, `alipay-inc.com`, `aliyun-inc.com`). Run `uv run python scripts/check_public_content.py --all` to check every tracked file directly. The checker does not recognise other intranet domains, private document IDs, internal ticket links, or SSO-only spaces on public hosts; those, and all external names, still need a manual read of both locales before submitting.

RFC-specific rules (allowed header fields, reference handling, and a pre-submit check) are in the [RFC guidelines](docs/en/rfcs/README.md) ([中文](docs/zh/rfcs/README.md)).

## Submit Feedback

The best way to send feedback is to file an issue at https://github.com/oceanbase/powercontext/issues.

If you are proposing a new feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.
- Remember that this is a volunteer-driven project, and that contributions
  are welcome :)

# Get Started!

Ready to contribute? Here's how to set up `powercontext` for local development.
Please note this documentation assumes you already have `uv` and `Git` installed and ready to go.

1. Fork the `powercontext` repo on GitHub.

2. Clone your fork locally:

```bash
cd <directory_in_which_repo_should_be_created>
git clone git@github.com:YOUR_NAME/powercontext.git
```

3. Navigate into the repository:

```bash
cd powercontext
```

Then install the development environment and Git hooks:

```bash
make install
```

Recommended Codex skills are optional and are not required to build or test the project. If you have `npx`
available, install the skills pinned in `skills-lock.json` before starting a new Codex session:

```bash
make skills-install
```

4. Create a branch for local development:

```bash
git checkout -b name-of-your-bugfix-or-feature
```

Now you can make your changes locally.

5. Run the checks that match the change:

```bash
make check
make unit-test
```

Use `make e2e-test` for cross-component behavior, `make contract-test` for OpenAPI changes, and
`make docs-test` for documentation changes. `make test` runs the complete pytest suite. These pytest targets
spread tests across one worker per CPU with pytest-xdist; pass `PYTEST_WORKERS=0` to run them in a single process,
for example when debugging with `pdb`.

Before raising a pull request you should also run tox when a change may affect supported Python versions.
This requires you to have the relevant Python versions installed. The same version matrix runs in CI.

6. Commit your changes and push your branch to GitHub:

```bash
git add .
git commit -m "Your detailed description of your changes."
git push origin name-of-your-bugfix-or-feature
```

7. Submit a pull request through the GitHub website.

# Pull Request Guidelines

## OceanBase migration acceptance

The E2E harness runs Scope search migrations, background listener lifecycle, clean
Atomic Memory migrations, managed startup consistency, and long-query acceptance
on three isolated OceanBase runners. Every group collects and validates its full
test collection before selecting zero-based shard `INDEX/COUNT`. Cases within each shard still run
serially in separate pytest processes; skips and failures fail the shard.

With the migration OceanBase URL configured as in `.github/workflows/e2e-harness.yml`,
reproduce the clean migration part of one shard with:

```bash
uv run --locked python scripts/run_atomic_memory_clean_migration.py \
  oceanbase --shard 0/3 --output .powercontext/migration-shard-0/clean-migration
```

Omit `--shard` to run the complete collection. Use `--tests` to select the other
groups' files from the workflow, or `--collect-only` to inspect the full collection
without running cases. Every invocation writes `summary.json`; only successful
execution, including a legitimately empty shard, marks the report complete.

The aggregate check retains the name `Atomic Memory clean migration (OceanBase)`.
It requires all three runners to succeed and verifies that their reports cover
every collected node exactly once in each of the five groups. To verify downloaded
artifacts locally, keep each shard artifact in a separate subdirectory and run:

```bash
uv run --locked python scripts/check_atomic_memory_migration_shards.py \
  --reports .powercontext/downloaded-migration-shards --shards 3
```

Collection-only or incomplete reports cannot satisfy the aggregate check. Include
links and measured before/after job and workflow times in migration CI PRs, and
report runner queue time separately from execution time.

## Submission checklist

Before you submit a pull request, check that it meets these guidelines:

1. Add behavior tests for new externally observable behavior. Add a regression test when a defect is likely to
   recur. Changes that provide neither do not need tests solely for coverage.

2. If the pull request adds functionality, the docs should be updated.
   Put your new functionality into a function with a docstring, and add the feature to the list in `README.md`.

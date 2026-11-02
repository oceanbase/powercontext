---
title: Preparing a release
description: Update the release version through Make, review independently versioned components, and verify release artifacts.
---

# Preparing a release

Use the repository Makefile to update the prepared PowerContext release version and its installation instructions.
The actual Python distribution version still comes from Git through Hatch VCS. Preparing a version does not create
a tag, publish packages, or create a GitHub Release.

The commands below use `1.2.0` as an example. These examples are not current-release declarations and do not need
updating for each release.

## Update and check the prepared version

From the repository root, on the release preparation branch:

```bash
make version-bump VERSION=1.2.0
make version-check
git diff --stat
git diff
```

`version-bump` updates the managed files and regenerates Python API metadata from the OpenAPI contract.
It also accepts prerelease versions such as `1.3.0a1`, `1.3.0b1`, and `1.3.0rc1`. Managed installation instructions
name the selected version without describing it as a stable release.
`version-check` checks consistency against `info.version` in `openapi/powercontext.yaml` without changing files.
To check a specific release target, supply its package version or tag spelling:

```bash
make version-check VERSION=1.2.0
make version-check VERSION=powercontext-v1.2.0
```

The tag spelling selects the expected version; it does not prove that the tag exists or that the current checkout
builds that version. Review the diff before committing. Generated Python files must be regenerated, not edited by hand.

For a stable release, prepend a bilingual entry to `website/src/lib/releases.ts` with the actual changes, release date,
installation command, and GitHub Release URL. Preserve all existing entries, then run:

```bash
make release-check VERSION=1.2.0
```

`release-check` runs `version-check`, installs the website dependencies, and verifies that the first website entry
matches the target version, contains English and Chinese notes, and uses matching installation and release links.
It requires the repository's Node/pnpm environment. Alpha, beta, and RC versions skip the website-entry check because
the website lists stable releases only. `make docs-test` also runs this check as part of the website tests.

## Version inventory

### Automatically synchronized

| Source | Managed value |
| --- | --- |
| `openapi/powercontext.yaml` | `info.version`, the prepared release version. |
| `release/upgrade-support.json` | Target release version; source versions and the support policy require compatibility review. |
| `src/powercontext/http/_generated/` | `API_VERSION` in `operations.py` and `info.version` in `schema.py`, regenerated through `make api-generate`. |
| `README.md`, `README_CN.md`, `README_JP.md` | Current release prose, pinned package installation commands, and matching Agent integration tag. |
| `docs/en/docs/get-started/quickstart.md`, `docs/zh/docs/get-started/quickstart.md` | Current release and matching installation/tag instructions. |
| `docs/en/docs/get-started/manual-start.md`, `docs/zh/docs/get-started/manual-start.md` | CLI/Server and Python Client installation pins, and the matching Agent integration tag. |
| `docs/en/docs/get-started/install-and-run.md`, `docs/zh/docs/get-started/install-and-run.md` | Page description, current release prose, package/tag pins, upgrade target, and Python Client installation. |
| `integrations/dsh/plugins/powercontext/README.md` | Server installation pin and the matching `setup dsh --ref` tag. |

The installation guides also contain historical migration versions. Those references are preserved. When adding
another current-release installation example, include it in the version tooling's managed paths and patterns so
the next release updates and checks it.

### Derived from another source

| Value | Source and handling |
| --- | --- |
| Python wheel/sdist version and installed `powercontext --version` | `pyproject.toml` declares `dynamic = ["version"]`; Hatch VCS reads Git metadata. Use the intended release tag for the final build. |
| Runtime package metadata | Reads the installed Python distribution version; there is no separate static runtime version to bump. |
| Desktop API bindings | Regenerate OpenAPI types and contract digests with `pnpm --dir desktop generate`, then run `pnpm --dir desktop generate:check`. The Desktop application has its own package version. |
| Dify tool contract | Regenerate with `uv run python integrations/dify/generate_contract.py`, then run the same command with `--check` to verify its API version and schemas. |
| Docker and Bub harness build version | Build inputs pass `POWERCONTEXT_VERSION` from the selected package/Git version. Do not add a fixed release number to Dockerfiles. |
| Website documentation and API references | Generated from documentation and API sources. Do not edit `website/content/docs/`, `website/.generated/`, or `website/out/`. |
| Dependency lockfiles | Regenerate with the owning package manager when dependency or independently versioned package metadata changes. A main release bump alone does not require replacing versions in locks. |

The OpenAPI version describes the prepared API release; it does not override Hatch VCS. Before tagging, a development
checkout can therefore have a development package version while the contract is already prepared for the next release.

### Independently versioned components

Review these when their own behavior or distribution changes. `make version-bump` does not assign them the main
PowerContext release number.

| Component | Version locations and review scope |
| --- | --- |
| Claude Code plugin | `integrations/claude-code/plugins/powercontext/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` must agree when publishing a plugin update. Review contract tests that assert the manifest version. |
| Codex plugin | `integrations/codex/plugins/powercontext/.codex-plugin/plugin.json` and its sibling `pyproject.toml` describe the plugin and its runtime package. Review both and refresh the plugin's `uv.lock` for a plugin release. `plugin_version.py` derives the hook and scope-binding User-Agent version from the manifest. |
| Portable Agent Plugin | `integrations/agent-plugin/powercontext/plugin.json`. Its package version is separate from the Agent Plugins schema version. |
| Hermes plugins | `integrations/hermes/plugins/{powercontext,powercontext-command}/plugin.yaml`. |
| MiniMax and ZCode plugins | `integrations/minimax/plugins/powercontext/.minimax-plugin/plugin.json` and `integrations/zcode/plugins/powercontext/.zcode-plugin/plugin.json`. |
| JavaScript plugins | `integrations/{dsh,opencode,pi}/plugins/powercontext/package.json` and `integrations/openclaw/plugins/memory-powercontext/package.json`. The OpenClaw discovery manifest has no separate version field. |
| WorkBuddy integration | Its hooks and transport scripts carry User-Agent strings; there is no plugin manifest version to align with the Server. |
| Skill Receiver | `RECEIVER_VERSION` in `src/powercontext/client/skill_receiver.py` supplies the default receiver identity version reported during enrollment, reconciliation, and receipts. It is independent of the Server version. |
| Python integrations | `integrations/{bub,langchain,langgraph,opendal,pydantic-ai}/pyproject.toml` contain separate distribution versions. Dependency constraints express compatibility, not the current main release. |
| Evaluation and harness packages | `evaluation/pyproject.toml`, `evaluation/coding/swebench_pro/web/package.json`, and `e2e/bub/pyproject.toml` have their own package versions. |
| Protocols and dependencies | Agent Plugins schema versions, persisted format versions, OpenAPI specification version, API paths, host minimum versions, and third-party dependencies change only with their own contracts. |

Installing an Agent integration from the matching `powercontext-v…` repository tag selects the matching source
revision. It does not require every plugin manifest to have the same numeric version as the Server.

For an independent plugin release, also audit version-bearing User-Agent strings in Python hooks/scripts and
JavaScript `src/errors.ts`, their contract tests, generated bundles, and associated lockfiles. These values can drift
from manifests; the main release version check does not validate them. DSH's `pack:release` builds before stamping
the package manifest, and the stamping script only changes the manifest version. Passing a new version to that
packaging command does not update the transport version already compiled into the bundle.

### Historical records and test examples

Preserve existing entries in `website/src/lib/releases.ts`, including their dates, installation commands, and GitHub
URLs. For a stable release, add a separate entry with the actual user-facing changes and upgrade notes. The website
lists stable releases; alpha, beta, and RC notes belong on GitHub Releases. Version tooling does not invent release notes.

Also preserve migration introduction versions, older-version compatibility documentation, RFC baselines, archived
release assets, and test fixtures that exercise older or arbitrary versions. For example, references to the tag-table
migration introduced in `1.1.0` and the pre-`1.0.0` processing migration remain historical facts. A fixture containing
`1.0.0` is not automatically a stale current-release declaration.

The DSH README's statement that a capability was included in release `1.1.0` is also historical; only its current
installation pins are synchronized.

## Verify before publication

### Database upgrade support

`release/upgrade-support.json` declares the target version, source-version selection policy, backends, and
configurations. `automatic_sources` keeps 1.3.0 as its fixed baseline; explicit `cases` are empty.
The catalog declares twelve cases: one source × three backends × vectors enabled/disabled × Code enabled/disabled.
Normal publication requires the ten automatic cases to pass. The two embedded seekDB configurations with Code enabled
are temporarily manual-only because of native DDL waits; they remain unverified and are excluded from supported
upgrade declarations. Passing the automatic gate does not establish complete twelve-case acceptance.
The target is always the actual wheel built by CI. Future target releases keep testing 1.3.0;
intervening releases are not added automatically. The registered 1.4.0 bridge remains implemented but is outside
CI upgrade acceptance, so a passing gate does not certify its upgrade path. Source wheel URLs and SHA-256s are resolved
from public PyPI and recorded with the target artifact.

The source-specific chains are `s1300 → s1400 → s1401 → s1402` for 1.3.0 and
`r1400 → s1400 → s1401 → s1402` for 1.4.0. They share the target contract while preserving their frozen execution
history and journal lineage for subsequent registered upgrades. Application versions do not identify schema
checkpoints. In particular, 1.4.0 already has current Candidate envelopes: its bridge preserves those records and
creates empty retained tables for the common target shape, without inventing old Candidate history. Adding other
sources requires exact captures, a registered bridge and actual acceptance; the two registered paths do not certify
all releases between them or the target.
The current `upgrade-required` policy mandates positive acceptance on all three backends while keeping upgrade
support unverified. Every profile points to `tests/release_upgrades/test_full_server.py`; missing migration support
must fail. Neither a fresh-install-only declaration nor correct rejection of an old database satisfies this gate.
After the full migration implementation and acceptance pass, review the declaration, set `supported-upgrades`, and
regenerate the installation notices before publication. Both modes run the same mandatory acceptance matrix.
The suite contract and evidence requirements are in `tests/release_upgrades/README.md` in the repository.

`make check` and `make release-check` validate this policy and the bilingual installation notices. Changes to
managed persistence inputs require a compatibility review with an updated digest and rationale. A digest is a
review trigger, not evidence that a migration is correct. Managed text hashes normalize CRLF to LF so checkout
line endings do not require a new review; distribution hashes always cover the exact archive bytes. `make version-bump` also updates the policy target;
regenerate the support notices with `uv run python scripts/upgrade_release_gate.py --write-docs` after matrix changes.

Build exactly one wheel and its matching source distribution into `dist/`, then execute the declared upgrade cases:

```bash
make upgrade-release-test
uv run python scripts/run_upgrade_release_gate.py --release --release-notes /path/to/release-notes.md
```

The publication input directory must contain exactly one wheel and one matching `.tar.gz` source distribution;
leftover distributions and unexpected files fail validation (`uv`'s `.gitignore` is not an artifact). The runner checks
source metadata and builds a wheel from the source archive, comparing all member contents except the derived
`RECORD` listing. A stale source archive with the same version therefore cannot bypass validation. Archive timestamps
and compression do not affect this comparison. A build-tool change that alters wheel contents requires rebuilding
the complete pair, not ignoring the mismatch.

The runner tests a private snapshot, records exact artifact SHA-256s in `publication.json`, and exports only passing
files to the evidence directory's `validated-dist/`. CI uploads that set. `make publish` uses `--release --publish` to
upload those exact paths in the same invocation; subsequent changes to the original `dist/` cannot join the upload.
No packages are uploaded by validation commands without `--publish`.

The release check requires the actual wheel version to match the policy and the release notes to include the exact
English support declaration from the installation guide. It installs each exact source release and the target wheel
in separate environments. Missing tests, skipped backends, failures, and missing evidence block the check. Static
policy validation alone does not establish an upgrade path. Ordinary PR builds may have a development
version; the `--release` check must run against the final tagged wheel without overriding its version.

The **Upgrade release gate** CI job requires successful public migration commands, full Server readiness, target
startup, preserved records and new writes on real 1.3.0 databases in all ten required automatic cases. The two
manual seekDB Code cases do not block publication or establish supported upgrades. Limited bundle verification with
`server_ready=false` fails. Every backend runs even if another backend fails, with separate logs and results.
seekDB runs embedded in a temporary runner directory. PR and publication jobs start a disposable OceanBase CE
container, wait for its MySQL tenant, archive diagnostics, and remove it even on failure. No external database
credentials are needed. Unavailable backends fail instead of being skipped. Historical rejection regressions are
not part of the positive acceptance gate. Administrators must make this job required in branch protection.
By default, the release workflow gates distribution upload, PyPI publication, and release assets on the acceptance result and
archives its evidence. It runs after `release.published`, so it cannot prevent the earlier manual publication of
the GitHub Release page: run the command above before that action. `make publish` requires
`UPGRADE_RELEASE_NOTES=/path/to/release-notes.md` and runs the same gate before uploading packages.

### Manual publication without upgrade acceptance

The **Release** workflow has a `workflow_dispatch` input named `skip_upgrade_acceptance`, defaulting to `false`.
Enable it only to publish for new databases without claiming that existing databases can be upgraded. This does
not change `release/upgrade-support.json`, certify a migration path, or relax the default release and PR gates.
The target policy must remain `upgrade-required` when using this option.

For an existing published Release such as `powercontext-v1.4.1`:

1. Add this exact English notice to its GitHub Release body:

   ```text
   Upgrade acceptance was skipped for this release. Use only new databases; upgrades of existing databases are not supported.
   ```

2. In **Actions → Release → Run workflow**, select the repository's default branch (`master`), set
   `release_tag` to `powercontext-v1.4.1`, and enable `skip_upgrade_acceptance`. The workflow definition must
   already be merged into the default branch. Select the tag through the input, not the workflow branch selector.

   ```bash
   gh workflow run release.yml --repo oceanbase/powercontext --ref master \
     -f release_tag=powercontext-v1.4.1 -f skip_upgrade_acceptance=true
   ```

The workflow resolves the existing tag to a commit and builds that commit; it does not move the tag or package
the current default branch. It reads current Release notes rather than reusing the failed run's event payload.
Version checks, synchronized release references, policy consistency, package metadata checks, an sdist rebuild
comparison, clean installation, dependency checks, and the new-database Server/MCP/Memory smoke test remain
mandatory. Only then can the exact checked distributions reach PyPI and GitHub Release assets. Post-publication
verification and website deployment remain enabled, using the selected release target.

The `release-without-upgrades` artifact records the tag, source commit, actor, run, distribution hashes, and
`upgrade_acceptance: skipped`; the run summary also states that existing-database upgrades are unsupported.
Leaving the option disabled runs the normal automatic upgrade matrix and support-declaration checks.

### General release validation

Run the checks for the complete release changes, including generated contracts and documentation:

```bash
make release-check VERSION=1.2.0
make contract-test
make check
make test
make docs-test
make build
uvx --from twine twine check dist/*
```

Use the supported Node/pnpm environment for website checks. Run integration-specific checks for independently
changed plugins or adapters. Test compatibility and data migrations required by the actual release changes; a
version consistency check does not establish backward compatibility.

On a clean checkout of the intended release tag, verify the actual package version before building final artifacts:

```bash
uvx --from hatchling --with hatch-vcs hatchling version
```

Its output must equal the package version represented by the tag. Do not use a pretend-version override to establish
that the tag produces the correct version. Install the built distribution in an isolated environment and verify
the CLI, Server, MCP, and SQLite behavior using `scripts/ci_release_smoke.py --version <package-version>`.

The repository's `.github/workflows/release.yml` runs when a GitHub Release is **published**. Before building and
publishing to PyPI, it runs `release-check` and checks the tag against Hatch VCS. It then builds and checks
distributions, publishes to PyPI, attaches artifacts, and invokes release verification.
Pushing a tag alone does not trigger that workflow. Creating the tag and publishing the GitHub Release are separate
release actions after preparation and review.

After the release tag exists, review the availability declarations in `integrations/capabilities.toml`. A `released`
entry must name an existing `release_tag` that contains the declared implementation and evidence; it is separate
from the plugin's own version. Keep experimental support experimental unless its support contract has changed.
Fetch the referenced tags in local and CI checkouts before running integration manifest checks, regenerate the
bilingual capability matrix with `make integration-manifest-docs`, and update related integration overview and
evaluation guidance. Do not create or move a release tag just to make an availability check pass.

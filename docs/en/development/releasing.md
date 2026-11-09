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
| `docs/{en,zh}/docs/operate/database-migrations.md`, `tests/release_upgrades/README.md` | Current target release only; frozen schema revision names and their original software versions remain unchanged. |

The installation guides also contain historical migration versions. Those references are preserved. When adding
another current-release installation example, include it in the version tooling's managed paths and patterns so
the next release updates and checks it.

### Derived from another source

| Value | Source and handling |
| --- | --- |
| Python wheel/sdist version and installed `powercontext --version` | `pyproject.toml` declares `dynamic = ["version"]`; Hatch VCS reads Git metadata. Use the intended release tag for the final build. |
| Runtime package metadata | Reads the installed Python distribution version; there is no separate static runtime version to bump. |
| DSH npm archive | The release workflow stamps `powercontext-dsh` from the selected Release tag in a temporary package directory. The checkout manifest remains a development version; runtime identity reads the installed manifest. |
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
| JavaScript plugins | `integrations/{opencode,pi}/plugins/powercontext/package.json` and `integrations/openclaw/plugins/memory-powercontext/package.json`. The OpenClaw discovery manifest has no separate version field. DSH release archives follow the main Release. |
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

## DSH npm package

`release.yml` builds `powercontext-dsh` from the same pinned commit as the Python release. It tests the plugin,
packs compiled entry points, declarations, packaged Skills, the bundle patch, README, license, and manifest, then installs
that exact archive into a clean consumer directory. The source manifest is never rewritten during packing.
The verified `.tgz` is retained as `dsh-release-package` and attached to the GitHub Release.
PyPI publication requires this package check as well as the existing release gate. npm publication follows
successful PyPI publication and runs only in `oceanbase/powercontext`.

Versions follow the Release tag: `powercontext-v1.6.0rc1` becomes `1.6.0-rc.1`; `a` and `b` become `alpha` and
`beta`. Stable releases use `latest`; version prereleases and releases marked as prerelease on GitHub use `next`.
The checkout's development version is not a separate npm release counter. Older tags without these packaging
scripts cannot be published through this workflow; select a release containing the npm delivery changes.

The publication job uses [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/), Node 24, and
GitHub's `id-token: write` permission. No npm token is stored in the workflow. A package owner must complete
the initial npm publication and configure its trusted publisher:

| npm setting | Value |
| --- | --- |
| Package | `powercontext-dsh` |
| Provider | GitHub Actions |
| Organization | `oceanbase` |
| Repository | `powercontext` |
| Workflow filename | `release.yml` |
| Environment | Leave blank; the publication job does not declare one |
| Allowed actions | Allow direct `npm publish` |

For the first publication, download the verified archive from the intended release build, sign in with the
maintainer's npm account, and publish that exact file. For example, a prerelease archive uses:

```bash
npm login --registry=https://registry.npmjs.org/
npm publish ./powercontext-dsh-1.6.0-rc.1.tgz --access public --ignore-scripts --tag next --registry=https://registry.npmjs.org/
```

Use `--tag latest` for a stable release. Configure the trusted publisher after the package exists, then rerun
the failed npm job. A rerun skips publishing only if the registry's SHA-512 integrity matches the archive;
different bytes under the same version fail explicitly. Identical reruns leave dist-tags unchanged.
Re-running an older release is not a way to roll back `latest` or promote a prerelease. Check the registry's
current tags before publishing a previously unpublished historical release.

To validate packaging locally, run from the repository root:

```bash
pnpm --dir integrations/dsh/plugins/powercontext install --frozen-lockfile --config.auto-install-peers=false
pnpm --dir integrations/dsh/plugins/powercontext test
pnpm --dir integrations/dsh/plugins/powercontext test:package
pnpm --dir integrations/dsh/plugins/powercontext pack:release 1.6.0-rc.1
```

The archive is written under the plugin's `dist/` directory. In DSH Desktop, install it using **Plugins → Add
plugin**, confirm the package and enabled state, restart if requested, and run `/pc doctor` against a matching
Server. Record the Desktop version and installation/loading results separately from Server readiness and
memory behavior. A local archive test does not establish npm availability; after publishing, repeat with
`powercontext-dsh@<version>` against the official registry and verify the selected mirror separately.

## Verify before publication

### Database upgrade support

`release/upgrade-support.json` declares the target version, source-version selection policy, backends, and
configurations. `automatic_sources` keeps 1.3.0 as its fixed baseline; explicit `cases` are empty.
The catalog declares twelve cases: one source × three backends × vectors enabled/disabled × Code enabled/disabled.
Normal publication requires all twelve automatic cases to pass, including both embedded seekDB configurations with
Code enabled. Failed, skipped, or missing cases block acceptance; the diagnostic-only `seekdb-code` selection cannot
authorize publication. Publication depends on the actual acceptance evidence, without a separate support-declaration approval.
The target is always the actual wheel built by CI. Future target releases keep testing 1.3.0;
intervening releases are not added automatically. The registered 1.4.0 bridge remains implemented but is outside
CI upgrade acceptance, so a passing gate does not certify its upgrade path. Source wheel URLs and SHA-256s are resolved
from public PyPI and recorded with the target artifact.

The source-specific chains start at `s1300` for 1.3.0 and `r1400` for 1.4.0, join the shared
`s1400 → s1401 → s1402` path, and continue through `v1_4_1_004 → v1_4_1_005 → v1_4_1_006 → v1_5_0_001 → v1_5_0_002 → v1_5_0_003 → v1_5_0_004`.
They share the target contract while preserving their frozen execution
history and journal lineage for subsequent registered upgrades. Application versions do not identify schema
checkpoints. In particular, 1.4.0 already has current Candidate envelopes: its bridge preserves those records and
creates empty retained tables for the common target shape, without inventing old Candidate history. Adding other
sources requires exact captures, a registered bridge and actual acceptance; the two registered paths do not certify
all releases between them or the target.
The current `upgrade-required` policy mandates positive acceptance on all three backends with support limited to combinations
that pass full Server acceptance. Every profile points to `tests/release_upgrades/test_full_server.py`; missing migration support
must fail. Neither a fresh-install-only declaration nor correct rejection of an old database satisfies this gate.
Both `upgrade-required` and `supported-upgrades` run the same mandatory acceptance matrix and allow publication
when it passes. Changing the policy mode or adding support prose to the Release body is not required for publication.
The suite contract and evidence requirements are in `tests/release_upgrades/README.md` in the repository.

`make check` and `make release-check` validate this policy and the bilingual installation notices. Changes to
managed persistence inputs require a compatibility review with an updated digest and rationale. A digest is a
review trigger, not evidence that a migration is correct. Managed text hashes normalize CRLF to LF so checkout
line endings do not require a new review; distribution hashes always cover the exact archive bytes. `make version-bump` also updates the policy target;
regenerate the support notices with `uv run python scripts/upgrade_release_gate.py --write-docs` after matrix changes.

Build exactly one wheel and its matching source distribution into `dist/`, then execute the declared upgrade cases:

```bash
make upgrade-release-test
uv run python scripts/run_upgrade_release_gate.py --release
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

The release check requires the actual wheel version to match the policy. Release notes have no required support
declaration or configuration table. It installs each exact source release and the target wheel
in separate environments. Missing tests, skipped backends, failures, and missing evidence block the check. Static
policy validation alone does not establish an upgrade path. Ordinary PR builds may have a development
version; the `--release` check must run against the final tagged wheel without overriding its version.

The **Upgrade release gate** CI job requires successful public migration commands, full Server readiness, target
startup, preserved records and new writes on real 1.3.0 databases in all twelve required automatic cases. The two
seekDB Code cases are mandatory. Limited bundle verification with
`server_ready=false` fails. Every backend runs even if another backend fails, with separate logs and results.
seekDB runs embedded in a temporary runner directory. PR and publication jobs start a disposable OceanBase CE
container, wait for its MySQL tenant, archive diagnostics, and remove it even on failure. No external database
credentials are needed. Unavailable backends fail instead of being skipped. Historical rejection regressions are
not part of the positive acceptance gate. Administrators must make this job required in branch protection.
By default, the release workflow gates distribution upload, PyPI publication, and release assets on the acceptance result and
archives its evidence. It runs after `release.published`, so it cannot prevent the earlier manual publication of
the GitHub Release page: run the command above before that action. `make publish` runs the same gate before
uploading packages and does not require a release-notes file.

### Publish an existing release tag

After the workflow changes are merged, run **Actions → Release → Run workflow** from `master` and enter the
existing published tag in `release_tag`. Leave `skip_upgrade_acceptance` disabled for full upgrade acceptance:

```bash
gh workflow run release.yml --repo oceanbase/powercontext --ref master \
  -f release_tag=powercontext-v1.5.1
```

The workflow pins its own commit for gate tooling and the selected tag commit for package builds and acceptance
suites. This applies gate fixes to existing tags without moving them or altering their package contents.
Preparation, case execution and collection use the same pinned tooling; the evidence records both commits.
No support table, fixed wording, policy-mode promotion or additional approval input is required.

### Manual publication without upgrade acceptance

The **Release** workflow has a `workflow_dispatch` input named `skip_upgrade_acceptance`, defaulting to `false`.
Enable it only to publish for new databases without claiming that existing databases can be upgraded. This does
not change `release/upgrade-support.json`, certify a migration path, or relax the default release and PR gates.

In **Actions → Release → Run workflow**, select the default branch (`master`), set `release_tag` to an existing
published tag such as `powercontext-v1.4.1`, and enable `skip_upgrade_acceptance`. The workflow definition must
already be merged into the default branch. Select the tag through the input, not the workflow branch selector.

```bash
gh workflow run release.yml --repo oceanbase/powercontext --ref master \
  -f release_tag=powercontext-v1.4.1 -f skip_upgrade_acceptance=true
```

The workflow resolves the existing tag to a commit and builds that commit; it does not move the tag or package
the current default branch. Release-body wording does not gate this explicit manual option.
Version checks, synchronized release references, policy consistency, package metadata checks, an sdist rebuild
comparison, clean installation, dependency checks, and the new-database Server/MCP/Memory smoke test remain
mandatory. Only then can the exact checked distributions reach PyPI and GitHub Release assets. Post-publication
verification and website deployment remain enabled, using the selected release target.

The `release-without-upgrades` artifact records the tag, source commit, actor, run, distribution hashes, and
`upgrade_acceptance: skipped`; the run summary also states that existing-database upgrades are unsupported.
Leaving the option disabled runs the normal automatic upgrade matrix.

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

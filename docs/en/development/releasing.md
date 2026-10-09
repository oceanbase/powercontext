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
| `src/powercontext/http/_generated/` | `API_VERSION` in `operations.py` and `info.version` in `schema.py`, regenerated through `make api-generate`. |
| `docs/en/docs/get-started/install-and-run.md`, `docs/zh/docs/get-started/install-and-run.md` | Explicit version examples, package/tag pins, and Python Client installation. |
| `integrations/dsh/plugins/powercontext/README.md` | Server installation pin and the matching `setup dsh --ref` tag. |

The installation guides also contain historical migration versions. Those references are preserved. When adding
another current-release installation example, include it in the version tooling's managed paths and patterns so
the next release updates and checks it.

README and Quick Start install the latest stable release through the scripts and derive integration tags from the installed CLI. Release bumps preserve these instructions and the independent uv bootstrap version.

### Derived from another source

| Value | Source and handling |
| --- | --- |
| Python wheel/sdist version and installed `powercontext --version` | `pyproject.toml` declares `dynamic = ["version"]`; Hatch VCS reads Git metadata. Use the intended release tag for the final build. |
| Runtime package metadata | Reads the installed Python distribution version; there is no separate static runtime version to bump. |
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
| Evaluation and harness packages | `evaluation/pyproject.toml`, `evaluation/web/package.json`, and `e2e/bub/pyproject.toml` have their own package versions. |
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

---
title: Desktop candidate assembly
description: Assemble exact, unqualified Desktop and managed resource candidates for Windows x64 and both macOS architectures.
---

# Desktop candidate assembly

[Issue #2112](https://github.com/oceanbase/powercontext/issues/2112) separates candidate assembly, managed resource production, and publication. The **Desktop candidate assembly** workflow builds normal product packages and reuses the [managed resource producer](managed-resource-production.md). Its outputs are unsigned Actions artifacts for validation and handoff. They cannot be released or treated as installed qualification.

## Build identity

A release owner explicitly selects the upstream `master` revision and dispatches `desktop-candidate.yml` with:

- `version`: exact Desktop `X.Y.Z`. The prospective tag is `desktop-vX.Y.Z`, independent of the Python release tag. The app package identity is `com.powercontext.desktop`.
- `server_version`: exact canonical Python Server package version (e.g. `1.5.1.dev25+g63e9665c6`), built from the pinned Server commit with the producer's locked runtime inputs. This version is independent of Desktop's version and is never inferred from a package index.
- `source_sha`: full Git commit, which must equal the workflow checkout. An existing Desktop tag may only refer to that same commit. Candidate assembly never creates a tag.
- `server_source_sha`: full Server commit. It can point to an earlier qualified Server revision. The wheel and its dependency lock come from that source; the Desktop build and the producer tools are still bound to `source_sha`.

Desktop version parts are integers from 0 to 65535, without leading zeros. The workflow uses a fresh release build without `ci-fixtures`, with the producer's exact catalog and Ops pins. It requires Windows x64 (NSIS), macOS arm64 (DMG), and macOS x64 (DMG). Linux is omitted. A later opt-in Linux release needs separately qualified assets from [#2111](https://github.com/oceanbase/powercontext/issues/2111); no Linux job can hold this required aggregate.

## Candidate handoff

Download the single successful run's `desktop-candidate-<version>-<source_sha>` artifact. The required target jobs produce same-run inputs; the aggregate never accepts uploads from preview builds, fixture manifests, or another run. Output names are `powercontext-desktop-<version>-<target>.exe` or `.dmg` under their respective target directory. Server and Ops archives, catalog, provision descriptor, input locks, producer manifests, the actual project wheel, and smoke evidence accompany each package. Target identifiers are `windows-x86_64`, `macos-arm64`, and `macos-x86_64`.

`desktop-release-candidate.json` has `schema_version: 1`, `kind: desktop-product-candidate`, `version`, `release_tag`, `source_sha`, `server_source_sha`, `server_version`, `status: unqualified_candidate`, and `publishable: false`. `contract` records the exact Desktop HTTP contract digest and operation set, and the bundled operation and compatibility files. `targets` maps each target to `build_kind: product`, `assets`, `provenance`, `qualification`, and `runtime`. The four release roles in `assets` are `desktop`, `server_archive`, `server_catalog`, and `ops_archive`. Each file entry has a root-relative `name`, byte `size`, and lowercase `sha256`. Ops provision and bundle manifest files are in `provenance` with unique basenames per target. `qualification.producer_ref` is always an unqualified producer reference. `matching_product_records` lists only bundled records matching the actual Server wheel, source, contract and operation set; an empty list leaves product protocol qualification outstanding. Even a matching record does not qualify an installer.

For publication handoff, select `desktop-candidate-manifest-<version>-<source_sha>`, which contains only the exact same `desktop-release-candidate.json`. The run summary records `candidate_run_id`, `candidate_full_artifact_id`, `candidate_manifest_artifact_id`, and `candidate_manifest_sha256`. Record the manifest artifact ID (as `candidate_artifact_id` in the publication handoff) so publication can retrieve the single file without downloading the large archives. Both Actions artifacts have a 14-day retention.

To check the complete candidate, take the manifest SHA-256 from the trusted run summary and verify the downloaded files:

```sh
uv run --locked --no-dev --group resource-build python scripts/desktop_candidate.py verify --root <download-directory> \
  --version <desktop-version> --server-version <server-version> --source-sha <desktop-commit> \
  --server-source-sha <server-commit> \
  --manifest-sha256 <sha256-from-run-summary>
```

The checksum binds the exact manifest and asset bytes. Trust still comes from selecting a successful run of this workflow on the intended upstream source. Record its run and artifact identity; a rerun can produce different bytes under the same human-readable name and cannot silently replace an accepted set. Candidate artifacts expire after 14 days; they are not an availability channel for installed clients.

## Qualification before publication

CI consumes the real catalog and archive, launches a disposable SQLite Server with bundled Python, and inspects local state with the independent Ops entry. This does not exercise a real HTTP download or register a service. The candidate catalog deliberately uses `candidate.invalid`.

The final platform packagers own signed Windows x64 and signed/notarized macOS arm64/x64 builds and install the approved independent Ops helper at the [fixed current-user location](../docs/operate/local-server-ops.md#provisioning-the-bridge). Desktop removal must preserve the independent helper, service state, and user data. Windows must retain both `pythonw.exe` for the Server service and `python.exe` for console use. The candidate packages embed pins but do not install the independent helper. Provisioning the helper, clean-machine installation and repair, reboot/login and native service lifecycle are part of [#2108](https://github.com/oceanbase/powercontext/issues/2108) and [#2109](https://github.com/oceanbase/powercontext/issues/2109).

When building the platform release, use the approved immutable resource URL and regenerate any affected inventory, archive, catalog, and provision digest after signing changes nested code. Embed the final pins before signing the outer Desktop package. Preserve the candidate manifest digest and record the separately qualified final package and resource digests; candidate bytes and signed bytes need not be identical. Final acceptance requires the same Desktop/Server combination to demonstrate identity, Scope and Memory, statistics, Runtime observation, and local setup through to the default Scope. Do not substitute a rebuilt wheel or a fixture for [#2101](https://github.com/oceanbase/powercontext/issues/2101) qualification. Publication and re-download verification consume the exact final signed and tested bytes.

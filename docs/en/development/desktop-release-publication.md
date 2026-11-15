---
title: Desktop release publication
description: Publish, verify, and promote the exact qualified Desktop packages and local setup resources.
---

# Desktop release publication

[09-B in #2112](https://github.com/oceanbase/powercontext/issues/2112) publishes already qualified files.
09-A supplies the original normal product candidate manifest, and
[09-C](managed-resource-production.md) supplies the pinned resource inputs. Platform maintainers own the final
Windows signing and installed acceptance ([#2108](https://github.com/oceanbase/powercontext/issues/2108)) and macOS
signing, notarization, stapling and installed acceptance ([#2109](https://github.com/oceanbase/powercontext/issues/2109)).
A passing build, Actions artifact, fixture or unsigned candidate does not establish publication eligibility.

## Configure the release boundary

The release maintainer dispatches **Desktop release publication** from protected upstream `master` only. Configure
the `desktop-release` environment with **protected branches only**, required reviewers, prevention of self-review,
and administrator bypass disabled. Assign a release reviewer and a named acceptance owner per target. Protect
`desktop-v*` tags from changes or deletion; create the tag at the approved Desktop commit on `master` before dispatch.
The workflow never intentionally creates or moves a tag. It checks the live tag against the approved commit before
writes and after public downloads. Only the approved job receives a `contents: write` workflow token; no signing key
or personal token is required by 09-B. The Python release workflow and its tags are independent.

Use the small `desktop-candidate-manifest-<version>-<source_sha>` artifact from a successful, manually dispatched
`desktop-candidate.yml` run at the exact tagged commit. Its only member is the original
`desktop-release-candidate.json`; keep the full candidate artifact for signing and audit.
Provide the exact final files as an immutable Actions artifact named `desktop-signed-release-handoff` from a
successful maintainer signing handoff dispatched on upstream `master`. The platform producer must stage this artifact
(for example with `actions/upload-artifact` in the protected signing workflow). 09-B does not implement signing,
produce platform approval records, or accept arbitrary URLs or handoffs from other branches or repositories.

## Build the final handoff

Windows x64, macOS arm64 and macOS x86_64 are required, each with its own installer, Server archive and catalog,
independent Ops archive and bundle manifest, and provision descriptor. Ubuntu x86_64 can be selected only when
[#2111](https://github.com/oceanbase/powercontext/issues/2111) and the matching candidate/resource producers
supply that same qualified inventory. Omit Linux entirely when it is not qualified. Do not advertise it.

Final resources and catalogs must be assembled before their dependent package is signed and accepted. On macOS,
sign nested code before freezing archives and their manifests. Give the catalog the final
`https://github.com/oceanbase/powercontext/releases/download/<tag>/<server-archive>` URL and allow the GitHub and
`https://release-assets.githubusercontent.com` origins. Recompute the catalog, helper and provision pins and
embed the final pins before signing. Report the three installed pins read from the final installed package and
independent Ops bundle; the candidate catalog or its placeholder origin is never a release input. Check that the
installed app and helper can bootstrap a clean machine without system Python, using the chosen Server profile,
and complete the required first-use and recovery outcomes. 09-B checks the binding to exact bytes. The approver
must inspect the platform owners' actual signature and installed acceptance reports.

The signing handoff ZIP must contain only flat regular files, no directory entries, with at most 6 GiB of uncompressed
payload. Use the final asset names from 09-A (the basename of each candidate path). Its `desktop-release.json`
contains these fields:

| Object | Fields |
| --- | --- |
| Root | `format_version: 1`, `kind: "qualified-desktop-release"`, `release_tag`, Desktop `source_sha`, `candidate_run_id`, `candidate_artifact_id`, `candidate_manifest_sha256`, `targets` keyed by target ID. |
| Each target | `assets` keyed by `desktop`, `server_archive`, `server_catalog`, `ops_archive`, `ops_provision`, `ops_bundle_manifest`; each has `name`, integer `size` in bytes, and lowercase `sha256`. `candidate_asset_sha256` has the same keys, with the original candidate digests. |
| Each target's `qualification` | `kind: "installed-product"`, `signing` (`authenticode`, `developer-id-notarized-stapled`, or `linux-qualified`), `clean_machine_provisioning: "passed"`, `asset_sha256` for all final roles, `installed_catalog_sha256`, `installed_ops_provision_sha256`, `installed_ops_bundle_manifest_sha256`, `os_version`, `webview_version`, `server_version`, `runtime_version` (the pinned Python version), `limitations`, `report` (upstream issue/comment or Actions run URL), and `server`. |
| `qualification.server` | `kind: "qualified-product-server"`, `id` from the candidate's matching product records, upstream `report` URL, `wheel_sha256`, `contract_sha256`, and exact Server `source_sha` (can differ from the Desktop commit). |

The maintainer should check that the reported Windows signer and macOS Developer ID/notarization results cover
the final installers and required executable resources. The workflow rejects Windows/macOS installers whose bytes still match the unsigned candidate.
The compatibility record must cover the exact Server wheel and
Desktop operations. If 09-A reports no matching product record, qualify the actual inputs first; do not edit the
candidate manifest or substitute a different wheel.

## Prerelease, retry, and promotion

1. Dispatch `prerelease` with the existing tag, Desktop commit, candidate and staging run/artifact IDs, and
   SHA-256 of `desktop-release.json`. The read-only job validates the inputs and lists the final digests and reports
   in its run summary. An independent reviewer checks those reports before approving the environment.
2. The writer fills a draft Release. A rerun for the same identity skips existing assets with the exact expected
   size and SHA-256. It may remove a zero-byte GitHub `starter` entry left by a failed upload on that draft. It
   never replaces an uploaded file. An unexpected asset, identity, or digest stops the run; investigate rather than
   overwrite. If qualified bytes must change, use a new Desktop version and tag. Keep final signed inputs and
   evidence available while retrying; Actions artifacts expire.
3. Only after the complete draft inventory matches does the workflow make it a public **prerelease**. It downloads
   each file, including the two manifests and generated `SHA256SUMS.txt`, into a new directory from its real public
   asset URL, without credentials, and checks the size and SHA-256. A public download failure leaves the same
   prerelease in place; rerunning verifies it without modifying its inventory.
4. On each included target, download the installer from that prerelease and exercise local setup on a clean machine
   against the published resources. Record the actual installer URL and the digests used in the test, and retain
   the reports. Restage the **same** final files and byte-identical release manifest, with one extra
   `desktop-provisioning-evidence.json` file. Its root has `format_version: 1`,
   `kind: "downloaded-desktop-provisioning"`, integer `release_id`, `release_manifest_sha256`, and `targets`
   for every published target. Each target records `download_url`, all final role digests in `asset_sha256`,
   `clean_machine_provisioning: "passed"`, and the upstream `report` URL.
5. Dispatch `promote` with the same release identity, new staging IDs, and the approved provisioning evidence
   SHA-256. The independent reviewer checks downloaded-installer evidence. The workflow verifies public files
   again before it clears the prerelease flag. It records the evidence digest and links in the Release notes and
   leaves the assets and tag intact. A failed or cancelled run never defaults to stable.

Keep published tags and resource assets available for installers that pin their URLs and digests. Archive the accepted evidence beyond the Actions artifact retention period. Do not
manually swap, delete, or reuse a tag to fix a release. If an asset must be withdrawn, coordinate the support and
recovery plan with the platform and Installer owners before removing a pinned resource. The workflow does not
perform platform installation itself, and local fake-HTTP tests are not evidence for #2112's real prerelease
or downloaded provisioning acceptance.

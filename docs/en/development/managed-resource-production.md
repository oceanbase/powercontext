---
title: Managed runtime resource production
description: Build fixed, unqualified Server and independent Ops candidates for Windows x64 and both macOS architectures.
---

# Managed runtime resource production

The candidate producer supplies the fixed resources consumed by [managed installation](../docs/operate/managed-local-server.md).
It produces a complete Server interpreter/package tree and a separate [Ops bundle](../docs/operate/local-server-ops.md).
Users of those artifacts need no system Python, uv, Git, or compiler. This is the resource-production part of
[#2112](https://github.com/oceanbase/powercontext/issues/2112); Desktop assembly and public distribution remain separate.

Every output is an **unqualified candidate**. The scripts cannot promote a build by accepting a qualification flag.
Successful builds and CI smoke results do not establish signing, notarization, clean-machine operation, or native
Task Scheduler/LaunchAgent lifecycle acceptance. They do not publish artifacts or infer a production download URL.

## Fixed inputs and supported build hosts

Build natively on Windows x86_64, macOS arm64, or macOS x86_64. Linux is not a prerequisite and cross-compilation is
not supported. The build host needs Git, curl, uv 0.13.5, and the locked `resource-build` dependency group. This bootstrap
group imports the Ops resource/model contracts without Server transports, cryptography, jieba, or maturin. Installing
the full `server`/`ops` extras first would attempt an ambient Intel cryptography source build before the pinned builder.
Build-host tools
are not copied from that environment into either resource.

`scripts/managed_resource_interpreters.json` pins CPython **3.12.16**, Astral python-build-standalone tag **20261026**,
and the exact install-only archive URL/SHA-256 for each target:

| Target | Upstream artifact suffix | SHA-256 |
| --- | --- | --- |
| `windows-x86_64` | `x86_64-pc-windows-msvc-install_only.tar.gz` | `590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5` |
| `macos-arm64` | `aarch64-apple-darwin-install_only.tar.gz` | `fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e` |
| `macos-x86_64` | `x86_64-apple-darwin-install_only.tar.gz` | `a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3` |

These are the upstream [20261026 release](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026)
artifacts, using its documented [install tree](https://docs.astral.sh/python-build-standalone/distributions/).
The producer preserves in-tree interpreter and dependency notices and emits `resource-licenses.json` in both trees.
That index binds retained notices and wheel license metadata to the actual package hashes; it is not legal clearance.

The preparation script takes an exact 40-character Git source commit and an explicit canonical package version.
It builds from `git archive`, so uncommitted files, editable installs, and the developer environment cannot enter the
project wheel. The supplied version is embedded with setuptools-scm's explicit version override and verified from
the resulting wheel and installed metadata. The source commit, archive, `uv.lock`, `pyproject.toml`, project wheel,
interpreter, build-tool closure, and all downloaded/built wheel identities are recorded in `inputs.json`.
The executing producer's Git revision and script digests are recorded separately from the packaged Server source.
`matches_committed_files=false` identifies a working-tree producer; it must not be described as a committed release builder.

Runtime dependencies are exported from that source's locked `ops` and `cli` extras (`ops` includes `server`). Preparation
selects compatible wheels using the pinned interpreter's actual tags and environment markers, then verifies the
original `uv.lock` sizes and hashes. Only `jieba==0.42.1` may use a locked sdist: it has no upstream wheel. Its source
hash is checked before building with the exact hash-pinned tools in `scripts/managed_resource_build_tools.json`,
without build isolation or backend dependency downloads. The project wheel is the other explicit source build.
On Intel macOS, the exact locked `cryptography==50.0.0` is an additional source-build exception described below.
Other source-only dependencies fail clearly instead of enabling an implicit build. The resulting target wheel closure
is frozen as exact versions and individual wheel hashes in `requirements.lock`.

Preparation may access the network. Final resource production uses only that verified wheelhouse, offline mode,
binary-only installation, and `--require-hashes`. Source builds are not claimed to be reproducible across arbitrary
toolchain/host changes: retain their actual wheel bytes and hashes. Repeated production from the same frozen inputs
must produce identical archives and manifests.

### Intel cryptography source build

Upstream cryptography [removed x86_64 macOS support in 49.0.0](https://cryptography.io/en/50.0.2/changelog/#v49-0-0).
The producer preserves the source lock's `50.0.0`, rather than downgrading to an older wheel. Its separate
`build_intel_managed_wheel.py` path builds the exact hashed sdist with these fixed native inputs:

| Input | Fixed identity |
| --- | --- |
| Rust | `1.95.0`, `x86_64-apple-darwin` distribution SHA-256 `998af750fe2c28f0dbf193edc496c9a7407850486968f40ac11a8c8adfb7cfa6` |
| OpenSSL | `4.0.3` source SHA-256 `325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9` |
| Backend | `maturin==1.14.1`, pinned Intel wheel; existing pinned setuptools plus native locked cffi/pycparser wheels |
| Compiler/SDK | Xcode `16.4` build `16F6`, macOS SDK `15.5`, deployment target `10.13` |

All download URLs and hashes are in `scripts/managed_resource_intel_build.json`. Rust is installed only into a disposable
build directory. OpenSSL is built statically there; no Homebrew library is shipped or referenced. Every external Cargo
crate must have a crates.io checksum in the source's fixed `Cargo.lock`; `cargo vendor --locked` verifies acquisition,
then the build uses vendored dependencies and offline Cargo. Backend installation is hash-locked, offline, and binary-only.
Its bare `maturin` command resolves to the exact binary from the verified wheel in the task-local build environment.
Inherited target-prefixed OpenSSL settings and Python/backend/compiler overrides cannot redirect the pinned inputs.
The finished wheel retains OpenSSL and vendored Rust license notices. `otool` rejects non-system dynamic dependencies
and runtime search paths. Installed candidate probes perform AES-GCM roundtrip and Ed25519 signing/verification on all targets.
The source/tool/SDK identities and linkage results enter `inputs.json` before its digest is frozen.

Intel build hosts must have that Xcode installed and select it for this process with
`DEVELOPER_DIR=/Applications/Xcode_16.4.app/Contents/Developer`; CI sets this variable without changing global Xcode selection.
This native build is a candidate for a platform upstream no longer supports. Compilation and smoke checks do not restore
upstream support or establish production maintenance/security qualification. The Intel CI leg must actually pass; it is
not skipped when a wheel is unavailable. No bit-for-bit source-build claim is made across different compiler/SDK identities.

## Produce a candidate

Use the current committed producer checkout, then choose the exact package-source commit and version. This executable
macOS arm64 recipe freezes the current commit and assigns an explicit candidate version:

```bash
uv sync --locked --no-dev --group resource-build --python 3.12 --no-build-package cryptography --no-build-package jieba
package_source_sha="$(git rev-parse HEAD)"
uv run --no-sync python scripts/prepare_managed_resources.py \
  --target macos-arm64 \
  --source-ref "$package_source_sha" \
  --server-version "0.0.0+candidate.$package_source_sha" \
  --output build/resource-inputs \
  --download-cache build/resource-downloads
```

The supplied source commit must exist in this checkout and contain a compatible `uv.lock` and package build contract.
Use `macos-x86_64` on Intel macOS. The equivalent Windows PowerShell preparation is:

```powershell
uv sync --locked --no-dev --group resource-build --python 3.12 --no-build-package cryptography --no-build-package jieba
$packageSourceSha = (git rev-parse HEAD).Trim()
uv run --no-sync python scripts/prepare_managed_resources.py --target windows-x86_64 --source-ref $packageSourceSha --server-version "0.0.0+candidate.$packageSourceSha" --output build/resource-inputs --download-cache build/resource-downloads
```

`--runtime-archive` can select an already downloaded interpreter archive; the same fixed hash
is still mandatory. The optional task-local download cache is digest-addressed, and every cache hit is rehashed.
An existing corrupt or incomplete input must be inspected before retrying; it is never silently adopted.

Retain the exact `inputs_sha256` printed by preparation, then pass it to the offline producer:

```bash
uv run --no-sync python scripts/produce_managed_resources.py \
  --inputs build/resource-inputs \
  --inputs-sha256 <printed-inputs-sha256> \
  --server-url https://candidate.invalid/server.tar.gz \
  --output build/resource-candidate
uv run --no-sync python scripts/ci_managed_resource_smoke.py \
  --candidate build/resource-candidate --serve --evidence-dir build/resource-evidence
```

`candidate.invalid` is a reserved test origin, not a usable download location. Supply an operator-controlled fixed HTTPS
candidate URL when testing real HTTP delivery; the producer neither uploads to it nor discovers one. The smoke tool
seeds the integrity-checked local cache with the exact archive, then uses real `ResourceCatalog` and `ResourceStore`
readback. It does not claim that an HTTP download occurred.

If smoke fails, bounded fixture stdout/stderr and an exception-class summary are saved under the explicit evidence
directory before temporary cleanup. Each log is limited to its last 64 KiB; raw exception/child stdout is not printed
inline. Without `--evidence-dir`, the durable default is the candidate's sibling `<candidate-name>-evidence` directory.

Input and output directories must not contain one another. Existing outputs are never overwritten. Keep a failed or
partial build for inspection and retry at a fresh path. Keep large input/output/cache directories out of source control.

## Output and launch contract

| Output | Purpose |
| --- | --- |
| `server/server.tar.gz` | Deterministic, link-free Server tree, including its own Python and complete installed wheel closure. |
| `server/catalog.json`, `server/catalog.sha256` | Exact `ResourceCatalog`/`ResourceSpec` bytes and their digest. Each file has path, size, SHA-256, and executable mode. |
| `server/resource.json` | Complete source/input provenance plus the produced resource specification. |
| `ops/` | Independent `runtime/`, `packages/`, `entry.py`, and compatible `bundle.json`, built through `build_ops_bundle.py`. |
| `ops.tar.gz`, `ops-resource.json` | Deterministic Ops archive and complete file size/hash/executable inventory, including `bundle.json`. |
| `ops-provision.json` | Desktop distribution input pinning exact `ops/bundle.json` and `server/catalog.json` digests, with `binding_mode=managed_catalog`. |
| `candidate.json` | Unqualified status, source/package/output bindings, producer identities, and actual Windows path measurements when run on Windows. |

The qualification reference starts with `unqualified-candidate:sha256:` and binds the frozen source/package inputs and
actual outputs. It provides no evidence of platform qualification. A checksum beside an untrusted artifact is not a
trust anchor. Desktop's packager must pin the descriptor in its trusted distribution and provision the independent
helper at the protected per-user `PowerContext/Ops/bundle` location, outside both the Desktop and managed Server trees.
Its catalog is packaged at `resource_dir/server/catalog.json`. Merely generating this directory does not install Desktop
or provision a helper.

The Server runs the exact bundled interpreter with `-I -B -m powercontext.service.launcher`. Windows catalogs select
`pythonw.exe` and also inventory the paired `python.exe`; Ops uses the console interpreter. Ops retains the existing
`-I -S -B entry.py` launch contract and manifest format. Installer-generated console wrappers are removed because their
absolute build-path shebangs/launchers do not relocate. Mutable uv installer-cache records containing build-input
timestamps are also removed; distribution `RECORD` rows are repaired accordingly. Modules,
native libraries, wheel data, and license notices remain. Human CLI use can invoke the bundled callback with
`<bundled-python> -I -B -c 'from powercontext.cli.app import main; main()' --help`.

Interpreter archive links are never extracted as links. Safe relative aliases resolving to an internal regular file
are materialized as independent regular files and recorded in provenance. Directory aliases, external/absolute links,
cycles, special files, ambiguous paths, and reparse points are rejected. Gzip/tar metadata, ordering, timestamps,
permissions, and ownership are normalized; bytecode generation is disabled while probing sealed output trees.

Windows production checks the actual current user's managed final directory, `staging/resource-<32 hex>` directory,
and independent Ops final directory against ordinary Win32 UTF-16 file/directory limits. It also invokes the consumer's
actual destination validator for Server paths. No system-wide long-path setting is enabled. Results on a short build
path do not qualify a different user profile; unsupported destination paths remain rejected by the consumer.

## CI and evidence boundary

`.github/workflows/managed-resource-candidates.yml` performs real native production on `windows-2025`, `macos-15`
(arm64), and `macos-15-intel`. Each matrix leg acquires pinned inputs from its exact commit, builds twice, compares
archive/manifest bytes after relocating the same hashed inputs, rejects mutation/version mismatches, consumes the real catalog/archive, and runs isolated
installed Server imports plus the exact service launcher against disposable SQLite data. The independent Ops smoke
starts with poisoned Python variables, audits module origins, and inspects a missing Server runtime with native
service-manager read-only commands. It never registers a service. Successful jobs retain explicitly unqualified candidates.
Bounded diagnostics, input identities and test evidence are retained on failure; partial runtimes and download caches are
not uploaded as failure diagnostics. A failed job is not a qualified resource.

Run focused local checks with:

```bash
uv run --no-sync pytest -q tests/test_managed_resource_producer.py tests/test_ops_resources.py
```

Without explicitly prepared native inputs, the expensive native producer tests skip. To enable the same native path,
set `POWERCONTEXT_RESOURCE_TEST_INPUTS` to the prepared directory and
`POWERCONTEXT_RESOURCE_TEST_INPUTS_SHA256` to its trusted `inputs.json` digest before running that command.
`POWERCONTEXT_RESOURCE_TEST_OUTPUT` optionally selects the first retained candidate directory; it must not exist.
Synthetic Windows archive/path tests verify rejection and layout logic, not actual Windows execution.

Formal assembly/publication must still validate Windows signatures, macOS signing and notarization, clean standard-user
Windows 11/macOS operation without developer tools, login/reboot/service lifecycle, and preservation through repair and
removal. Signing can change interpreter/native-library bytes. Perform actual signing before freezing the hashes in any
qualified file inventory, archive, catalog, bundle manifest, or provision descriptor; regenerate and reverify the complete
identity chain afterward. Candidate digests must never be reused as proof of different signed artifacts.

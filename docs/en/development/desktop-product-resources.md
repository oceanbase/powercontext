---
title: Desktop product resources
description: Prepare pinned, redistributable Ops resources and a durable Server download catalog for a product build.
---

# Desktop product resources

The [candidate workflow](desktop-candidate-assembly.md) produces unqualified build artifacts. Product packaging additionally needs an embedded independent Ops runtime and a Server catalog pointing at approved durable downloads. `scripts/prepare_desktop_product.py` prepares these inputs without publishing assets, installing a Server, or claiming platform acceptance.

## Prepare the exact inputs

Use the native target's complete [resource producer output](managed-resource-production.md): `candidate.json`, `server/catalog.json`, `server/server.tar.gz`, and the expanded `ops/` tree. This is the producer's output directory, not the renamed aggregate Desktop artifact. Preserve its provenance and obtain the SHA-256 of `candidate.json` from the trusted producer run. A checksum found beside an untrusted download is not an independent trust source.

Choose a non-`ci-` record already checked into `desktop/src-tauri/src/connections/compatibility.json`. Its Server commit, actual wheel digest, HTTP contract digest, and covered operations must match these resources and the Desktop checkout. If none matches, qualify that exact Server build through [#2101](https://github.com/oceanbase/powercontext/issues/2101) before preparing a product; do not copy a fixture record or relabel an unqualified candidate.

Publish the exact Server archive to an approved immutable HTTPS asset URL before distributed clients use it. Allowlist the initial origin and any approved redirect origins. Temporary signed query URLs, placeholder hosts, Actions artifact downloads, and mutable `latest` routes are unsuitable. The script rejects query URLs and reserved placeholder hosts; release owners still approve the URL's immutability, origin policy, retention, and actual availability. It makes no network request. Ops retains the existing archive size/hash/inventory checks and interrupted-download recovery.

On each native build host, from the repository root:

```sh
uv run --locked --no-dev --group resource-build python scripts/prepare_desktop_product.py \
  --resources /absolute/producer-output \
  --candidate-sha256 <trusted-producer-candidate-sha256> \
  --output /absolute/product-resources \
  --version <desktop-X.Y.Z> \
  --server-url <approved-immutable-server-archive-https-url> \
  --allowed-origin <approved-https-origin> \
  --qualification <bundled-product-record-id>
```

Repeat `--allowed-origin` for redirects. Use a fresh output directory each time. The script verifies producer bindings, actual Server archive bytes, the complete Ops tree and executable, target identity, and the exact product qualification. It copies the helper, updates the catalog URL and qualification reference, pins the resulting catalog and Ops manifest, and atomically publishes the staging directory only after verification. Inputs remain unchanged. It emits:

- `ops/`, `server/catalog.json`, and `ops-provision.json` for the application resource directory.
- `tauri.product.conf.json`, with product identity `com.powercontext.desktop`, the selected Desktop version, and a directory mapping that preserves the complete Ops tree, including dotfiles. Keep the staging directory at its original absolute location through the build.
- `product-resources.json`, which records producer/resource bindings and the selected qualification. Signing and installed acceptance are explicitly `not_performed`; this is not a publication manifest.

From `desktop/`, build without `ci-fixtures` or preview features:

```sh
pnpm install --frozen-lockfile
pnpm tauri build --config /absolute/product-resources/tauri.product.conf.json --ci --no-sign -- --locked
```

Tauri automatically applies the checked-in macOS platform configuration. Windows uses the checked-in current-user NSIS configuration. Do not also merge `tauri.ops.conf.json`, which stages only pins for the legacy externally provisioned helper. An unsigned build checks assembly; it does not establish clean-machine installation, service lifecycle, or release qualification. Signing nested code changes hashes: regenerate producer inventories and bindings, stage final resources again, then sign the outer application. [#2108](https://github.com/oceanbase/powercontext/issues/2108) and [#2109](https://github.com/oceanbase/powercontext/issues/2109) own platform acceptance and signing; [#2112](https://github.com/oceanbase/powercontext/issues/2112) owns publication of final tested bytes.

## Runtime deployment and recovery

On first Ops use, native code verifies the application's `ops/` against its packaged manifest pin, then copies it into `PowerContext/Ops/bundles/<manifest-sha256>` under the OS current-user local data directory. It uses a private temporary directory and a cross-process lock, verifies the completed copy, and renames it into place. Concurrent provisioning returns `busy` for retry. The interpreter comes from that verified directory; no globally installed Python or `uv` is needed. Server installation and service changes still require the existing plan/approval flow and installation UI.

A valid matching cache is reused. A damaged cache is moved to `damaged-<uuid>` only after a replacement has passed verification. A damaged packaged resource fails closed, even if an older helper exists: reinstall a trusted Desktop package to repair the source. A package with no `ops/` retains the legacy `PowerContext/Ops/bundle` behavior. An empty or invalid `ops/` never selects that fallback.

Desktop removal must leave the independent helper copies, operation references/journals, Server registration, and user data intact. Old digest directories and damaged copies are retained; this implementation performs no automatic cleanup and does not authorize removal of potentially active versions. This costs one copy per distinct helper version plus any quarantined copies. Every launch reads complete resource inventories, including the packaged source and deployed helper; no startup performance budget or clean-machine qualification is claimed. Existing Ops download, repair, status, cancellation, and resume contracts remain unchanged.

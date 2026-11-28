---
title: Install the Gateway
description: Download, verify, and run a standalone Gateway without installing Rust.
---

# Install the Gateway

The Gateway is a separate executable named `pc-gateway`. It connects to a running PowerContext Server (Core);
installing the Python package does not install the Gateway. Releases built with the Gateway distribution workflow
attach four native archives to the same [PowerContext GitHub Release](https://github.com/oceanbase/powercontext/releases).
Older releases without these attachments require a source build. Select a release with all four archives and a
successful **Gateway distribution** verification; an existing tag alone does not mean publication has finished.

## Supported platforms

| System | Architecture | Archive target | Runtime baseline |
| --- | --- | --- | --- |
| Linux | x86_64 | `x86_64-unknown-linux-gnu` | glibc 2.28+ |
| Linux | arm64 | `aarch64-unknown-linux-gnu` | glibc 2.28+ |
| macOS | Intel | `x86_64-apple-darwin` | macOS 15+ |
| macOS | Apple Silicon | `aarch64-apple-darwin` | macOS 15+ |

Linux archives are not Alpine/musl builds. macOS archives are unsigned and not notarized; macOS may require
approval under its normal security controls. Windows archives and a Gateway container image are not provided.
CLI-backed subscription routes still require their provider CLI and sign-in on the Gateway host.

Linux release checks cover native Ubuntu 22.04 and Rocky Linux 8 container userspace on both architectures.
Containers share the runner's kernel, so these checks do not establish compatibility with historical kernels or
entire older distributions. Core's Python and SQLite requirements and each provider CLI's requirements apply
separately from the Gateway's glibc baseline.

### Windows

There is no native Windows executable in this release. The Gateway currently depends on Unix socket transport and
Unix signal handling; adding a Windows build runner alone does not provide Windows support.
On Windows, use a WSL2 Ubuntu 22.04 or newer distribution and follow the Linux instructions **inside WSL2**.
Run Core and any provider CLI in the same Linux environment. This uses the Linux package, not a native Windows build;
the distribution CI validates Ubuntu runners, not WSL2 host integration.

## Use the install script

Pass `--gateway` when installing PowerContext. The script installs the Gateway with the same version and performs the download and verification steps below:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

`pc-gateway` is installed in the same directory as `powercontext`. The version selected with `--version` also applies to the Gateway. `POWERCONTEXT_GATEWAY_DOWNLOAD_URL` names an HTTPS directory that holds the same archives and `.sha256` files, such as an internal mirror. On an unsupported platform, the script exits before installing anything. If the download or verification fails, the CLI is already installed, but the script does not install or replace `pc-gateway` and exits with a non-zero status. The option requires a local Server installation and cannot be combined with `--profile client`.

## Download and verify

The example selects 1.6.0. Use the same release version as Core. No Rust compiler or repository checkout is needed.
Download the archive and its checksum from the selected release, then verify before extracting or installing:

```bash
set -eu
version=1.6.0
case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) target=x86_64-unknown-linux-gnu ;;
  Linux/aarch64|Linux/arm64) target=aarch64-unknown-linux-gnu ;;
  Darwin/x86_64) target=x86_64-apple-darwin ;;
  Darwin/arm64) target=aarch64-apple-darwin ;;
  *) echo "Unsupported platform" >&2; exit 1 ;;
esac
archive="powercontext-gateway-$version-$target.tar.gz"
base="https://github.com/oceanbase/powercontext/releases/download/powercontext-v$version"
mkdir -p "gateway-$version-$target"
cd "gateway-$version-$target"
curl --fail --location --output "$archive" "$base/$archive"
curl --fail --location --output "$archive.sha256" "$base/$archive.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check "$archive.sha256"
else
  shasum -a 256 --check "$archive.sha256"
fi
tar -xzf "$archive"
./pc-gateway --version
mkdir -p "$HOME/.local/bin"
install -m 755 pc-gateway "$HOME/.local/bin/pc-gateway"
export PATH="$HOME/.local/bin:$PATH"
```

`--version` prints the PowerContext release version, full source commit and Rust target; `--help` works without Core.
The archive also contains `manifest.json` with the binary SHA256 and platform baseline, the project license, and
Gateway documentation. The Release provides `powercontext-gateway-SHA256SUMS` and
`powercontext-gateway-manifest.json` for all four platforms. Checksums detect changed bytes; they are not code signatures.
Add `$HOME/.local/bin` to your shell's PATH if it is not already present.

## Connect to Core

Start Core first using [Server deployment](deploy-server.md). For a standalone Gateway connected to Core over HTTP,
configure an accessible Scope and a matching approved upstream profile, then run:

```bash
PC_GATEWAY_CORE_BASE=http://127.0.0.1:8000 \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_DEFAULT_SCOPE=your-scope \
PC_GATEWAY_UPSTREAM_PROFILE=approved-provider \
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
pc-gateway
```

Core must run with `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and a supported authentication provider for every
Gateway deployment, including combined UDS and non-enforcing inference privacy modes. A Core with authentication/access
control disabled cannot authorize model sends. For a combined deployment, replace `PC_GATEWAY_CORE_BASE` with
`PC_GATEWAY_CORE_UDS=/path/to/core.sock`; Core must already be listening on that private socket.
`PC_GATEWAY_API_KEY` protects model routes and is required for every model listener, including loopback. It must be
different from `PC_GATEWAY_CORE_TOKEN`; it does not replace Core's credentials for forwarded API, MCP or Dashboard
routes. The selected `PC_GATEWAY_UPSTREAM_PROFILE` must already exist in Core and must contain the provider endpoint,
model and `openai-chat` protocol. For Responses, also set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to a Core target
with the `openai-responses` protocol. Do not set `PC_GATEWAY_UPSTREAM_URL*`, and remove `PC_GATEWAY_DEGRADE` if it has any
non-empty value; these legacy settings prevent startup.
`PC_GATEWAY_CORE_TOKEN` must identify a service principal, and `PC_GATEWAY_API_KEY` is required even on loopback and
must be different from the Core token. A dedicated Gateway principal needs `gateway.user` (`gateway.invoke`) on every
Scope selected with `X-PowerContext-Scope-ID`, not only `PC_GATEWAY_DEFAULT_SCOPE`, and `server.observer`
(`server.observe`) on the Server resource whose deployment ID is configured by
`POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` (default `powercontext`). With `PC_GATEWAY_SEDIMENT=on`, also grant
`scope.contributor` instead of `scope.viewer`; it includes `scope.read` and `scope.contribute`. With capture off,
`scope.viewer` is sufficient. For the built-in writable Access Provider, inspect the Gateway principal using
`GET /v1/access/me`, then have an Access administrator create the corresponding bindings through
`POST /v1/access/bindings/create`; see [Gateway authentication and target requirements](deploy-server.md#gateway-model-destination)
for the complete commands. The bootstrap Server token is an administrator credential with static Scope roles; in a
single-token setup, grant it `gateway.user` on every Scope clients select, not only the default Scope. Its admin
authority covers `server.observer`, and its static Scope roles cover the reader/contributor permission. The following
Scope examples show the invocation grant and the reader role for a dedicated identity:

```json
{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"your-scope"},"role":"gateway.user","idempotency_key":"gateway-user-your-scope"}
```

```json
{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"your-scope"},"role":"scope.viewer","idempotency_key":"gateway-viewer-your-scope"}
```

When `PC_GATEWAY_SEDIMENT=on`, use `scope.contributor` instead of `scope.viewer`; also create the `server.observer`
binding on `{"type":"server","deployment_id":"powercontext"}` (replace with the configured deployment ID).
Replace `server-token` with the service identity returned for `PC_GATEWAY_CORE_TOKEN`. The complete curl commands and
the bootstrap Server-token distinction are in [Gateway authentication and target requirements](deploy-server.md#gateway-model-destination).

`curl --fail http://127.0.0.1:8787/healthz` checks the Gateway listener only. In a combined UDS deployment, an authenticated
request to `/v1/server-info` through the Gateway should return the same Server identity as Core. Standalone HTTP mode
does not forward Core routes. Provider/model acceptance remains separate from startup and connection checks.

For an upgrade, stop the Gateway, download and verify the matching release, and replace the executable. Before restart,
remove any `PC_GATEWAY_UPSTREAM_URL*` settings, configure the approved Core profile and distinct mandatory Gateway/Core
tokens, and grant the Gateway service `gateway.invoke`, `server.observe`, and (when capture is enabled)
`scope.read` plus `scope.contribute` on their respective resources. Deployments that selected
different models per request need a separate Gateway instance for each model; provider endpoints must be directly
reachable without an ambient HTTP proxy. Follow the Server's database maintenance requirements separately. Keep the
previous binary for operational rollback; replacing a Gateway binary does not roll back Core's database.

## Build from source

Check out the matching release tag and run `cargo build --locked --manifest-path gateway/Cargo.toml --release -p pc-gateway-app`.
The executable is `gateway/target/release/pc-gateway`. Source builds without distribution metadata report the Cargo package
version and an unknown commit; official release builds embed the release identity. See the
[Gateway README](https://github.com/oceanbase/powercontext/blob/master/gateway/README.md) for development and tests.

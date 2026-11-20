# PowerContext Desktop preview

## Native macOS updates

The macOS **Check for Updates…** command uses Sparkle 2 for its native window, download lifecycle, Ed25519 verification, skipped versions, install-on-quit behavior and relaunch. Run `bash desktop/scripts/setup-sparkle.sh` to download and verify the pinned framework, then copy `desktop/src-tauri/Info.plist.example` to `Info.plist`, replace the HTTPS appcast URL and Sparkle public key, and build with `pnpm --dir desktop desktop:build:macos`. Keep the private key in the release Keychain or CI secret store; never commit it.

Sparkle does not create an updater during `tauri dev` because that process is not running from an `.app` bundle. Qualify the flow with an older signed and notarized application against the real appcast.

Internal Windows and macOS preview for [#1654](https://github.com/oceanbase/powercontext/issues/1654), following RFC #1455. It provides a packaged Tauri 2 shell with Home, Memories, Services & Agents, and bilingual Settings with light, dark, and system themes. Connection profiles, explicit activation, identity/readiness checks, exact Scope selection and local diagnostics are implemented. Shared note saving, bounded FTS search and exact-version reading are implemented; release qualification remains open.

See [中文说明](README.zh.md) and the [security boundary](SECURITY.md). This is not a supported or signed release, and does not close #1654 or #1428.

## Local Server preparation

Overview remains the initial page. Its **Get started with PowerContext** guide offers **View local setup** and **Add connection**. Local setup opens and focuses the same **Settings & diagnostics → Local Server** preparation page; installation and recovery remain unavailable in this preview pending [#2306](https://github.com/oceanbase/powercontext/pull/2306). Adding a connection opens the existing editor. A saved-profile selector opens the exact selected profile for review, checking and explicit activation; reopening never restores a private connection or Scope.

After activation, the guide opens the shared exact-Scope picker and continues to Memories only after an explicit successful selection. With a selected Scope, it displays the actual connection and full `scope_id`, opens the existing save/search/exact-read/copy workspace, and explains unavailable operations. The three steps describe the journey; they do not track first-memory completion, usage counts or a complete inventory. **Dismiss guide** collapses the tutorial while preserving next actions and recovery; **Revisit guide** restores it. This preference lasts only until Desktop closes. Keyboard focus, existing draft-discard guards, English/Chinese and theme settings apply to these entries.

In disconnected Memories, **Set up locally** opens and focuses **Settings & diagnostics → Local Server**; **Connect an existing Server** opens the existing connection editor. Selecting either path does not install software, activate a connection, run diagnostics or probe readiness.

The Local Server guidance is also available by opening Settings directly, including when native state is loading or unavailable. It explicitly marks Desktop installation, recovery and service management as unavailable; that status makes no claim about an actual local installation or running service. Settings stays at the bottom of the sidebar.

Prepare a Server independently using [Install and run](../docs/en/docs/get-started/install-and-run.md) and [Deploy the Server](../docs/en/docs/operate/deploy-server.md), observing their platform limits. Confirm the actual address, authentication and a qualified Desktop compatibility profile, then save and explicitly activate the connection and select an exact Scope. A running Server alone does not establish Desktop compatibility. Existing independently running Servers do not need to be reinstalled. The in-app Usage help provides a copyable address for this guide.

## Run the installed preview

Download and extract `desktop-windows-internal-unsigned` from a successful **Desktop validation** Actions run on this branch. The installer is under `src-tauri/target/release/bundle/nsis/`. Its accompanying `.artifacts/windows-smoke.json` records the commit, SHA-256 and signature status; compare the downloaded installer with `Get-FileHash -Algorithm SHA256 <installer-path>`. The current package is unsigned and intended for internal validation.

Install it, then open **PowerContext Desktop Preview** from the Windows Start menu. Vite, Python and a local Server are not prerequisites for launching the installed UI. No connection is active at startup; explicitly connect to an existing Server and select a Scope before saving or searching. Without a Server, the shell and settings remain available. Use the commands below for development mode.

Uninstall Desktop through Windows **Installed apps**. Uninstallation does not manage the independent Server, business database or Agent configuration; removing Server data is not part of removing Desktop.

Repeated launches activate the existing window for this channel. Profile storage is exclusively owned until the application exits, preventing concurrent instances from overwriting saved connections. Late save completions cannot clear the unsaved-input warning of a new editor or Scope.

## Build on Windows

Use Windows 11 x64, Visual Studio C++ Build Tools with a Windows SDK, Node **24.14.1**, pnpm **11.13.1**, Rust **1.95.0 MSVC**, **JDK 11 or newer with `java` available on `PATH`**, and WebView2. `desktop/.mise.toml`, `rust-toolchain.toml`, `pnpm-lock.yaml` and `src-tauri/Cargo.lock` pin the tools and dependencies independently of Python and the website.

The OpenAPI Generator used by `generate` and `generate:check` runs on Java. On the first run, `openapi-generator-cli` may download the pinned generator JAR from Maven Central, so the first generation or drift check requires network access. Subsequent runs can reuse the downloaded JAR.

Some machines default to Rust's GNU host. In PowerShell, explicitly select the pinned MSVC host for this process:

```powershell
rustup toolchain install 1.95.0-x86_64-pc-windows-msvc --profile minimal --component clippy --component rustfmt
$env:RUSTUP_TOOLCHAIN = '1.95.0-x86_64-pc-windows-msvc'
pnpm --dir desktop install --frozen-lockfile
pnpm --dir desktop desktop:dev
```

From the repository root:

```powershell
pnpm --dir desktop lint
pnpm --dir desktop typecheck
pnpm --dir desktop test
pnpm --dir desktop build
pnpm --dir desktop ipc:check
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo clippy --locked --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
pnpm --dir desktop desktop:build
```

`build` produces UI assets only. `desktop:build` produces the release executable and current-user NSIS installer under `desktop/src-tauri/target/release/bundle/nsis/`. No Python, private HTTP server, or Vite process is embedded or started by the installed application. Closing the window exits Desktop.

The unsigned installer is for internal verification. If WebView2 is absent, its configured download bootstrapper needs network access and may require the user to complete Microsoft installation prerequisites. The absent-runtime and standard-user cases require a disposable Windows environment; never remove a developer's WebView2 to simulate them.

## Connect an existing Server

Choose **Connect an existing service** on Home or in **Services & Agents**. Enter the service address and choose its authentication mode. Use HTTPS for remote services and a literal loopback address for local HTTP. Choose a [verified compatibility profile](src-tauri/src/connections/compatibility.json) that matches the Server deployment. Bearer credentials can stay in the system vault or be limited to the current session.

Confirm the connection, then use the space menu in the top bar to select an authorized space. The Desktop application verifies the target and credentials before enabling qualified operations.

## Save, search, and read a memory

In **Memories**, choose **Record memory** and review the service and space before saving. Enter a keyword to search the selected space. The service returns at most ten matches. Open a result to read the exact version it names. If a save has an unknown outcome, search the original space to check whether it was recorded before trying again.

## Explicit readiness and connection identity

Saving, checking, activating, refreshing and ordinary Scope/Memory operations do not probe model or dependency readiness.
After checking a saved profile, **Probe readiness** explains and performs the optional diagnostic explicitly. Its timestamp
is separate from the connection check. A failed probe does not grant or revoke business permissions, activate a profile,
change the Scope, or discard input. While a probe is pending, independently authorized Scope and Memory operations remain
available, including after leaving Connections. Its diagnostic error stays associated with the probed profile revision.
Opening a page and reopening Desktop do not run this action.

The public `get_connection_identity` operation reports the actual deployment, request Principal, authentication state
and Access mode without readiness probes or `server.observe`. Anonymous loopback profiles require matching qualification
for that operation and explicit disabled authentication/Access with no Principal. A generic 503, selected no-token profile,
or previous readiness result cannot establish anonymous identity. Older Servers without this qualified operation require
an upgrade for anonymous use; independently qualified authenticated legacy profiles retain Principal revalidation.
Identity/authentication failures do not fall back. Changes to deployment, Principal, authentication or Access mode invalidate
the active context. Denied capability observations remain independent of qualified, individually authorized Memory operations.

## Current-Scope inventory and usage

The usage cards on **Home** show statistics for the current space when the active connection supports them.
The view displays the Server timestamp, Scope coverage, inventory snapshot,
recorded UTC model usage and recall token estimates. Missing token counts remain
**Unknown**; estimates are not billing savings or quality scores. Every chart has
an equivalent table and keyboard-scrollable region. A statistics error does not
disable independent Memory actions. See the [developer contract](../docs/en/development/desktop-statistics.md).

For Server source `7d89a97a`, select `sqlite-identity-stats-7d89a97a-v1` to qualify identity,
Memory and statistics together against the same pinned wheel.

## Native discovery results

Connection checks call the public `get_server_info` operation through the existing native transport. `CheckReport.discovery` reports `supported`, `missing`, `denied`, `unavailable`, `invalid`, or `incompatible`, the observed Server metadata, safe errors, and per-operation `qualified`/`unsupported`/`unverified` results with reasons. Compatible discovery minor versions accept additional optional fields in the response and its nested version/feature objects.

Qualification requires an explicitly selected profile that matches the bundled contract. A profile can also list the exact Desktop contract digest among its supported Bearer clients. This path requires Bearer mode and a valid Principal and uses the profile's recorded operation set. A discovery 404 permits that profile's qualified legacy operations; 401, 403, 503, malformed responses and incompatible products/required majors do not select a legacy fallback. Missing feature groups, unsupported feature versions and absent operation IDs restrict the affected operations. Exact Artifact reads require their own qualification; the `memory.explicit` group does not establish that support. Discovery metadata never grants authorization or attests a remote binary.

Current profiles require `get_connection_identity`; a missing advertised identity operation cannot downgrade them to legacy Principal checks. A qualified observation-only 403 preserves independently authorized operations once that identity succeeds. Scope listing/default lookup/selection, Memory operations and statistics revalidate deployment, Principal, authentication and Access mode through that identity endpoint before dispatch and before returning private results. Changed or failed identity clears the active context, invalidating Scope selections, cursors and pending reads. Legacy profiles retain their discovery and Principal fences. Reconnect explicitly to establish a new context. Package version changes alone do not change deployment identity.

Submitted writes retain their original endpoint, `serverId` (null for qualified legacy Servers), Principal, Scope and generation. If the post-write identity check fails, a successful HTTP reply is treated as an unknown outcome: private bodies/references are withheld and the write is never automatically replayed. The original deployment is shown in write recovery feedback. The submitted input survives that disconnection as a read-only recovery draft in the current window, with explicit copy/discard actions; it is never inserted into a new target's form or persisted. Closing the window loses it. Confirmed navigation discards it, and a late response cannot restore an explicitly discarded draft. A late write cannot invalidate a newly activated context. Qualified legacy sessions require discovery to remain 404; discovery appearing requires explicit rechecking rather than silently acquiring a deployment identity. A failed connection recheck removes the previous cached report instead of leaving old verification visible.

Desktop retains each submitted input in application memory from the start of the native call, independently of the form. An automatic disconnection during a concurrent read therefore leaves a read-only copy even while the write is pending. Recovery distinguishes pending, unknown, rejected and attempts without a receipt. A retry does not discard earlier uncertain input, including when that retry fails before its mutation reaches the Server. Each attempt has its own local identity: only a successful receipt clears its input, rejected saves keep their text for explicit recovery, and explicit discard removes only the selected copy (or all copies when confirming navigation). Late receipts cannot restore discarded inputs or clear another attempt. Inputs are limited to 8192 UTF-8 bytes per attempt; copies remain for pending, uncertain or rejected attempts until success, explicit discard or window close. They contain user input and observed original-target metadata, not returned private Server bodies, and are never replayed or persisted.

These checks observe identity around requests; the public mutation contract has no conditional Server-identity precondition. They cannot atomically prevent an endpoint from changing between identity verification and dispatch, or detect a replacement that changes back between observations. Legacy Servers have no durable deployment fence. Discovery does not attest binary identity. Connection checks and ordinary identity revalidation do not probe readiness; the separate diagnostic action is explicit.

Connections displays discovery separately from the compatibility verdict, including qualified observation denial and the reason for an incompatible product or major version. Other failed discovery checks keep business operations unavailable without removing the selected qualification record. Correct the connection or service and explicitly recheck. Only a discovery 404 permits the qualified legacy policy described above. A pending readiness probe does not block checking or activating another connection; switching context invalidates the old diagnostic result without promising to stop work already running on the Server.

Save, search, exact revision reads, Scope lookup and Scope selection each require their own entry in `supportedOperations`; the aggregate compatibility verdict does not enable other operations. An unavailable action explains missing features, unsupported feature versions, missing operation support or missing independent qualification. Independently supported actions remain usable, and Overview shows availability for each Memory feature. Native request-time authorization and compatibility checks remain authoritative.

## Contracts and resources

`pnpm --dir desktop generate` derives TypeScript schemas, the reviewed thirteen-operation manifest, and the Rust wire models from `openapi/powercontext.yaml`; `generate:check` detects drift, including a normalized contract SHA-256. The OpenAPI Generator CLI version is pinned in `desktop/openapitools.json`, and the limited Rust customization lives in `desktop/scripts/openapi-generator/rust/`. Generated `src-tauri/src/transport/wire.rs` must not be edited by hand: after changing the contract or generator template, run `pnpm --dir desktop generate`, then run `pnpm --dir desktop generate:check` in CI. Typed Rust adapters consume that generated manifest and generated wire schemas. No public route is independently handwritten in the application. The manifest is not an IPC permission grant.

`cargo run --locked --manifest-path desktop/src-tauri/Cargo.toml --example export_ipc` derives the TypeScript IPC request/receipt/error types from Rust. `ipc:check` verifies them. IPC exposes typed connection, Scope, memory and diagnostic operations to the main window only; no generic fetch, shell, file, database or secret-read command is granted.

The canonical brand source is `website/assets/powercontext-color.png`, which is read directly without running the website. `pnpm --dir desktop icons` extracts its square mark and uses the pinned Tauri CLI to derive the Windows icon; `icons:check` verifies all three generated assets against the canonical source. UI SVGs originate from the repository's Desktop design assets and are promoted into `ui/src/assets/` for reproducible builds. They are project resources under the repository Apache-2.0 license.

## Native credential feasibility

```powershell
cargo run --locked --manifest-path desktop/src-tauri/Cargo.toml --example credential_probe
```

This explicit probe creates a uniquely named synthetic credential in the preview namespace, loads it in another process and deletes it. It never reads another application's credential or prints a secret. Normal tests simulate unavailable storage and require an explicit session-only choice; a native-store failure never downgrades a persistent request or writes the secret to profile JSON. Do not treat the probe as end-to-end S2 credential-form verification.

The Windows CI workflow runs on PRs and the preview branch, verifies native vault persistence, builds an unsigned internal installer, and tests Chinese-path installation/uninstallation on its disposable runner. It uploads the installer and a JSON smoke report. CI and mocked IPC tests do not prove clean-machine installation, notification activation, independent service login or Agent capture/recall.

## Local CLI diagnostics

Settings provides explicit local service and Agent integration checks. They remain separate from the active remote connection; a missing local CLI does not disable remote operations. Integration checks may start temporary Agent helpers and do not prove capture/recall.

Register an explicitly trusted local installation in `%APPDATA%/com.powercontext.desktop.preview/diagnostic-cli.json`:

```json
{
  "executable": "C:/trusted/powercontext/Scripts/powercontext.exe",
  "sha256": "REPLACE_WITH_VERIFIED_64_CHARACTER_SHA256",
  "source": "explicit_local_installation"
}
```

On Linux, put the same file at `$XDG_DATA_HOME/com.powercontext.desktop.preview/diagnostic-cli.json`; when `XDG_DATA_HOME` is unset, this resolves to `~/.local/share/com.powercontext.desktop.preview/diagnostic-cli.json`. The registered executable must be the absolute path to the `powercontext` file (not `powercontext.exe`), and the `sha256` value must be the verified 64-character digest. The `source` must be `explicit_local_installation`.

The native adapter verifies the absolute executable path and pinned digest before running either `service status --json` or `doctor codex claude-code dsh openclaw opencode pi hermes workbuddy kimi-code zcode --json`. Registration does not require a version; legacy `version` fields are ignored. Compatibility is determined by the diagnostic response matching the adapter's supported output schema. The observed CLI version is collected for display after a valid diagnostic response; an unavailable version is shown as unknown and does not reject the response. This is a local installation pin, not publisher-signature verification; the Python environment and dependencies must also be trusted. Update the registered digest if the executable changes. No PATH-first CLI selection or renderer-provided commands are accepted.

The optional version query has a 15-second deadline, service status 20 seconds, and integration diagnostics 60 seconds. Each invocation limits combined stdout/stderr to 256 KiB. Windows helpers run hidden in an owned Windows Job; Linux and macOS helpers run in a private POSIX process group. Completion, timeout and cancellation clean up the helper and its descendants without affecting unrelated processes. Only allowlisted status fields reach the UI. Valid unhealthy JSON remains useful even with exit code 1.

## Windows installer smoke test

Windows GitHub Actions builds an unsigned NSIS installer and installs it in a temporary path containing Chinese characters. A matching Microsoft-signed WebDriver checks that the packaged WebView2 renders the first-run page. The runner then uninstalls Desktop and checks that a file outside the installation directory is intact.

The `desktop-windows-internal-unsigned` artifact includes the installer, smoke reports, screenshot, and driver logs. To repeat the smoke test with an existing installer from the preview workflow, dispatch `Desktop validation` with its `installer_run` ID. The job checks the source commit, size, and SHA-256 before installing the package.

Connection, memory save, search, and exact readback are covered by the separate [native SQLite Server probe](tests/real_server.py).

With a matching client contract, `sqlite-atomic-112db11e-v1` and `sqlite-atomic-3cefa45a-v1` remain available in Bearer mode, preserving saved selections and Principal revalidation. Anonymous use requires a Server upgrade. A saved incompatible selection remains visible until explicitly replaced. For Server source `7d89a97a`, choose `sqlite-identity-stats-7d89a97a-v1`, whose recorded source, wheel and contract must match the actual deployment, then check and explicitly activate the connection. The `ci-fixtures` profiles are for disposable tests.

## Build on macOS

Use macOS 12 or later, Xcode Command Line Tools (`xcode-select --install`), Node **24.14.1**,
pnpm **11.13.1**, Rust **1.95.0**, and **JDK 11 or newer with `java` available on `PATH`**.
Build on the target architecture (Apple Silicon or Intel).

`generate:check`, included in `lint` and `build`, runs the Java OpenAPI Generator. The first run
may download its pinned JAR from Maven Central and requires network access; later runs can reuse it.

```sh
cd desktop
pnpm install --frozen-lockfile
pnpm desktop:dev
# Or build the app bundle and disk image:
bash scripts/setup-sparkle.sh
pnpm desktop:build:macos
```

The app is in `src-tauri/target/release/bundle/macos/`; the installer is in
`src-tauri/target/release/bundle/dmg/`. Open the DMG and drag the app to Applications.
Launch **PowerContext Desktop**. An independent Server is required for memory operations.
Persistent Bearer credentials use macOS Keychain; session-only storage remains an explicit option.
Removing the app does not remove Server data or Keychain credentials. Remove saved connection profiles
in the app first if their credentials must also be deleted.

Builds are unsigned internal previews. Developer ID signing, notarization, clean-machine Gatekeeper,
accessibility, actual IME input and installed-app Keychain prompts require separate qualification.
The macOS CI jobs test Apple Silicon and Intel builds; their existence is not evidence of a passed run.

macOS local diagnostics read `diagnostic-cli.json` under
`~/Library/Application Support/com.powercontext.desktop.preview/`. Register an absolute executable
named `powercontext`, its SHA-256 digest, and `source: "explicit_local_installation"`, using the same
JSON contract as Windows. A renderer cannot supply the executable or arguments. The native adapter
executes a private read-only copy of the verified launcher. Use a self-contained executable or a
launcher with an absolute interpreter path; launchers that locate dependencies relative to themselves
are unsuitable. The CLI must not depend on a shell profile or an interactive terminal.
Diagnostics keep the existing output limits and deadlines and terminate their private process group
on completion, timeout and cancellation. Before normal application exit, Desktop rejects new diagnostics,
cancels active work and waits for process-group cleanup and launcher reaping. The app does not install or manage a LaunchAgent.

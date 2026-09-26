# PowerContext Desktop preview

Internal Windows preview for [#1654](https://github.com/oceanbase/powercontext/issues/1654), following RFC #1455. It provides a packaged Tauri 2 shell with Home, Connections, My memories, Chinese/English settings, semantic light/dark/system themes and honest disconnected states. Connection profiles, explicit activation, identity/readiness checks, exact Scope selection and local diagnostics are implemented. Shared note saving, bounded FTS search and exact-version reading are implemented; native workflow qualification remains open.

See [中文说明](README.zh.md), [security boundary](SECURITY.md), and [validation guide](VALIDATION.md). This is not a supported or signed release, and does not close #1654 or #1428.

## Run the installed preview

Download and extract `desktop-windows-internal-unsigned` from a successful **Desktop validation** Actions run on this branch. The installer is under `src-tauri/target/release/bundle/nsis/`. Its accompanying `.artifacts/windows-smoke.json` records the commit, SHA-256 and signature status; compare the downloaded installer with `Get-FileHash -Algorithm SHA256 <installer-path>`. The current package is unsigned and intended for internal validation.

Install it, then open **PowerContext Desktop Preview** from the Windows Start menu. Vite, Python and a local Server are not prerequisites for launching the installed UI. No connection is active at startup; explicitly connect to an existing Server and select a Scope before saving or searching. Without a Server, the shell and settings remain available. Use the commands below for development mode.

Uninstall Desktop through Windows **Installed apps**. Uninstallation does not manage the independent Server, business database or Agent configuration; removing Server data is not part of removing Desktop.

## Build on Windows

Use Windows 11 x64, Visual Studio C++ Build Tools with a Windows SDK, Node **24.14.1**, pnpm **11.13.1**, Rust **1.95.0 MSVC**, and WebView2. `desktop/.mise.toml`, `rust-toolchain.toml`, `pnpm-lock.yaml` and `src-tauri/Cargo.lock` pin the tools and dependencies independently of Python and the website.

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

1. Open Connections and add a named profile. HTTP is restricted to literal loopback hosts; other addresses require HTTPS. A reverse-proxy path prefix is preserved.
2. Explicitly choose unauthenticated loopback access or Bearer authentication. Bearer storage is either Windows Credential Manager or this session only. Trust/address changes require credential reconfiguration. An optional CA augments system trust without disabling certificate checks.
3. Select a qualified compatibility profile after comparing its tested build with your deployment. The selection does not prove the remote binary identity. See [validation guide](VALIDATION.md) for the exact fixture and supported combinations.
4. Save, then explicitly use the connection. Merely selecting a saved profile does not activate it. Review liveness, readiness, identity and capabilities separately; none implies resource authorization.
5. Find an authorized Scope by title (50 per page), inspect a default suggestion, or enter an exact Scope ID. Selection never creates a Scope or changes Agent bindings. Editing a query cancels its old read; connection and identity changes invalidate old results.

Profiles persist under the app data directory; credentials never appear in profile JSON. Active authorization, session-only credentials, Scope selection and query/results are not restored as an authenticated offline session. Remove a profile to remove its Desktop configuration and owned credential reference; it does not stop the Server or remove business data.

## Save, find and read a note

After explicitly activating a qualified connection and choosing a Scope, use Home's note form or **My memories → Add note**. The target connection and exact Scope are shown before submission. Enter inserts a newline; only the save button submits. The input is plain text, limited conservatively to 8192 raw UTF-8 bytes without truncation. The Server owns normalization and the returned text is authoritative.

Search uses FTS in the selected Scope and returns at most 10 matches. This is not a full directory or history, and ten matches do not establish a total count. **Read exact version** sends the complete returned citation; it never substitutes the latest version. Copy buttons explicitly copy either the full plain text or citation JSON.

A successful save without an entry is reported as an operation success without inventing a citation. A timeout or interrupted dispatched write is **unknown**, not a safe invitation to retry: inspect the original Server/Scope before deciding whether to submit again. Identical text alone cannot identify that operation. Desktop does not automatically replay writes or keep an offline queue. Switching context hides old results while retaining minimal original-operation metadata for this session. See [validation guide](VALIDATION.md) for tested behavior and qualification gaps.

## Contracts and resources

`pnpm --dir desktop generate` derives TypeScript schemas and the reviewed ten-operation manifest from `openapi/powercontext.yaml`; `generate:check` detects drift, including a normalized contract SHA-256. Typed Rust adapters consume that generated manifest and generated wire schemas. No public route is independently handwritten in the application. The manifest is not an IPC permission grant.

`cargo run --locked --manifest-path desktop/src-tauri/Cargo.toml --example export_ipc` derives the TypeScript IPC request/receipt/error types from Rust. `ipc:check` verifies them. IPC exposes typed connection, Scope, memory and diagnostic operations to the main window only; no generic fetch, shell, file, database or secret-read command is granted.

The canonical brand source is `website/assets/powercontext-color.png`, which is read directly without running the website. `pnpm --dir desktop icons` extracts its square mark and uses the pinned Tauri CLI to derive the Windows icon; `icons:check` verifies all three generated assets against the canonical source. UI SVGs originate from the repository's Desktop design assets and are promoted into `ui/src/assets/` for reproducible builds. They are project resources under the repository Apache-2.0 license.

## Native credential feasibility

```powershell
cargo run --locked --manifest-path desktop/src-tauri/Cargo.toml --example credential_probe
```

This explicit probe creates a uniquely named synthetic credential in the preview namespace, loads it in another process and deletes it. It never reads another application's credential or prints a secret. Normal tests simulate unavailable storage and require an explicit session-only choice. Do not treat the probe as end-to-end S2 credential-form verification.

The Windows CI workflow runs on PRs and the preview branch, verifies native vault persistence, builds an unsigned internal installer, and tests Chinese-path installation/uninstallation on its disposable runner. It uploads the installer and a JSON smoke report. CI and mocked IPC tests do not prove clean-machine installation, notification activation, independent service login or Agent capture/recall.

## Local CLI diagnostics

Settings provides explicit local service and Agent integration checks. They remain separate from the active remote connection; a missing local CLI does not disable remote operations. Integration checks may start temporary Agent helpers and do not prove capture/recall.

Register an explicitly trusted local installation in `%APPDATA%/com.powercontext.desktop.preview/diagnostic-cli.json`:

```json
{
  "executable": "C:/trusted/powercontext/Scripts/powercontext.exe",
  "sha256": "REPLACE_WITH_VERIFIED_64_CHARACTER_SHA256",
  "version": "1.0.1.dev61+g63f918b7e.d20260919",
  "source": "explicit_local_installation"
}
```

The native adapter checks the absolute executable path, pinned digest and fixed `--version` result before running either `service status --json` or `doctor integrations --json`. This is a local installation pin, not publisher-signature verification; the Python environment and dependencies must also be trusted. The adapter admits 1.0.1 and 1.1.1 and their development builds; the registration must pin the exact installed version. The example identifies the tested baseline. Other version series require adapter qualification. No PATH-first CLI selection or renderer-provided commands are accepted.

Version verification has a 15-second deadline, service status 20 seconds, and integration diagnostics 60 seconds. Each invocation limits combined stdout/stderr to 256 KiB. Helpers run hidden in an owned Windows Job; completion, timeout and cancellation clean up their descendants. Only allowlisted status fields reach the UI. Valid unhealthy JSON remains useful even with exit code 1. Isolated real-CLI checks pass; installed-application qualification remains open in [validation guide](VALIDATION.md).

[Validation guide](VALIDATION.md) describes reproducible checks and outstanding platform and product gates.

## Remote installed-package acceptance

Windows GitHub Actions builds an unsigned installer, installs into a temporary Chinese path, and uses a matching Microsoft-signed WebDriver to operate the actual installed WebView2 page. An independent SQLite Server with synthetic data supports explicit connection activation, exact Scope selection, multiline Chinese note save, FTS search, exact reading, and paste-back verification of copied text and citation. The fixture Server and its temporary workspace are cleaned up afterward.

Reports, a screenshot and driver logs accompany the installer in the `desktop-windows-internal-unsigned` artifact. Failed or pending steps are not acceptance passes. The UI script permits only GitHub Windows runners and does not operate your local desktop. Hosted runners do not establish clean standard-user Windows 11, actual IME, screen-reader or Agent-host qualification.

To diagnose installed UI tests, manually dispatch `Desktop validation` with `installer_run` set to an existing Desktop CI run ID. It verifies the original package commit, digest and size before reusing that exact installer, and reports harness and installer commits separately. This diagnostic run does not replace final full-build acceptance.

Installed UI acceptance also checks content isolation between two independent connections, disconnect/reconnect, Server data preservation after removing an inactive profile, and the unknown outcome without replay after a real committed write loses its response. Credit each scenario only when its matching run report passes.

The CI-only lifecycle scenario forcibly ends its own installed Desktop process after saving a synthetic note, then checks that the independent Server still serves the original exact entry and accepts a new readable write. Consult the matching lifecycle report for its result; it does not simulate normal window closure or uninstall preservation.

Installed boundary checks exercise an 8192-byte Unicode note, reject over-budget input, display zero and capped-ten search results, and verify cancel/confirm behavior when disconnecting with an unsaved draft. Each result requires its matching remote report.

After upgrading from the earlier preview, select `sqlite-1.1.1-v1` and recheck your connection. The previous `sqlite-63f918b7-v1` selection is not silently upgraded to a different contract. See [current qualification](VALIDATION.md).

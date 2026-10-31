# PowerContext Desktop preview

Internal Windows and macOS preview for [#1654](https://github.com/oceanbase/powercontext/issues/1654), following RFC #1455. It provides a packaged Tauri 2 shell with Home, Connections, My memories, Chinese/English settings, semantic light/dark/system themes and honest disconnected states. Connection profiles, explicit activation, identity/readiness checks, exact Scope selection and local diagnostics are implemented. Shared note saving, bounded FTS search and exact-version reading are implemented; release qualification remains open.

See [中文说明](README.zh.md), [security boundary](SECURITY.md), and [validation guide](VALIDATION.md). This is not a supported or signed release, and does not close #1654 or #1428.

## Observation

The sidebar provides Overview, Connections, Memories, and Observation, with Settings & diagnostics at the bottom. Overview is the initial page. Observation contains Runtime state, Current-Scope inventory & usage, and This session tabs. Their data views are explicitly unavailable until integrated; opening the page or switching tabs does not run connection checks or local diagnostics. English/Chinese and existing theme settings apply to Observation.

The native host collects a bounded, in-memory window of HTTP requests and Memory/Scope user operations independently of page navigation. Trusted typed IPC provides an allowlisted summary, reset and safe JSON preview. The window retains at most 512 terminal records; it preserves cancellation and unknown-write outcomes and contains no content or target identities. See the [session observation contract](../docs/en/development/desktop-session-observations.md) for measurement units, coverage, reset and failure isolation. The This session page and clipboard flow require separate UI integration.


## Shared feedback and current context

Every page uses the same loading, empty, unavailable and error presentation. Loading local state is distinct from being disconnected; a local-state failure offers **Reload local state**, without probing Server readiness. Empty search results mean no matches for that query, not an empty Scope inventory. Unimplemented Observation views remain unavailable.

Errors explain the affected operation and recovery. Read failures offer an explicit retry; exact reads retain the original Artifact revision. An expired Scope cursor restarts from the first page. Credential, permission and compatibility failures explain the required configuration or authorization change. Save failures keep the draft; unknown writes retain their original target and require the existing confirmation before another submission. Writes are never replayed automatically. Local diagnostic failures offer a retry of the same selected local check and do not indicate remote unavailability.

Draft discard, connection removal and resubmission after an unknown write use an application-owned confirmation dialog in both languages. Cancel is focused initially; Escape or the backdrop cancels, Tab remains within the dialog, and confirming performs only the requested action. The same modal behavior applies to Scope selection. Closing Scope selection restores its actual trigger, or the page heading when that trigger is unavailable.

The global bar shows the active connection, full Server address, Scope title and complete `scope_id` on every page. Identically named Scopes remain distinguishable. Identity, compatibility, liveness and readiness are separate facts; the latest check for the active configuration revision supplies the displayed facts and check time. A healthy Server does not grant operation permissions. Addresses and IDs wrap in small windows. The connection menu supports arrow keys, Home/End and Escape; the Scope dialog constrains focus and restores it when closed.

Settings & diagnostics includes bilingual usage guidance, a locale-specific documentation address and a fixed issue-report address. Copy actions report success or failure; the read-only address fields select their text on focus for manual copying. Help is available without a Server or native connection state. The displayed Desktop version comes from the native host and is Unknown if unavailable.

While a connection operation is pending, a global message remains visible across page changes. You can browse other pages, but Memory editing, saving, searching, exact reads and Scope switching pause until the operation and its native-state refresh finish. This prevents a late activation result from clearing text entered against the old Scope. Once the operation completes, explicitly select an exact Scope if needed. Existing draft confirmations and unknown-write protections continue to apply.

Selecting a saved profile in the global connection menu opens that exact configuration in the editor, including when Connections is already open. Leaving an unsaved editor or Memory draft requires the existing discard confirmation; cancelling preserves the current editor and text. Selecting a configuration never checks or activates it. Explicitly reselecting the same profile after confirming discard reloads its saved values.

Scope selection also keeps an application-wide pending state. Escape, the close button and the backdrop can close the dialog, but do not cancel an accepted native selection. Memory editing and further Scope switching stay paused until it finishes. Closing a dialog whose trigger is disabled returns focus to the page heading. A late result still updates authoritative native state, while it cannot clear a new connection editor's draft guard. Failures after the dialog has closed appear in the shared error feedback.

## Existing Server connection journey

Saving, checking and activating a profile are separate actions with distinct progress and completion feedback. Saving does not activate a connection; a completed check asks users to review its individual facts. A compatible activation with a verified identity opens the shared exact-Scope picker. Lookup and selection retain their individual operation restrictions. Select the intended Server-returned Scope, using its complete ID to distinguish duplicate titles; the ID also supplies the button's accessible description. The activation guide then opens Memory and focuses its page heading. Closing the guide restores the activation button without choosing a Scope.

Opening the global Scope picker elsewhere keeps the current page after selection and restores focus to its selector. Cancelling draft confirmation preserves the current input. Confirming a successful Scope switch discards the old Memory or connection-editor draft. Native states rejected as stale cannot open the activation guide or finish Scope navigation. Empty results offer another title, an access request or another connection; loading, permission, authentication and compatibility failures remain separate.

Reopening Desktop loads saved profiles without activating a private session. The local setup entry continues to point to Local Server guidance; approved installer results and default-Scope handoff remain owned with #2117.

## Local Server preparation

Overview remains the initial page. In disconnected Memories, **Set up locally** opens and focuses **Settings & diagnostics → Local Server**; **Connect an existing Server** opens the existing connection editor. Selecting either path does not install software, activate a connection, run diagnostics or probe readiness.

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

1. Open Connections and add a named profile. HTTP is restricted to literal loopback hosts; other addresses require HTTPS. A reverse-proxy path prefix is preserved.
2. Explicitly choose unauthenticated loopback access or Bearer authentication. Bearer storage uses Windows Credential Manager, macOS Keychain, or Linux Secret Service (GNOME Keyring/KWallet); session-only storage is also available only when explicitly selected. If the native credential store is unavailable or locked, saving fails without writing the secret to profile JSON; choose session-only storage or cancel. Trust/address changes require credential reconfiguration. An optional CA augments system trust without disabling certificate checks.
3. Select a qualified compatibility profile after comparing its tested build with your deployment. The selection does not prove the remote binary identity. See [validation guide](VALIDATION.md) for the exact fixture and supported combinations.
4. Save, then explicitly use the connection. Merely selecting a saved profile does not activate it. Review liveness, readiness, identity and capabilities separately; none implies resource authorization.
5. Open the Scope dropdown and find an authorized Scope by title (50 per page). Selecting a result updates the active Scope and closes the dialog. Selection never creates a Scope or changes Agent bindings. Editing a query cancels its old read; connection and identity changes invalidate old results.

Profiles persist under the app data directory; credentials never appear in profile JSON. Active authorization, session-only credentials, Scope selection and query/results are not restored as an authenticated offline session. Remove a profile to remove its Desktop configuration and owned credential reference; it does not stop the Server or remove business data.

## Save, find and read a note

After explicitly activating a qualified connection and choosing a Scope, use Home's note form or **My memories → Add note**. The target connection and exact Scope are shown before submission. Enter inserts a newline; only the save button submits. The input is plain text, limited conservatively to 8192 raw UTF-8 bytes without truncation. The Server owns normalization and the returned text is authoritative.

Search uses FTS in the selected Scope and returns at most 10 matches. This is not a full directory or history, and ten matches do not establish a total count. **Read exact version** reads the returned Atomic Memory reference (`{family: "atomic-memory", artifact_id, revision}`) at that exact revision; it never substitutes the latest version. Copy buttons explicitly copy either the full plain text or the reference JSON.

A successful save that returns no memory record is reported as an operation success without inventing a reference. A timeout or interrupted dispatched write is **unknown**, not a safe invitation to retry: inspect the original Server/Scope before deciding whether to submit again. Identical text alone cannot identify that operation. Desktop does not automatically replay writes or keep an offline queue. Switching context hides old results while retaining minimal original-operation metadata for this session. See [validation guide](VALIDATION.md) for tested behavior and qualification gaps.

## Native discovery results

Connection checks call the public `get_server_info` operation through the existing native transport. `CheckReport.discovery` reports `supported`, `missing`, `denied`, `unavailable`, `invalid`, or `incompatible`, the observed Server metadata, safe errors, and per-operation `qualified`/`unsupported`/`unverified` results with reasons. Compatible discovery minor versions accept additional optional fields in the response and its nested version/feature objects.

Qualification still requires an explicitly selected native record matching the bundled contract. A discovery 404 permits that record's independently qualified legacy operations; 401, 403, 503, malformed responses and incompatible products/required majors do not select a legacy fallback. Missing feature groups, unsupported feature versions and absent operation IDs restrict the affected operations. Exact Artifact reads require their own qualification; the `memory.explicit` group does not establish that support. Discovery metadata never grants authorization or attests a remote binary.

Connection checks and anonymous identity revalidation still perform readiness probes. The observed `server_id` is recorded with the check; deployment-aware context fencing and explicit readiness separation are not established by this discovery result.

Connections displays discovery separately from the compatibility verdict, with guidance for denied or temporarily unavailable discovery and the reason for an incompatible product or major version. A failed discovery check keeps business operations unavailable; it does not remove the selected qualification record. After correcting the connection or service, explicitly check the connection again to reassess operation availability. Only a discovery 404 permits the qualified legacy policy described above.

Save, search, exact revision reads, Scope lookup and Scope selection each require their own entry in `supportedOperations`; the aggregate compatibility verdict does not enable other operations. An unavailable action explains missing features, unsupported feature versions, missing operation support or missing independent qualification. Independently supported actions remain usable, and Overview shows availability for each Memory feature. Native request-time authorization and compatibility checks remain authoritative.

## Contracts and resources

`pnpm --dir desktop generate` derives TypeScript schemas, the reviewed eleven-operation manifest, and the Rust wire models from `openapi/powercontext.yaml`; `generate:check` detects drift, including a normalized contract SHA-256. The OpenAPI Generator CLI version is pinned in `desktop/openapitools.json`, and the limited Rust customization lives in `desktop/scripts/openapi-generator/rust/`. Generated `src-tauri/src/transport/wire.rs` must not be edited by hand: after changing the contract or generator template, run `pnpm --dir desktop generate`, then run `pnpm --dir desktop generate:check` in CI. Typed Rust adapters consume that generated manifest and generated wire schemas. No public route is independently handwritten in the application. The manifest is not an IPC permission grant.

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

The native adapter verifies the absolute executable path and pinned digest before running either `service status --json` or `doctor codex claude-code dsh openclaw opencode pi hermes workbuddy kimi-code zcode --json`. Registration does not require a version; legacy `version` fields are ignored. Compatibility is determined by the diagnostic response matching the adapter's supported output schema. The observed CLI version is collected for display after a valid diagnostic response; an unavailable version is shown as unknown and does not reject the response. This is a local installation pin, not publisher-signature verification; the Python environment and dependencies must also be trusted. Update the registered digest if the executable changes. No PATH-first CLI selection or renderer-provided commands are accepted.

The optional version query has a 15-second deadline, service status 20 seconds, and integration diagnostics 60 seconds. Each invocation limits combined stdout/stderr to 256 KiB. Helpers run hidden in an owned Windows Job; completion, timeout and cancellation clean up their descendants. Only allowlisted status fields reach the UI. Valid unhealthy JSON remains useful even with exit code 1. Isolated real-CLI checks pass; installed-application qualification remains open in [validation guide](VALIDATION.md).

[Validation guide](VALIDATION.md) describes reproducible checks and outstanding platform and product gates.

## Remote installed-package acceptance

Windows GitHub Actions builds an unsigned installer, installs into a temporary Chinese path, and uses a matching Microsoft-signed WebDriver to operate the actual installed WebView2 page. An independent SQLite Server with synthetic data supports explicit connection activation, exact Scope selection, multiline Chinese note save, FTS search, exact reading, and paste-back verification of copied text and reference. The fixture Server and its temporary workspace are cleaned up afterward.

Reports, a screenshot and driver logs accompany the installer in the `desktop-windows-internal-unsigned` artifact. Failed or pending steps are not acceptance passes. The UI script permits only GitHub Windows runners and does not operate your local desktop. Hosted runners do not establish clean standard-user Windows 11, actual IME, screen-reader or Agent-host qualification.

To diagnose installed UI tests, manually dispatch `Desktop validation` with `installer_run` set to an existing Desktop CI run ID. It verifies the original package commit, digest and size before reusing that exact installer, and reports harness and installer commits separately. This diagnostic run does not replace final full-build acceptance.

Installed UI acceptance also checks content isolation between two independent connections, disconnect/reconnect, Server data preservation after removing an inactive profile, and the unknown outcome without replay after a real committed write loses its response. Credit each scenario only when its matching run report passes.

The CI-only lifecycle scenario forcibly ends its own installed Desktop process after saving a synthetic note, then checks that the independent Server still serves the original exact entry and accepts a new readable write. Consult the matching lifecycle report for its result; it does not simulate normal window closure or uninstall preservation.

Installed boundary checks exercise an 8192-byte Unicode note, reject over-budget input, display zero and capped-ten search results, and verify cancel/confirm behavior when disconnecting with an unsaved draft. Each result requires its matching remote report.

After upgrading from an earlier preview, open Connections and select your saved connection. Profiles matching the bundled API contract appear under “Available for this Desktop build”; compare their tested Server builds with your deployment before selecting one, saving, checking and explicitly using the connection again. New connections start with no compatibility selection.

Historical profiles appear as disabled records. A saved incompatible or unavailable selection remains visible with an explanation until explicitly replaced; it is never silently upgraded. For the qualified current Server sources, explicitly choose `sqlite-atomic-68491568-v1` and check the connection before selecting a Scope; that record's contract digest matches this build's bundled contract, while `sqlite-atomic-dd62723d-v1`, `sqlite-atomic-98275dbb-v1`, `sqlite-atomic-1da99747-v1`, `sqlite-atomic-8f10c560-v1`, `sqlite-atomic-d9a78567-v1`, `sqlite-atomic-caf2a35a-v1`, `sqlite-atomic-683e2db0-v1` and `sqlite-atomic-cab054db-v1` qualify their corresponding Server source deployments and do not match it. The recorded Server source and wheel identities must match the deployment you intend to use. The independently qualified `ci-fixtures` build is for disposable acceptance tests. Historical qualification evidence and remaining requirements are documented in the [validation guide](VALIDATION.md).

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
pnpm desktop:build
```

The app is in `src-tauri/target/release/bundle/macos/`; the installer is in
`src-tauri/target/release/bundle/dmg/`. Open the DMG and drag the app to Applications.
Launch **PowerContext Desktop Preview**. An independent Server is required for memory operations.
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

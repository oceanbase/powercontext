# Desktop validation

Desktop is an unsigned internal Windows preview. Passing CI does not establish release qualification.

## Reproducible checks

Run from the repository root:

```sh
pnpm --dir desktop install --frozen-lockfile
pnpm --dir desktop lint
pnpm --dir desktop typecheck
pnpm --dir desktop test
pnpm --dir desktop build
pnpm --dir desktop ipc:check
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --check
cargo clippy --locked --manifest-path desktop/src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
```

[Desktop CI](../.github/workflows/desktop.yml) also builds a Server wheel, runs real Server and CLI tests, checks Windows credentials and process containment, builds the installer, and exercises installation, the installed UI and uninstallation. Consult the PR checks for the exact commit being reviewed. Reports, logs and screenshots are generated under ignored `.artifacts/` and uploaded as Actions artifacts; do not copy them into the repository.

## Compatibility scope

[compatibility.json](src-tauri/src/connections/compatibility.json) identifies the qualified Server source, normalized OpenAPI contract digest and qualification-wheel digest for `sqlite-1.1.1-v1`. The qualification wheel was built from checkout `3501e4a6`; its backend sources, OpenAPI contract, project configuration and lock match the Server commit recorded in that manifest. CI builds have their own artifact identities.

The real Server harness covers SQLite with anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. It exercises save/search/exact reads, identity changes, binding revocation, ambiguous writes without replay and Scope pagination. Installed UI checks cover explicit connection/Scope selection, clipboard copies, connection isolation, draft cancellation, byte limits and independent Server survival after Desktop exit.

Selecting a compatibility profile does not attest the remote binary identity. Existing `sqlite-63f918b7-v1` selections require explicitly choosing the new profile and rechecking the connection. CLI diagnostics separately verify the registered executable path, digest and exact version.

## Remaining release qualification

- Clean standard-user Windows 11, including absent/present WebView2 and bootstrap recovery.
- Publisher signing and release ownership.
- Actual IME, screen reader, high contrast and complete keyboard accessibility.
- Notification cold activation, independent service login/reboot behavior and a named Agent host's capture/recall workflow.
- Maintainer ownership and approved performance budgets.

Hosted Windows CI and developer-machine checks do not replace these platform and product gates. This preview does not close #1654 or #1428.

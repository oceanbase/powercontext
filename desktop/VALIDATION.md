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

[compatibility.json](src-tauri/src/connections/compatibility.json) identifies the qualified Server source, normalized OpenAPI contract digest and qualification-wheel digest for `sqlite-atomic-f702c041-v1`, described in [Atomic Memory protocol qualification](#atomic-memory-protocol-qualification). CI builds have their own artifact identities.

The real Server harness covers SQLite with anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. It exercises save/search/exact reads, identity changes, binding revocation, ambiguous writes without replay and Scope pagination. Installed UI checks cover repeated launches preserving the existing editor, late committed-save responses preserving a new Scope draft, explicit connection/Scope selection, clipboard copies, connection isolation, draft cancellation, byte limits and independent Server survival after Desktop exit.

Selecting a compatibility profile does not attest the remote binary identity. Existing `sqlite-1.1.1-v1` / `sqlite-63f918b7-v1` / `sqlite-ab43e3a7-v1` / `sqlite-f1089f4e-v1` / `sqlite-6e237568-v1` / `sqlite-legacy-58f7f4f6-v1` selections require explicitly choosing the current profile and rechecking the connection. Desktop reads memories only through Atomic Memory references, so Servers that answer only with Memory entry citations are not qualified. CLI diagnostics separately verify the registered executable path, digest and exact version.

### Atomic Memory protocol qualification

`sqlite-atomic-f702c041-v1` identifies Server sources at `f702c0418d69e3072e1ec2fa657e0df81e8e51b4`, contract digest `5d8bbad951234b05e3174f1b9113e0ffe066ac968abbe53ac2456b2375e406d3`, and qualification-wheel digest `4fe64b868a2e214316a088574d8d4023d9b9679ec42029e2d13502121e6aade9`. The wheel version is `1.2.1.dev78+g77dc6f73b.d20261009`. It was captured at HEAD `77dc6f73b86678221d34a14e2de61dade0ff9db3` with staged tree `d2e9d703877ea707d78191ce0c358112fd69f65f` and a dirty working tree. That tree became the recorded Server commit; all 460 runtime files in the wheel match its sources byte for byte. The runtime source-manifest digest is `e26a4c4581f32b855689e3e0db26b48239f2f6758ab594addc9efc047978390d` (sorted wheel runtime paths and content digests). The OpenAPI contract, project configuration and lock remained unchanged between capture and commit. CI builds have their own wheel identities.

The native `ConnectionManager` exercised the actual isolated built-wheel SQLite Server in four modes: anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. All four passed on macOS 26.2 ARM64 with Rust 1.95.0 and Python 3.12.7. Checks covered Atomic Memory save/search/exact Artifact revision reads, identity changes, binding revocation, ambiguous writes without replay, Scope pagination and independent Server survival after client exit. The catalog records the ten operations exercised, including `get_artifact_revision`. The native harness candidate remains separate from the product catalog.

These results establish the stated Server protocol compatibility. Windows credentials, process containment, registered-executable checks and installed-package acceptance for this Atomic profile require their actual Windows CI checks. This protocol record does not establish platform or release acceptance.

## CI fixture 的独立资格

CI fixture 使用独立资格记录，不会修改历史生产资格。原生 HTTP 测试显式注入 synthetic profile；真实 Server 和安装验收使用 `fixture_qualification.py` 从当前合同、Server 提交和构建 wheel 生成的 `.artifacts/ci-compatibility.json`，读取时重新核对合同、操作集合和 wheel 哈希。

只有显式启用 `ci-fixtures` 的测试安装包会包含这个 profile。普通构建仍只使用历史资格记录，并保留合同不匹配时的 fail-closed 行为。CI fixture 和其安装包不能作为生产发布资格证据。

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
DESKTOP_FIXTURE_COMMIT=$(git rev-parse HEAD)
uv run python desktop/tests/fixture_qualification.py --server-commit "$DESKTOP_FIXTURE_COMMIT"
pnpm --dir desktop tauri build --bundles nsis --ci --features ci-fixtures -- --locked
```

## Remaining release qualification

- Clean standard-user Windows 11, including absent/present WebView2 and bootstrap recovery.
- Publisher signing and release ownership.
- Actual IME, screen reader, high contrast and complete keyboard accessibility.
- Notification cold activation, independent service login/reboot behavior and a named Agent host's capture/recall workflow.
- Maintainer ownership and approved performance budgets.

Hosted Windows CI and developer-machine checks do not replace these platform and product gates. This preview does not close #1654 or #1428.

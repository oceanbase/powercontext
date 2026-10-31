# Desktop validation

Desktop is an unsigned internal Windows and macOS preview. Passing CI does not establish release qualification.

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

## Native session observation checks

Windows x64 checks for the native session collector use Rust 1.95.0, Node 24.14.1 and pnpm 11.13.1.
`cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, all 62 native tests and generated IPC
checking pass. The new regressions exercise real loopback HTTP sockets with synthetic responses, including
readiness 503, request versus user-operation counting, millisecond latency, cancellation, unknown writes,
invalid decoded receipts, reset during an in-flight write, bounded retention, safe projection and collector
contention/poisoning. Mock Tauri IPC checks exercise the actual capability configuration and reject untrusted
windows and origins; session commands also work without connection state.

Desktop lint, TypeScript checking, all 98 UI tests and the frontend production build pass. The Desktop OpenAPI
contract checks pass without contract changes. Website lint and link validation pass, including both locale
versions of the [session observation contract](../docs/en/development/desktop-session-observations.md).
`uv lock --locked` and `uv run --locked pytest tests/test_desktop_contract.py -q` pass.

Repository hooks pass except `ty check` on Windows, which reports POSIX-only attributes in unchanged Python files
under `src/powercontext/builtin/code/` and `tests/builtin/test_native_code.py`. The explicit Linux-target check,
`uv run --locked ty check --python-platform linux`, passes. The native tests do not qualify an actual Server,
installed application, clipboard interaction or macOS execution. The This session UI and installed copy flow
remain separate delivery work; native collection does not make the placeholder page operational.

## Native discovery acceptance

The native `get_server_info` adapter and full real-Server harness were exercised on Windows x64 on 2026-10-23 with Rust 1.95.0 and Python 3.11.15. The Server sources, project configuration and lock matched baseline commit `c535d816`; the wheel was built at Desktop commit `f2b442fc41f724e206a50f2415a03476abdcdf76` with an unrelated dirty documentation tree. The wheel `powercontext-1.4.1.dev138+gf2b442fc4.d20261023-py3-none-any.whl` had SHA-256 `677812c860dd6f12866afa3b6b3e2e0529a6d37b427187c0ce4f3cd732349551`; the normalized contract digest was `5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`. All four isolated SQLite Server configurations passed: anonymous loopback, static Bearer, HTTPS with an explicit CA and reverse-proxy base path, and an enforced authentication Provider. Checks covered discovery, Scope selection, Memory save/search/exact revision reads, Principal changes, same-Principal revocation, uncertain writes without replay, pagination and independent Server survival. The Server environment included the declared `jieba==0.42.1` dependency. Results are recorded under ignored `.artifacts/real-server.json`.

The native suite passed all 61 tests, including synthetic HTTP regressions for missing/denied/unavailable/invalid/incompatible discovery, optional-field additions, operation membership, duplicate deployment-identity fields, qualification isolation, cancellation, credentials and uncertain writes. A synthetic HTTP regression verifies that a qualified connection blocks Scope requests after discovery returns 503 and allows them again after an explicit successful recheck. The bundled-product qualification test also exercised explicit Scope browsing through the normal manager. All 116 frontend tests passed, including distinct bilingual discovery diagnostics, replacement of a temporary-failure diagnostic after a successful recheck, and per-operation Memory/Scope gating with restriction reasons while independently supported operations remain available, including exact-read gating for records returned by a save. Clippy with `--all-targets -- -D warnings`, Rust formatting, generated contract drift, IPC export drift, frontend lint/type checks/build and seven focused Python contract/fixture tests passed. Generation was executed from a Windows checkout whose path contains a space. Repository hooks passed except default Windows `ty check`, which reported POSIX-only attributes in unchanged Python modules; `ty check --python-platform linux` passed. Synthetic HTTP and UI fixtures establish local status/recovery handling, not installed-host or release acceptance.

The historical Server c535d816 protocol qualification below records the specific source, wheel and operation identities independently of the CI-only fixture. It does not establish installed UI or release acceptance. The focused `server_probe <fixture.json> discovery-only` mode calls discovery without readiness or Memory requests and accepts the full probe's fixture format. Historical product records remain unchanged; the locally generated qualification file is a CI-only fixture. This historical discovery qualification retains its original contract digest; the `cc5980f0`, `dd62723d` and `4ffdf4e9` records retain their ten independently qualified operations and do not add discovery qualification. The `cc5980f0` acceptance predates the native discovery adapter and does not establish real-Server acceptance of the combined client and Server sources. Deployment-aware read/write fencing, explicit readiness separation, local setup approval handoff and installed Windows/macOS qualification remain separate acceptance requirements.

## Compatibility scope

[compatibility.json](src-tauri/src/connections/compatibility.json) preserves every recorded Server source, normalized OpenAPI contract digest, qualification-wheel digest and operation set. The bundled contract is qualified by `sqlite-atomic-bfaff459-v1` for the eleven protocol operations recorded below, using real SQLite Servers on macOS ARM64. All twenty-four historical records retain their original identities, operation sets and evidence. Ordinary builds require explicitly selecting the record whose tested Server source, wheel and contract match the deployment, then checking the connection before business operations become available. Records with a different contract digest are disabled in the selector, and saved unavailable selections remain visible until explicitly replaced. Selecting a profile does not extend its recorded operation set or qualify another Server source. CI fixture evidence is separate from product qualification.

The real Server harness covers SQLite with anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. It exercises save/search/exact reads, identity changes, binding revocation, ambiguous writes without replay and Scope pagination. Installed UI checks cover repeated launches preserving the existing editor, late committed-save responses preserving a new Scope draft, explicit connection/Scope selection, clipboard copies, connection isolation, draft cancellation, byte limits and independent Server survival after Desktop exit.

Selecting a compatibility profile does not attest the remote binary identity. Existing `sqlite-1.1.1-v1` / `sqlite-63f918b7-v1` / `sqlite-ab43e3a7-v1` / `sqlite-f1089f4e-v1` / `sqlite-6e237568-v1` / `sqlite-legacy-58f7f4f6-v1` selections require explicitly choosing an available matching profile and rechecking the connection. Desktop reads memories only through Atomic Memory references, so Servers that answer only with Memory entry citations are not qualified. CLI diagnostics separately verify the registered executable path and digest, then validate the diagnostic response schema. The observed version is optional report information; a registration does not require a version.

<a id="current-server-protocol-qualification"></a>

### Server protocol qualification records

#### Server bfaff459 protocol qualification

`sqlite-atomic-bfaff459-v1` records Server sources at
`bfaff4594528ffc32d7f3aa8b6bdd8e16f4c925b` for OpenAPI digest
`4aeefdd602a11ebece4c74f120b0cc8671aa3cd62386822326198ca7a4b88ca5`.
The wheel is `powercontext-1.4.2.dev97+gbfaff4594-py3-none-any.whl`, SHA-256
`b8291955e6bf242e504d35679cd74277cc46c4be9b80f593fd995ba14af74b06`, built from that clean commit.
All 666 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the recorded source bytes. The sorted runtime manifest digest is
`aeaaa92d42234bd68e672c973065e7c35e16e774dc09551f349ee6b9bce66c68`
(one `wheel/path sha256` line per file, with a final newline). Distribution version metadata
is outside this runtime manifest. OpenAPI, project configuration and the lock file also match
that commit.

On 2026-10-31, macOS 26.5.2 ARM64 with Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against independent SQLite Server processes loaded from
this wheel. All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit
CA/base path, and an injected provider with enforced access. The eleven recorded operations
cover Server discovery, liveness/readiness, access principal/capabilities, Scope listing,
detail and default, Atomic Memory save/search and exact Artifact revision reads. The native
connection manager also passed identity changes, same-identity revocation, Scope pagination,
committed writes with lost responses without replay, and Server survival after client exit.

The native `server_probe` compiled the unchanged product modules and locked dependencies;
its executable SHA-256 is
`a615f5931bdbc799c422b5311c09a9c9b47aae22e72123182de28f9f41b93540`.
All native build source, Cargo manifest and lock bytes match the recorded commit. The completed
harness report records `desktopWorkingTreeDirty=false`.

Reproduce from the recorded commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --frozen python desktop/tests/fixture_qualification.py --server-commit bfaff4594528ffc32d7f3aa8b6bdd8e16f4c925b
uv run --frozen python desktop/tests/real_server.py
```

Ignored local evidence is in `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This qualifies the listed SQLite
protocol behavior on that host. It does not establish native Windows/Intel execution,
installed GUI workflows, persistent native credentials, signing, notarization or full
release-upgrade acceptance. All twenty-four historical records retain their exact identities
and evidence.

本记录绑定上述准确源码、契约和 wheel；666 个运行时文件均与记录提交逐字节一致。
四种真实 SQLite／HTTP 连接模式均通过，覆盖所列十一项协议操作、身份变化、同身份撤权、
Scope 分页及响应丢失后不重放已提交写入。原生探针使用该提交的产品模块及锁定依赖。
该验证不替代 Windows／Intel、安装后 GUI、原生凭据持久化、签名、公证或完整升级验收。
全部历史记录保留原有身份、操作集合和证据。

#### Server db9c8d6e protocol qualification

`sqlite-atomic-db9c8d6e-v1` records Server sources at
`db9c8d6edc49166504b39e0d8038dd0082368dee` for OpenAPI digest
`e2a8b64371385e857a3bc44a6d83e09b909a72c9fead64964723ed30fd06226c`.
The wheel is `powercontext-1.4.2.dev90+gdb9c8d6ed-py3-none-any.whl`, SHA-256
`788a157d27765cc33c16357617d217096bcb3bf056c5f1ecb512e6c14d209e3c`, built from that clean source commit.
All 625 packaged source files in `powercontext/` and `powercontext_service_bootstrap/`
match that commit byte for byte; generated version metadata is excluded. The sorted source
manifest digest is `b962c8c852b5ef31922d6d19cad621009841a47ac288940d4794746a297f52c6`
(one `wheel/path sha256` line per file, with a final newline). OpenAPI, project configuration
and the lock file also match the recorded sources.

On 2026-10-30, macOS 26.5.2 ARM64 with Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against independent SQLite Servers loaded from this
wheel. Anonymous loopback, static Bearer, HTTPS with an explicit CA/base path, and an injected
provider with enforced access all passed. The unchanged native probe exercised discovery,
health, Principal, Scope selection and pagination, Atomic Memory save/search/exact revision
reads, identity changes, same-identity revocation, committed writes with lost responses without
replay, and Server survival after client exit. The candidate fixture supplies the exact
source/contract/wheel identity; the product record includes only the eleven exercised operations.
The harness reports `desktopWorkingTreeDirty=false`.

Reproduce from the recorded commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --frozen python desktop/tests/fixture_qualification.py --server-commit db9c8d6edc49166504b39e0d8038dd0082368dee
uv run --frozen python desktop/tests/real_server.py
```

Local evidence is in ignored `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This qualifies the listed protocol
behavior. It does not establish native Windows/Intel execution, installed GUI workflows,
persistent credentials, signing or full release-upgrade acceptance. Every historical record
retains its original source, contract, artifact and operation identities.

本记录绑定上述源码、契约和 wheel。原生探针通过四种真实 SQLite／HTTP 连接模式，
覆盖所列十一项操作、身份变化、撤权及响应丢失后的不确定写入。该验证不替代 Windows／Intel、
安装界面、原生凭据持久化、签名或完整升级验收。历史资格记录保留原有身份与证据。

#### Server 15c9d932 protocol qualification

`sqlite-atomic-15c9d932-v1` qualifies Server sources at
`15c9d9323dd071e595430c7b9d8a3ca68b4a4bee` for OpenAPI digest
`e37820efe88057fb7c77968b6c28d18c9698a919695d9c83e229ca0ca95cc08d`.
The wheel is `powercontext-1.4.2.dev49+g15c9d9323-py3-none-any.whl`, SHA-256
`7c943cb6a43b7e2e359f70cc0779d2f998b8ed79079eadfcb60b93c6f30b5bb0`, built from that clean commit.
All 618 packaged source files in `powercontext/` and `powercontext_service_bootstrap/` match the
recorded source bytes; generated version metadata is excluded. The sorted source manifest digest is
`b6ded3cf00b2a08488c6ccea43126b48045ca3f57ff1824557d6a0e26d1109b2`
(one `wheel/path sha256` line per file, with a final newline). OpenAPI, project configuration and
the lock file also match the recorded commit.

On 2026-10-29, macOS 26.5.2 ARM64 with Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against the extracted wheel and independent SQLite Server
processes. All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit CA
and base path, and injected-provider/enforced access. The eleven recorded operations cover
discovery, health, Principal, Scope selection and pagination, Atomic Memory save/search/exact
revision reads, identity changes, same-identity revocation, committed writes with lost responses
without replay, and Server survival after client exit. The native probe uses the unmodified
product modules and locked dependencies. The harness reports `desktopWorkingTreeDirty=false`.

Reproduce from the recorded commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --frozen python desktop/tests/fixture_qualification.py --server-commit 15c9d9323dd071e595430c7b9d8a3ca68b4a4bee
uv run --frozen python desktop/tests/real_server.py
```

Local evidence is under ignored `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This qualifies the stated SQLite protocol
behavior. Installed GUI workflows, persistent native credentials, Windows/Intel execution, signing,
and accessibility require their separate checks. Historical records retain their exact identities.

本记录绑定上述源码、契约和 wheel。四种真实 SQLite／HTTP 连接模式均已通过，覆盖发现、
Scope、Atomic Memory、身份变化、撤权及响应丢失后的不确定写入。该结果不替代安装后 GUI、
原生凭据持久化、Windows／Intel、签名或无障碍验收；历史资格记录保留原始身份。
#### Server 0a49db9b protocol qualification

`sqlite-atomic-0a49db9b-v1` qualifies Server sources at `0a49db9bf640703e1067ecda67650f20612d3427`
for OpenAPI digest `bce4e0d3deec56590a29c3c06af4d853fc8cad2917d8832b23c92dd160472ccd`.
The wheel `powercontext-1.4.1.dev0+g0a49db9b-py3-none-any.whl` has SHA-256
`8e57881de55502f99f44a6076bcffee2935f3d3666a00330664f4bcb0fb92150`. It was built from `git archive` of that commit with
`SETUPTOOLS_SCM_PRETEND_VERSION=1.4.1.dev0+g0a49db9b uv build --wheel`.
All 649 packaged runtime files match the archived sources byte for byte;
the sorted runtime path/content-digest manifest has SHA-256
`da9bf44eb0b21cfa72cef3cc863e87322a4c1d98af3dd7409d94d0e9ba57d9a6`.

On 2026-10-30, Linux x86_64, Python 3.12.12 and Rust 1.95.0 ran
`desktop/tests/real_server.py` against that wheel in independent SQLite Server processes.
Anonymous loopback, static Bearer, HTTPS with an explicit CA/base path, and
injected-provider/enforced access all passed. The eleven recorded operations include
Server discovery, health/readiness, principal/capabilities, Scope listing/detail/default,
Atomic Memory save/search and exact Artifact revision reads. The native context owner
also passed identity changes, same-identity revocation, committed writes with lost
responses without replay, Scope pagination and independent Server survival after client exit.

The native probe compiled the unchanged product `connections`, `credentials`, `error`,
`session_observations` and `transport` modules and `server_probe.rs` in a standalone library.
Resolved dependency versions and checksums match the product Cargo lock. The standalone
library excludes the Tauri GUI shell; it links the host's installed D-Bus library.
This qualifies the stated SQLite protocol behavior, not Windows/macOS execution,
native credential persistence, installed GUI workflows, signing or release readiness.
Historical records retain their original identities and evidence.

Reproduce using the archived wheel, native `server_probe` build,
`fixture_qualification.py --server-commit 0a49db9bf640703e1067ecda67650f20612d3427`, and
`real_server.py` as described below. Ignored evidence is retained in
`desktop/.artifacts/real-server.json` and `desktop/.artifacts/qualification-wheel-identity.json`.

本记录绑定上述准确源码、契约和 wheel；649 个运行时文件逐一核对一致。
四种真实 SQLite／HTTP 连接模式均已通过，覆盖包括 Server discovery 在内的十一项协议操作。
原生探针使用未修改的产品协议模块及产品锁文件中的依赖版本。
本次验收不覆盖 Windows／macOS 执行、原生凭据持久化、安装后 GUI 或发布验收。

#### Server cc5980f0 protocol qualification

`sqlite-atomic-cc5980f0-v1` qualifies Server sources at
`cc5980f01610f32b2f86b4d4068dd72a0408a500` for OpenAPI digest
`fd8f5dcf8dfb1a02c186623c08c3c62217ecdbc21a80cd5d9c329f3497b85f7d`.
The wheel `powercontext-1.4.1.dev211+gcc5980f0-py3-none-any.whl` has SHA-256
`443f5ec8c73148045caadbba09cf6f08cc6e17329fcf9764378ebcc2cb03ba7c`.
It was built from `git archive` of that commit with
`SETUPTOOLS_SCM_PRETEND_VERSION=1.4.1.dev211+gcc5980f0 uv build --wheel`.
All 614 packaged runtime files across `powercontext/` and `powercontext_service_bootstrap/`
match the archived sources byte for byte. Their manifest digest (sorted `wheel/path sha256`
lines with a final newline) is
`ea0a886acb5092548822b43b2dcee7f3e414a5ffba7d6bdc0b193ac311e157c1`.
The OpenAPI contract, project configuration and lock also match that commit.

On 2026-10-27, macOS 26.6.2 ARM64, Python 3.13.12 and Rust 1.99.1 ran
`desktop/tests/real_server.py` with the freshly built native `server_probe` against
independent SQLite Server processes loaded from that wheel. Anonymous loopback,
static Bearer, HTTPS with an explicit CA/base path, and injected-provider/enforced
access all passed. The ten operations recorded in the profile cover liveness,
readiness, access principal, capabilities, Scope listing/detail/default, Atomic Memory
save/search and exact Artifact revision reads. The same native connection manager also
passed Scope pagination, identity changes, same-identity revocation, committed writes
with lost responses without replay, and independent Server survival after client exit.

The client Rust sources, Cargo manifest/lock and qualification harnesses match the
recorded commit. The candidate fixture profile supplied the exact wheel/contract identity
to the probe. Evidence is
retained locally in ignored `desktop/.artifacts/real-server.json`,
`desktop/.artifacts/qualification-wheel-identity.json`,
`desktop/.artifacts/qualification-cc5980f0/` and `desktop/.artifacts/server-wheel/`.

`cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml` passed all 62 tests,
including `bundled_qualification_allows_explicit_scope_browsing`, with this product record.

This qualifies the listed SQLite protocol operations on that host. Windows execution,
installed UI, native credential persistence, signing and packaging require their own
acceptance checks. Historical profiles retain their original identities and evidence.

2026-10-27，使用上述提交的归档源码构建独立 wheel，并逐文件核对 614 个运行时文件及
OpenAPI、项目配置和锁文件。原生探针通过匿名回环、Bearer、显式 CA／路径前缀 HTTPS
及强制访问控制四种真实 SQLite 协议验收，覆盖所列十项操作、Scope 分页、身份变更、
同身份撤权、响应丢失后不重放已提交写入及客户端退出后 Server 独立存活。该记录仅覆盖
上述主机的协议行为，不包含 Windows、安装界面、原生凭据持久化、签名或打包验收。
包含随包兼容资格用例在内的全部 62 项 Rust 测试通过。

On 2026-10-28, the macOS ARM64 checkout adapter also passed all four modes against
the same archived wheel. The native `server_probe` executable had SHA-256
`c88e62723a05419b144282eedd90207cdd1d670a73aef2ec1b84eb94cfdc3d84`.
This run used the disposable CI fixture profile, including `get_server_info`, and
`cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml` passed. Evidence
is retained in ignored `desktop/.artifacts/qualification-cc5980f0-client-oct28/`.
These results verify the checkout adapter against that archived Server; they do
not extend the product profile's ten qualified operations or validate subsequent
Server migration code.

2026-10-28，macOS ARM64 上的当前检出客户端使用相同归档 wheel，再次通过四种连接模式，
并通过全部 Rust 测试。本次使用包含 `get_server_info` 的临时 CI 资格记录，仅验证客户端
与该归档 Server 的协议交互，不扩大产品记录的十项操作范围，也不验证后续 Server 迁移代码。

#### Server 777bfdb7 protocol qualification

`sqlite-atomic-777bfdb7-v1` qualifies Server sources at
`777bfdb7ef45e1380a8a9ea6d6639149924b639f` for OpenAPI digest
`f5e455eabb8d0e0353d963141802df5b14a4c1fdbb1a4a64ed1cfab6235730f1`.
The wheel `powercontext-1.4.1.dev202+g777bfdb7-py3-none-any.whl` has SHA-256
`9b0543d1c907774ea0f35d4b804b87c3f927925eb65661ebc6e7a1a184412199`.
It was built from `git archive` of that commit with the distribution version supplied explicitly.
All 553 packaged runtime files match the archived sources byte for byte. Their manifest digest
(sorted `wheel/path sha256` lines with a final newline) is
`55b6e3fcbb95f0d29d1ccecefd4d757498e305f23df1421b7f529bacedb0c07f`.

On 2026-10-26, macOS 26.6.2 ARM64, Python 3.13.12 and Rust 1.99.1 ran
`desktop/tests/real_server.py` with the native `server_probe` against independent SQLite
Server processes loaded from that wheel. Anonymous loopback, static Bearer, HTTPS with an
explicit CA/base path, and injected-provider/enforced access all passed. The ten listed
operations cover save/search/exact revision reads, Scope pagination, identity changes,
same-identity revocation, committed writes with lost responses without replay, and independent
Server survival after client exit. The client was built from commit `777bfdb7`; the working
tree also contained a persistence projection change, which was excluded from the archived
Server wheel and does not change the native client. Evidence is retained locally in ignored
`desktop/.artifacts/real-server.json` and `desktop/.artifacts/server-wheel/`.
This qualifies the listed SQLite protocol operations, not manifest descriptions, Windows
execution, native vaults, installed UI, signing or packaging. Historical records keep their
original source, contract and wheel identities.

2026-10-26，使用上述提交的归档源码构建独立 wheel，并逐文件核对 553 个运行时文件。
原生探针通过匿名回环、Bearer、显式 CA／路径前缀 HTTPS 及强制访问控制四种真实 SQLite
协议验收。该记录仅覆盖所列十项操作，不包含 manifest 摘要、Windows、安装包或原生凭据库验收。

#### Artifact framework merge 87d01f68 protocol qualification

`sqlite-artifact-merge-87d01f68-v1` identifies the uncommitted merge of branch HEAD
`0d78bcb14d51cda1b29d1adacce13568ad2e8cd4` and mainline
`90355b8e96d68e2932d7980b3e76957f35ea2b44`, including the migration compatibility adjustments.
Its `serverCommit` is a working-tree identity, not a Git commit. The source snapshot SHA-256 is
`87d01f6868f8337840f57985fd9be7f0772120d845d1b4ae80895e15724a5a80`: SHA-256 of sorted `path sha256` lines, each ending in a newline,
for all packaged runtime files at their `src/` paths plus `pyproject.toml`, `uv.lock`
and `openapi/powercontext.yaml`. All 638 packaged runtime files match the source byte for byte.

OpenAPI SHA-256: `aa46c5f141190bc3516a34da9fd07ce756bdcf7537ab7109f151755e28cc6ed7`.
Wheel: `powercontext-1.4.1.dev235+g0d78bcb14.d20261030-py3-none-any.whl`.
Wheel SHA-256: `4806c56e3e948249ef097e504e11347833b1d8fe510f4b7639ef3eca16460ce2`.
The development version is build metadata and does not change the formal release version.

On 2026-10-30, Linux x86_64, Python 3.12.12 and Rust 1.95.0 passed all four
built-wheel SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The unchanged
`server_probe.rs` covers save/search/exact reads, explicit activation and Scope selection,
committed writes with lost responses without replay, identity changes, revocation, Scope
pagination and Server survival after client exit. The record qualifies the same ten operations
as the preceding Artifact profile; plugin administration APIs are outside this qualification.

The 13 native source/probe files were copied unchanged into an isolated crate without
Tauri/UI dependencies. The local build used vendored DBus because the host lacked DBus
development headers. A local harness wrapper retained the 180-second fixture startup
allowance; protocol assertions and product timeouts were unchanged. This establishes SQLite
protocol compatibility for the recorded snapshot and wheel, not native credential-store,
installed Windows/macOS UI, signing or release acceptance. Historical records remain unchanged.

#### Artifact migration merge protocol qualification

`sqlite-artifact-merge-e30479df-v1` qualifies OpenAPI digest
`bf9c258adaa03a652093fbc83f449a5eff95ac6e018cc7281b5c530a2214f7a9` and wheel
`powercontext-1.4.1.dev179+g64b5f8e42.d20261027-py3-none-any.whl`, SHA-256
`6daf419858ae4eb618d49bc5e0cc3f3cc47d7d8ef473c8801b27d849b7c7b406`.

The tested source is the uncommitted merge of branch HEAD
`64b5f8e426019c4bd242a49818eb52feec9d32b7` and mainline
`8ef4f645e66622e223cd49720d5f009f63ecff85`, including Artifact migration compatibility changes.
The record's `serverCommit` value explicitly identifies a working-tree snapshot, not a Git commit.
Its source snapshot SHA-256 is `e30479dfd8e034b1cdca8a9b59d8b9860b350d9f1acc1f57e1b20a4f3d13c7b0`.
This hashes sorted `path sha256` lines, each ending in a newline, for all packaged runtime files
at their `src/` paths plus `pyproject.toml`, `uv.lock` and `openapi/powercontext.yaml`.
All 625 packaged runtime files match the source byte for byte; the corresponding
sorted `wheel/path sha256` manifest digest is `1fdc52ce259767cf0e362e85d43735ed0dad80aac3a9f54dd5b48a11ab235593`.
The wheel's development version is build metadata, not a formal release version change.

On 2026-10-27, Linux x86_64, Python 3.12.12 and Rust 1.95.0 passed all four
built-wheel SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access.
The unchanged `server_probe.rs` assertions cover save/search/exact reads, committed writes
with lost responses without replay, identity changes, revocation, Scope pagination and
Server survival after client exit. The record grants the same ten Desktop operations as
the preceding Artifact profile; plugin administration APIs are not qualified.

The unmodified connection, credential, error and transport modules and probe were rebuilt
in an isolated native crate without Tauri/UI dependencies. The initial default 20-second
fixture startup wait expired before protocol assertions. A local harness wrapper allowed
180 seconds for Server startup; protocol assertions and product timeouts were unchanged.
This establishes native SQLite protocol compatibility for the exact source snapshot and
wheel, not installed Windows/macOS UI, native credential-store or release acceptance.

#### Server 68491568 protocol qualification

`sqlite-atomic-68491568-v1` qualifies Server sources at `68491568359fd9f5723208bbeeec2f808fd2bcc9`
for OpenAPI digest `b89e721f9fba1c4ef4267b4420af417e5a88a7f7fbe893f639df913b77d24dc0`.
The wheel is `powercontext-1.4.1.dev188+g684915683-py3-none-any.whl`,
SHA-256 `91c54cfd8551cc1590abcedb8bbd7bf9865ffc41667fee7b3ac015c249f8bbfb`. All 634 packaged runtime files
match the clean source commit byte for byte; the sorted runtime manifest digest is
`13a9fac57efe09f5f336f2bb5dcb82b07d2e161dcb31c267f3ef60247a6e805b`
(one `wheel/path sha256` line per file, with a final newline).

On 2026-10-26, Linux x86_64, Python 3.12.12 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against this wheel and independent SQLite Servers.
All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit CA/base path,
and an injected provider with enforced access. The ten recorded operations cover Atomic Memory
save/search/exact reads, Scope selection and pagination, identity changes and revocation,
committed writes with lost responses without replay, and Server survival after client exit.

The native client compiled the unchanged product `connections`, `credentials`, `error` and
`transport` modules and existing `server_probe.rs` in a standalone library, excluding the Tauri GUI
shell. Every resolved dependency version and checksum matches the product Cargo lock.
This qualifies the stated SQLite protocol behavior, not Windows/macOS execution, native credential
persistence, installed GUI workflows, signing or release readiness. Historical records retain their
original identities and evidence.

Reproduce from the recorded source using the wheel build, native `server_probe` build,
`fixture_qualification.py --server-commit 68491568359fd9f5723208bbeeec2f808fd2bcc9`, and
`real_server.py` commands described below for Linux protocol-only qualification.
Ignored evidence is retained in `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`.

本记录绑定上述精确源码、契约和 wheel；四种真实 SQLite／HTTP 连接模式均已通过。
原生客户端使用未修改的产品协议模块，依赖版本及校验和与产品 Cargo 锁文件一致。
本次验收不覆盖 Windows／macOS 执行、原生凭据库、安装后 GUI 或发布验收。

#### Server 4ffdf4e9 protocol qualification

`sqlite-atomic-4ffdf4e9-v1` qualifies the v1.4.1 preparation sources at
`4ffdf4e97ad43c55d8718a1df581596db2e6097d` for OpenAPI digest
`c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`.
The qualification wheel is `powercontext-1.4.1.dev176+g4ffdf4e97-py3-none-any.whl`, SHA-256
`33079ebc5eb61d03dd4cccc0d104c47b0415beab1502a5b01fbc38f2cc30af2b`, built from that clean commit.
All 552 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the source bytes at that commit. The runtime manifest digest is
`ea7f63e5e0a16fa7386c3bdea80ea083dd09fe00447a3639391ed114a9e65ec3`
(sorted `wheel/path sha256` lines, with a final newline).
This is a development wheel from the release preparation branch; it is not evidence of a published v1.4.1 package.

On 2026-10-26, Linux x86_64 with Python 3.14.6 and Rust 1.96.0 ran the existing
`desktop/tests/real_server.py` harness against the extracted wheel and independent SQLite Server processes.
All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit CA and base path,
and an injected provider with enforced access. The ten recorded operations cover Atomic Memory
save/search/exact revision reads, Scope selection and pagination, identity changes, same-identity
revocation, committed writes with lost responses without replay, and Server survival after client exit.
The report records the source commit above and `desktopWorkingTreeDirty=false`.

The native client compiled the unmodified product `connections`, `credentials`, `error`, and `transport`
modules and the existing `server_probe.rs` in a standalone library. Dependency versions and checksums
match the product Cargo lock. The standalone library excludes the Tauri GUI shell because the local
Linux environment lacks WebKit development libraries. These results qualify SQLite protocol behavior;
Windows/macOS packaging, native vaults, installed UI, signing, IME, and screen-reader behavior require
their own platform checks.

From the recorded checkout, use an empty wheel output directory and a platform with the Desktop native
build dependencies to run the existing harness:

```sh
uv sync --frozen --python 3.14
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --no-sync python desktop/tests/fixture_qualification.py --server-commit 4ffdf4e97ad43c55d8718a1df581596db2e6097d
uv run --no-sync python desktop/tests/real_server.py
```

#### Server dd62723d protocol qualification

`sqlite-atomic-dd62723d-v1` qualifies Server sources at `dd62723dceabae8b41e293531f0080a819f87c68`
for OpenAPI digest `c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`.
The wheel is `powercontext-1.4.1.dev293+gdd62723dc-py3-none-any.whl`,
SHA-256 `030c6a4647f0ee517d15877286c99fef87ed6a5fb29ef6b2d606615b66274441`. All 609 packaged runtime files
match the clean source commit byte for byte; the sorted runtime manifest digest is
`75947c5ecf6ce0053a9632155c2063093732ada18128607aba20eca91229aeeb` (one `wheel/path sha256` line per file, with a final newline).

On 2026-10-26, macOS 15.7.3 ARM64, Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against this wheel and independent SQLite Servers.
All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit CA/base path,
and an injected provider with enforced access. The native `server_probe` was rebuilt from
the unmodified product modules and locked dependencies. The ten recorded operations cover
save/search/exact reads, Scope selection and pagination, identity changes and revocation,
committed writes with lost responses without replay, and Server survival after client exit.

This is protocol qualification of the recorded source, contract and wheel. It does not qualify
Windows/Intel execution, native credential persistence, installed GUI workflows, signing or release readiness.
Historical records keep their original identities. Reproduce with the existing wheel build,
native `server_probe` build, `fixture_qualification.py --server-commit dd62723dceabae8b41e293531f0080a819f87c68`,
and `real_server.py` commands from this clean commit.

本记录对应上述精确源码、契约和 wheel；四种真实 SQLite／HTTP 连接模式均已通过。
它不替代 Windows／Intel、安装后 GUI、原生凭据库或正式发布验收。

#### Server 98275dbb protocol qualification

`sqlite-atomic-98275dbb-v1` qualifies Server sources at
`98275dbbb745cea6d905eb6fdd2eac70197d0127` for the bundled OpenAPI digest
`3165f114185aa403b563c936d5fbd0829388628f4a37540d9d1519202a07871c`. The qualification wheel is
`powercontext-1.4.1.dev175+g98275dbbb-py3-none-any.whl`, SHA-256
`970fa0eb6b3d0f728e8a4a92ca2a9d9435c38380e162a0a3386f503738378c36`, built from that clean commit.
All 551 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the recorded commit byte for byte. The runtime manifest digest is
`328fc51cc6320458a28700c45e7fca96bb24ffdd03242a578f5765f74a14cb05`
(sorted `wheel/path sha256` lines, with a final newline). The OpenAPI contract,
project configuration and lock also match the recorded commit.

On 2026-10-25, Linux x86_64 with Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against the extracted wheel and independent
SQLite Server processes. All four modes passed: anonymous loopback, static Bearer,
HTTPS with an explicit CA and base path, and an injected provider with enforced access.
The ten recorded operations cover Atomic Memory save/search/exact revision reads,
Scope selection and pagination, identity changes, same-identity revocation, committed
writes with lost responses without replay, and independent Server survival after client exit.

The native client compiled the unmodified product `connections`, `credentials`, `error`
and `transport` modules and the existing `server_probe.rs`, using dependency versions
and checksums from the product Cargo lock. The standalone library excludes the Tauri
GUI shell because the Linux environment lacks WebKit development libraries. This
qualifies the stated SQLite protocol behavior; Windows/macOS packaging, native vaults,
installed UI, signing, IME and screen-reader behavior require their own platform checks.

From the recorded checkout, the existing harness prepares a candidate profile from
the exact wheel before exercising it:

```sh
uv sync --frozen --python 3.12
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --no-sync python desktop/tests/fixture_qualification.py --server-commit 98275dbbb745cea6d905eb6fdd2eac70197d0127
uv run --no-sync python desktop/tests/real_server.py
```

Use an empty wheel output directory and a platform with the Desktop native build
dependencies for the Cargo command. Linux protocol-only runs can compile the four
unchanged modules and `server_probe.rs` in a standalone library with the product lock's
dependency versions. The completed run's ignored evidence is
`desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. Historical profiles retain their
exact identities and evidence; ordinary builds require explicit selection of the matching
profile and a connection check.

2026-10-25，Linux x86_64 的原生连接管理器对独立 wheel／SQLite Server 完成四种真实协议验收，
覆盖匿名、Bearer、HTTPS／反向代理路径及注入认证／权限控制。551 个运行时文件、OpenAPI、
项目配置和锁文件均与记录的源码提交逐一核对。该资格记录绑定上述源码、契约和 wheel 摘要；
历史记录保持原有身份与证据。原生客户端使用未修改的产品模块及现有探针，依赖版本和校验和
与产品 Cargo 锁文件一致。本次未运行 Tauri 图形界面、Windows／macOS 安装包、原生凭据库、
签名、IME 或屏幕阅读器验收。

#### Server 8f10c560 protocol qualification

The historical preview contract is qualified by `sqlite-atomic-8f10c560-v1`. It identifies Server sources
at `8f10c560776b32b717c3511fad9da349982a5748` and normalized OpenAPI digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`.
The qualification wheel is `powercontext-1.4.1.dev242+g8f10c5607-py3-none-any.whl`,
SHA-256 `c771a39de6d6797efc33d406619aa02eeb7d7731f27147b3287079616709db97`.
All 583 packaged runtime files across `powercontext/` and `powercontext_service_bootstrap/`
match that Git commit byte for byte. The OpenAPI contract, project configuration and lock also match.
The sorted runtime source-manifest digest is
`d9f578a88dc9a2e0fffb3b75b9c4fba8bc245720857333c6bd6ad590e488b17c`
(one `wheel/path sha256` entry per line, with a final newline).

The existing real-Server harness passed against the isolated wheel and SQLite in all four modes:
anonymous loopback, static Bearer, HTTPS with an explicit CA and base path, and an injected
provider with enforced access. The ten recorded operations cover Scope selection and pagination,
Atomic Memory save/search/exact revision reads, identity changes, same-identity revocation,
committed writes with lost responses without replay, and independent Server survival after the
client exits.

Qualification ran on macOS 15.7.3 ARM64 with Python 3.12.13 and Rust 1.95.0, from a clean worktree
at `8f10c560776b32b717c3511fad9da349982a5748`. The native `server_probe` was built with the unchanged
product modules and Cargo lock. Credentials used session-only storage. This establishes the stated
Server protocol compatibility on that host; it does not establish installed GUI workflows, native
credential persistence, Intel or Windows execution, signing, notarization, or release qualification.
Historical records retain their exact source, contract, wheel and platform evidence.

#### Server 1da99747 protocol qualification

`sqlite-atomic-1da99747-v1` qualifies Server sources at
`1da997476651c2850ff7141e2b7cd512d0152a90` for the bundled OpenAPI digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`. The qualification wheel is
`powercontext-0.1.dev1120+g2066de01d-py3-none-any.whl`, SHA-256
`f87da56c813fe0e994ba60c29e8ba99eb48567dc561ac9f19ac2979e0599c1a6`. It was built from clean Desktop commit
`2066de01dbd27d08eb01a192b5b22be01c688235`; Server sources, OpenAPI, project configuration
and lock match the recorded Server commit after LF normalization. All 542 packaged
runtime files in `powercontext/` and `powercontext_service_bootstrap/` were compared with
that commit. The manifest digest is `716896e084dbaac6a6ea159c7d4e1a47bb741a27afbc8be5b56f548d7ef72ce0`
(sorted `wheel/path sha256` lines over the wheel's original bytes, with a final newline).
Windows checkout CRLF differences were normalized for the source comparison only.

On 2026-10-23, Windows x64 with Python 3.12.4 and Rust 1.95.0 ran the
existing native `ConnectionManager` / `server_probe` against the extracted wheel and
independent SQLite Server processes. All four modes passed: anonymous loopback, static
Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. The ten
operations cover Atomic Memory save/search/exact revision reads, Scope selection and
pagination, identity changes, same-identity revocation, committed writes with lost responses
without replay, and independent Server survival after client exit.

Reproduce the protocol check from a clean checkout with the pinned tools:

```powershell
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --locked python desktop/tests/fixture_qualification.py --server-commit 1da997476651c2850ff7141e2b7cd512d0152a90
uv run --locked python desktop/tests/real_server.py
```

Use an empty wheel output directory. The harness supplies a separate candidate fixture profile;
the bundled product record above is backed by its completed four-mode results and the verified
source/artifact identities. Ordinary builds require explicit profile selection and a connection
check; historical profiles remain unchanged. Local evidence is under ignored
`desktop/.artifacts/real-server.json` and `.artifacts/desktop-shell/current-contract-wheel-identity.json`.
This establishes protocol compatibility for the recorded sources and wheel, without qualifying
installed UI, credential-store prompts, signing, macOS execution, IME or screen-reader behavior.

2026-10-23，Windows x64 的原生连接管理器对独立 wheel／SQLite Server 完成四种真实协议验收，
覆盖匿名、Bearer、HTTPS／反向代理路径及注入认证／权限控制。新资格记录绑定上述源码、
合同和 wheel 摘要；542 个运行时文件与指定提交在统一换行符后逐一核对，历史记录保持不变。
普通构建需明确选择新配置并检查连接。该记录验证协议兼容性，不代表安装包、凭据库提示、
签名、macOS、IME 或屏幕阅读器验收通过。

The qualification regression `bundled_qualification_allows_explicit_scope_browsing` and the
complete default-feature Rust test suite passed with this record. All 86 frontend tests,
typecheck, lint, production build, generated-contract/icon checks, IPC export checking,
Rust fmt/all-target clippy and five fixture qualification regressions passed. Native test
output is under ignored `.artifacts/desktop-shell/current-contract-native-tests.log`.
新记录下原失败用例与完整 Rust 测试通过，86 项前端测试及相关构建、静态和生成检查通过。
Repository hooks passed except the existing Windows Python type errors in unchanged code;
see ignored `.artifacts/desktop-shell/current-contract-prek.log`.
仓库全量钩子仍有未修改代码的既存 Windows Python 类型错误，其余钩子通过。

#### Server c535d816 protocol qualification

`sqlite-atomic-c535d816-v1` records Server sources at
`c535d816cee43fe1d80871489361a34c70306e61`, normalized OpenAPI digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`, and wheel
`powercontext-1.4.1.dev138+gf2b442fc4.d20261023-py3-none-any.whl`, SHA-256
`677812c860dd6f12866afa3b6b3e2e0529a6d37b427187c0ce4f3cd732349551`.
The wheel was built at Desktop commit `f2b442fc41f724e206a50f2415a03476abdcdf76`.
All 540 packaged runtime files match the recorded Server sources after CRLF normalization;
the OpenAPI contract, project configuration and lock also match. The sorted wheel runtime
source-manifest digest is `de9a0d13784677fcf1b3dd0cc1c54fc719d462e4a6dbafc5524071125ad96504`
(one `wheel/path sha256` entry per line, with a final newline; hashes use the packaged bytes).

The eleven recorded operations include discovery and the existing health, Principal, Scope and
Atomic Memory operations. The full real-Server harness passed in all four SQLite modes described
above. A second run used the normal `ConnectionManager::new` and explicitly selected this bundled
product record, without injected qualification. Its temporary native probe reused the existing
`server_probe.rs` assertions, changing only manager construction and the selected qualification ID.
All four modes passed again, including save/search/exact reads, Principal changes and revocation,
pagination, uncertain writes without replay and independent Server survival. The product
connection, transport, credential and error implementations were unchanged. Evidence is recorded
under ignored `.artifacts/real-server-product-protocol.json` and
`.artifacts/product-protocol-source-verification.json`.

Qualification used Windows 10 x64 (10.0.19045), Python 3.11.15 and Rust 1.95.0. This establishes
Server protocol compatibility for these sources and this wheel. It does not establish Windows 11
or macOS installed-package, credential-store, UI or release qualification. Historical catalog
records retain their exact identities and evidence.

#### Server d9a78567 protocol qualification

The corresponding historical preview contract is qualified by `sqlite-atomic-d9a78567-v1`. The record identifies Server
sources at `d9a785675dedb72cd72a386b346e590f4c067ac0` and normalized OpenAPI digest
`a6758fd35296abefb55ab86b152584a369d5f2deedfee7a3f9822e16ad1edc25`.
The qualification wheel is `powercontext-1.4.1.dev69+ga200d6094.d20261022-py3-none-any.whl`,
SHA-256 `cbd2b544b12c781f4e331e1f0bcc9c9203392dea84f7d2f4491dce9bfd45c7f4`.
All 537 packaged runtime files match the recorded Server commit byte for byte; the OpenAPI
contract, project configuration and lock also match. The sorted runtime source-manifest digest
is `4ceb111b390b7558c955d8fef0c87f96355edc522b767340f7adac963880ba70`
(one `wheel/path sha256` entry per line, with a final newline).

The existing real-Server harness passed against the isolated wheel and SQLite in all four modes:
anonymous loopback, static Bearer, HTTPS with an explicit CA and base path, and an injected
provider with enforced access. The ten recorded operations cover Scope selection and pagination,
Atomic Memory save/search/exact revision reads, identity changes, same-identity revocation,
committed writes with lost responses without replay, and independent Server survival after the
client exits.

Qualification ran on Linux x86_64 with Python 3.12.13 and Rust 1.95.0. The standalone Rust client
compiled the unmodified product connection, transport, credential and error modules, plus the
existing `server_probe.rs`, with dependency versions from the product Cargo lock. The Tauri GUI
shell was excluded. This establishes the stated Server protocol compatibility; it does not
qualify Linux Desktop support, native macOS/Windows packaging, credential stores, or installed
UI workflows. Historical qualification records retain their exact identities and evidence.

#### Server cab054db protocol qualification

The corresponding historical preview contract is qualified by `sqlite-atomic-cab054db-v1`. It identifies Server sources
at `cab054dbf6d86e2311b079388e5158178257def2` and normalized OpenAPI digest
`042842aee551aa6930c90ce99724f88ebed2547ed42e5f02bc5d8191163b907b` (release v1.4.0 changes the OpenAPI
version only). The qualification wheel is
`powercontext-1.3.1.dev114+g9243caa3a-py3-none-any.whl`, SHA-256
`042c28f85caae59bc6df3cdec9a78fad50f67cd2492b5bd229dc740e2af8f8d9`. It was built at Desktop merge
commit `9243caa3a8ed0f8683c87681c71269571ff4768b`; Server runtime sources are unchanged since
`cab054db`. All 517 packaged runtime files match the recorded Server commit byte for byte; the
OpenAPI contract, project configuration and lock also match. The sorted runtime source-manifest
digest is `d8f1257b59192feab62f82d76355457719e1e45ea2cf3103f5296d32e658893d`
(one `wheel/path sha256` entry per line, with a final newline).

The native `ConnectionManager` exercised the isolated wheel and SQLite in anonymous loopback,
static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced-access modes.
All four passed on macOS 26.5.2 ARM64, Python 3.12.13 and Rust 1.95.0. The ten recorded operations
cover Atomic Memory save/search/exact revision reads, Scope pagination, identity changes,
revocation, committed writes with lost responses without replay, and independent Server survival
after client exit. This establishes protocol compatibility for these sources and this wheel.
The tested native client source includes the macOS host support and the bounded diagnostic
pipe collection described in the [security boundary](SECURITY.md). Windows installer acceptance,
Intel execution, signing and accessibility require their own checks. Historical records retain
their original source, contract and wheel identities.

#### Server 683e2db0 protocol qualification

The bundled contract of the previous preview build is qualified by `sqlite-atomic-683e2db0-v1`. It
identifies Server sources
at `683e2db081fc58a69f6527c1916ae77cdeeeb371` and normalized OpenAPI digest
`dae5feab227a6c16a8199455a27c82b444c9c17b43bec7de0fca4790c7bed14f`. The qualification wheel is
`powercontext-1.3.1.dev105+g7f8d5dfdf-py3-none-any.whl`, SHA-256
`dd81cfadb5ce6999873eab25ff5318a6a794987597d83daeb4f0eb90618e95c1`. It was built at Desktop commit
`7f8d5dfdf74f39d7c51af908e2c1ebe3e73205b9`. All 517 packaged runtime files match the recorded Server
commit byte for byte; the OpenAPI contract, project configuration and lock also match.
The sorted runtime source-manifest digest is
`0d750d72f9933631d16833dd7f69be3dd6cffcf083035d5457b8f2b313b1e5e9` (one `wheel/path sha256` entry per line, with a final newline).

The native `ConnectionManager` exercised the isolated wheel and SQLite in anonymous loopback,
static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced-access modes.
All four passed on macOS 26.5.2 ARM64, Python 3.12.13 and Rust 1.95.0. The ten recorded operations
cover Atomic Memory save/search/exact revision reads, Scope pagination, identity changes,
revocation, committed writes with lost responses without replay, and independent Server survival
after client exit. This establishes protocol compatibility for these sources and this wheel.
Windows installer acceptance, Intel execution, signing and accessibility require their own checks.
Historical records retain their original source, contract and wheel identities.

#### Server caf2a35a protocol qualification

`sqlite-atomic-caf2a35a-v1` records Server commit `caf2a35aa5c626e013584a2c3a37dcd94e0d49bf`,
normalized OpenAPI contract digest `e43c7034b6930688c350c68af5e7bd6083b123783b92d0a50eebbebd45aa7da7`,
and wheel digest `4ac43be2f914f8075f97b896b9e2773fcfeff0b5fe354241c6d02d44dbb4a955`.
The qualification wheel is `powercontext-1.3.1.dev89+gcaf2a35aa.d20261018-py3-none-any.whl`.
All 512 packaged runtime files match the recorded commit byte for byte. The sorted runtime
source-manifest digest (one `wheel/path sha256` entry per line, with a final newline) is
`a5c0fba7746b38b0f69db2a07689353f98f2f0526f9315684687704d0aeb3ee2`.
The wheel was built before Desktop-only commits; those commits do not change Server sources.

The native real-Server harness passed on macOS 26.5.2 ARM64, Python 3.14.7 and Rust 1.95.0.
It used the isolated wheel and SQLite in four modes: anonymous loopback, static Bearer,
HTTPS with an explicit CA and base path, and an injected authentication provider with enforced access.
It exercised the ten recorded operations, including Atomic Memory save/search/exact revision reads,
identity changes, revocation with an unchanged identity, Scope pagination, committed writes with lost
responses without replay, and independent Server survival after the native client exited.
Fixture certificates include Authority Key Identifier extensions for strict X.509 verification.
The tested native client source includes the macOS host support and strict fixture-certificate change.

This is protocol qualification of that source and wheel, not binary attestation of arbitrary remote
Servers. Users must check their deployed source before selecting the profile. Windows and Intel
native checks, installed UI workflows, signing and notarization require their own evidence.
Reproduce with `uv build --wheel --out-dir desktop/.artifacts/server-wheel`,
`fixture_qualification.py --server-commit <the recorded Server commit>`,
`cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe`,
and `uv run python desktop/tests/real_server.py`. New builds have their own wheel identities.

### Historical macOS installed application workflow

This observation was recorded in Desktop commit `984e8c60fed262f60e1054c17c8b7d84489af999`,
whose bundled OpenAPI digest was `e43c7034b6930688c350c68af5e7bd6083b123783b92d0a50eebbebd45aa7da7`,
matching `sqlite-atomic-caf2a35a-v1`. The observed app is identified by the executable SHA-256 below;
this record does not identify its exact build source revision. This is historical evidence, not a
procedure or native UI qualification for the current bundled contract.

The `sqlite-atomic-d9a78567-v1` record has non-GUI protocol evidence on Linux x86_64 only.
Native macOS save/search/exact-read and clipboard acceptance against that record remains unverified.

The historical records have native protocol evidence on Windows x64 (`sqlite-atomic-1da99747-v1`)
and macOS ARM64 (`sqlite-atomic-8f10c560-v1`). Both bind the earlier contract digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`; their installed GUI and clipboard
workflows require separate acceptance. The current `sqlite-atomic-98275dbb-v1` record establishes
the Linux protocol scope stated above, not those native application workflows.

The normal release app (without `ci-fixtures`) was operated on macOS 26.5.2 ARM64 against the
wheel recorded under [Server caf2a35a protocol qualification](#server-caf2a35a-protocol-qualification)
on a dedicated loopback Server and SQLite workspace. The UI saved its
connection profile, explicitly selected `sqlite-atomic-caf2a35a-v1`, checked/activated the connection,
searched by Scope title and selected its exact ID. It saved a multiline Chinese/English note,
found that note through FTS, and read its exact Atomic Memory revision. Both copy buttons were
verified by pasting the actual clipboard into the app editor: full text remained unchanged and
reference JSON retained `family`, `artifact_id` and `revision`. An independent HTTP read returned
the same persisted exact text. Test paste-back drafts were cleared without saving duplicates.
The saved connection and qualification choice survived application restart. After read-only DMG
verification, the app was copied to a separate directory and launched from that copy; its executable
SHA-256 matched the built app (`ba8ee45b5895572737a8b639535dee4a5725e54c0eda6a10cc49169974f9441f`).
The copied app reconnected, selected the Scope, searched and read the original exact revision.
The independent demo Server remained alive after Desktop exited, and its persisted note also
survived restarting the Server.

This establishes those workflows on the observed host and build. It does not establish actual IME
composition (Chinese text was pasted), screen-reader behavior, Intel/Windows execution, Gatekeeper
acceptance, signing or notarization.

### Atomic Memory protocol qualification

`sqlite-atomic-f702c041-v1` identifies Server sources at `f702c0418d69e3072e1ec2fa657e0df81e8e51b4`, contract digest `5d8bbad951234b05e3174f1b9113e0ffe066ac968abbe53ac2456b2375e406d3`, and qualification-wheel digest `4fe64b868a2e214316a088574d8d4023d9b9679ec42029e2d13502121e6aade9`. The wheel version is `1.2.1.dev78+g77dc6f73b.d20261009`. It was captured at HEAD `77dc6f73b86678221d34a14e2de61dade0ff9db3` with staged tree `d2e9d703877ea707d78191ce0c358112fd69f65f` and a dirty working tree. That tree became the recorded Server commit; all 460 runtime files in the wheel match its sources byte for byte. The runtime source-manifest digest is `e26a4c4581f32b855689e3e0db26b48239f2f6758ab594addc9efc047978390d` (sorted wheel runtime paths and content digests). The OpenAPI contract, project configuration and lock remained unchanged between capture and commit. CI builds have their own wheel identities.

The native `ConnectionManager` exercised the actual isolated built-wheel SQLite Server in four modes: anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. All four passed on macOS 26.2 ARM64 with Rust 1.95.0 and Python 3.12.7. Checks covered Atomic Memory save/search/exact Artifact revision reads, identity changes, binding revocation, ambiguous writes without replay, Scope pagination and independent Server survival after client exit. The catalog records the ten operations exercised, including `get_artifact_revision`. The native harness candidate remains separate from the product catalog.

These results establish the stated Server protocol compatibility. Windows credentials, process containment, registered-executable checks and installed-package acceptance for this Atomic profile require their actual Windows CI checks. This protocol record does not establish platform or release acceptance.

### Historical protocol qualifications

`sqlite-6e237568-v1` records the original Server source, contract digest and qualification-wheel digest. Its qualification wheel was built from checkout `ea30fd5c`; its backend sources, OpenAPI contract, project configuration and lock matched the recorded Server commit. The immutable catalog preserves its identities and original ten operations.

`sqlite-legacy-58f7f4f6-v1` identifies Server sources at `58f7f4f6cbadd698bac35b3fca019d9f48f55b81`, contract digest `9f4ba7b35ecd3b3639af4e648aff84b713a7f30b51d5a4c30c8809aeb8dc8024`, and qualification-wheel digest `7203cf1a98cb9dfd8631e142f7a0f5e74c39845da4fb7d862117a49fa1ef38af`. Its original ten Desktop operations and Legacy Memory protocol were exercised by the native `ConnectionManager` against the isolated built-wheel SQLite Server in all four harness modes: anonymous loopback, Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. These checks cover save/search/exact historical citation reads, identity changes, revocation, ambiguous writes without replay, and Scope pagination. The original `sqlite-6e237568-v1` record retains its identities and operations. The Base Legacy qualification requires explicitly selecting a profile whose recorded contract matches the bundled Desktop contract and rechecking the connection.

This qualification used macOS 26.2 ARM64, Rust 1.95.0 and Python 3.12.7. An external system Python launcher was used for native process creation because this environment can terminate native subprocesses before their first request. The wheel, native client, fixtures and business assertions were preserved. The real CLI's fixed version, service-status and integration commands were also exercised with an isolated home and PATH; their actual outputs passed the native projections. These checks establish the stated SQLite compatibility and CLI response handling. Windows registered-executable checks, credentials, process containment and installed-package acceptance require their actual Windows CI checks. No remote legacy deployment was tested in this qualification.

`sqlite-atomic-7bd5b85c-v1` identifies Server sources at `7bd5b85c154c284d0ad550de31ecf8aa629d8ccf`, contract digest `0807df984ba706b505d4fff0cb849d4566222f10572d91a5e03399b833d1fe4f`, and qualification-wheel digest `057eca5de29961684bfea8ea8aece7c133b3d8b46a024db69a944519bc5b2746`. Its native `ConnectionManager` was exercised against the isolated built-wheel SQLite Server in all four harness modes: anonymous loopback, Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. These checks cover Atomic writes, all returned references, search, immutable revision reads, identity changes, revocation, ambiguous writes without replay, and Scope pagination. These are historical results for that qualified build; this Desktop build reads only Atomic Memory references and does not retain the Legacy citation protocol.

The Atomic qualification run used macOS 26.2 ARM64, Rust 1.95.0 and Python 3.12.7. An external launcher routed native process creation through system Python 3.9.6 because the same signed native executable exited with SIGKILL when launched by the Conda Python parent before its first request. The wheel, native client, harness fixtures and business assertions were unchanged by that launcher. This establishes the stated SQLite compatibility; Windows IPC, vault, process containment and installed-package acceptance require their actual Windows CI checks. No legacy remote deployment was tested in the Atomic qualification run.

## CI fixture 的独立资格

CI fixture 使用独立资格记录，不会修改历史生产资格。原生 HTTP 测试显式注入 synthetic profile；真实 Server 和安装验收使用 `fixture_qualification.py` 从当前合同、Server 提交和构建 wheel 生成的 `.artifacts/ci-compatibility.json`，读取时重新核对合同、操作集合和 wheel 哈希。

只有显式启用 `ci-fixtures` 的测试安装包会包含这个 profile。普通构建仍只使用历史资格记录，并保留合同不匹配时的 fail-closed 行为。CI fixture 和其安装包不能作为生产发布资格证据。

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
DESKTOP_FIXTURE_COMMIT=$(git rev-parse HEAD)
uv run python desktop/tests/fixture_qualification.py --server-commit "$DESKTOP_FIXTURE_COMMIT"
pnpm --dir desktop desktop:build:windows:ci
```

## Observation navigation (#2102)

The application opens Overview and provides Overview, Connections, Memories, and Observation in the main navigation. Settings & diagnostics remains at the bottom. Observation provides Runtime state, Current-Scope inventory & usage, and This session tabs with unavailable data views; navigation does not initiate connection checks or local diagnostics.

Local verification on Windows x64 on 2026-10-21 used Node 24.14.1, pnpm 11.13.1, and Rust 1.95.0 for generated-contract checking. All 37 UI tests, type checking, lint, and the frontend production build passed. Existing Overview, connection, Memory, Scope and diagnostic behavior tests remain in place; Observation tests cover navigation, English/Chinese, keyboard tabs, unavailable states and the existing draft confirmation.

Headless Edge 155.0.4283.62 with synthetic IPC checked all five pages in 24 combinations of English/Chinese, light/dark/system, and CSS viewports 1180×800, 800×600, 400×400 and 400×300. The pages had no horizontal overflow. At desktop widths, Settings & diagnostics had the same coordinates with and without the Observation entry; at small widths it remained below the main navigation. The 400×300 viewport at device scale factor 2 checks layout equivalent to 800×600 at 200%, not installed WebView zoom. Browser fixtures and mocked IPC do not establish real Server, installed-package, native IME, screen-reader or macOS qualification.

应用默认进入总览，主导航包含总览、连接、记忆、观察，“设置与诊断”位于底部。观察包含运行状态、当前 Scope 库存与用量、本次会话三个标签页，数据视图尚未可用；页面及标签切换不会执行连接检查或本机诊断。

2026-10-21 在 Windows x64 上使用 Node 24.14.1、pnpm 11.13.1 验证，生成合同检查使用 Rust 1.95.0。37 项 UI 测试、类型检查、lint 和前端生产构建通过。原有业务行为测试保留，观察测试覆盖导航、双语、键盘标签切换、不可用状态和现有草稿确认。

无头 Edge 155.0.4283.62 使用合成 IPC 检查中英文、三种主题及上述四组 CSS 视口，共 24 种组合，五个页面没有横向溢出。在桌面宽度下，有无观察入口时“设置与诊断”的坐标一致；小窗口中它仍位于主导航下方。400×300 配合设备缩放因子 2 仅检查 800×600 在 200% 下的等效布局，不代表安装版 WebView 缩放。真实 Server、安装包、原生 IME、屏幕阅读器和 macOS 仍需实际验收。


## Shared feedback and global context (#2102)

On 2026-10-22, Windows x64 local checks used Node 24.14.1, pnpm 11.13.1 and Rust 1.95.0. Focused feedback/context tests and all 50 frontend tests passed, together with typecheck, lint (including generated contracts and icons in check mode) and production build. Coverage includes pending/failed/disconnected initialization, local-state recovery without readiness probes, same-name Scope IDs, latest matching connection facts, identity/health/compatibility separation, prerequisite recovery, Scope pagination recovery, failed exact-read retries retaining the original revision, preserved drafts, local diagnostics, keyboard menus and modal focus. Existing save, unknown-write and late-response protections remain covered.

Headless Edge 155.0.4283.62 with synthetic IPC exercised all five pages in 48 combinations: connected/disconnected, English/Chinese, light/dark/system, and CSS viewports 1180×800, 800×600, 400×400 and 400×300. Long addresses and Scope IDs, permission errors, Scope selection and missing-local-CLI errors had no horizontal overflow. A forced-colors run checked pending-search feedback. Screenshots were inspected for the 800×600 Scope dialog and forced-colors loading view. The 400×300 viewport with device scale factor 2 is a layout equivalent of 800×600 at 200%, not actual installed WebView zoom.

These checks use browser fixtures and mock IPC. No real native/backend, installer, platform IME or screen-reader acceptance was executed for this change. Repository-wide `uv run --locked prek run -a` retains the existing Windows `ty check` failures involving `os.O_NOFOLLOW`, `fcntl` and `resource` in unchanged Python code; other hooks passed. Logs and browser evidence are under ignored `.artifacts/desktop-shell/`.

2026-10-22 在 Windows x64 上使用上述固定工具版本，专项测试及全部 50 项前端测试、typecheck、lint 和生产构建通过。测试覆盖加载／失败／未连接区分、恢复入口、同名 Scope 的完整 ID、最新匹配连接事实、身份／健康／兼容性分别表达、原版本精确读取重试、草稿保护及键盘焦点。

Edge 合成 IPC 检查覆盖上述 48 组配置和五个页面，包含权限及本机诊断错误、长地址和 Scope ID、Scope 弹窗，无横向溢出；另检查强制高对比度的加载反馈并查看截图。200% 为等效布局检查。本次没有执行真实原生／后端、安装包、平台 IME 或屏幕阅读器验收。仓库检查中，未修改 Python 代码的 Windows 类型检查存在上述既有错误，其余钩子通过。

## Save outcome recovery (#2102)

On 2026-10-22, mocked-IPC UI tests verified that a resolved `WriteOutcome` with `record.status: "failed"` and `forbidden` displays the same permission recovery guidance as the exception path in English and Chinese, preserves the draft and dirty state, and does not replay the write. Unknown-write coverage verifies that cancelling resubmission sends no request and explicit confirmation permits a new submission. All 16 Memory tests and all 52 frontend tests passed, along with typecheck, lint and production build using the pinned tools above. No real native/backend or installed-package acceptance was executed for this fix. Repository hooks retain the existing Windows Python `ty check` failures described above.

2026-10-22 的模拟 IPC 测试验证：确定失败的保存结果显示与异常分支一致的中英文权限恢复说明，保留草稿及未保存状态，不自动重放写入；未知写入取消再次提交时不发请求，确认后才提交。16 项 Memory 测试、全部 52 项前端测试、typecheck、lint 和生产构建通过。本次未执行真实原生／后端或安装包验收；仓库钩子仍有上述既有 Windows Python 类型检查错误。

## Help and pending-connection protection (#2102)

On 2026-10-22, the pinned Windows tools above ran all 62 frontend tests, typecheck, lint and production build successfully. Ten focused help/connection tests cover bilingual guidance, fixed-address copying, success/failure feedback, keyboard selection for manual copying, offline access, native/unknown version display, pending activation and active-connection recheck across page changes, native failure recovery, overlapping checks, explicit Scope selection and the new draft's discard guard. Existing late-write, unknown-write and editor lifetime tests remain passing.

Headless Edge 155.0.4283.62 exercised 18 combinations of English/Chinese, light/dark/system and CSS viewports 800×600, 400×400 and 400×300. Synthetic IPC delayed activation across Memory and Settings navigation; the old context stayed non-editable, completion required a new Scope, and cancellation of navigation preserved a new draft. Clipboard stubs checked the locale-specific documentation address, copy failure and manual address selection. Help, pending feedback and confirmation paths had no horizontal overflow. An additional forced-colors view was inspected. The 400×300 viewport at device scale factor 2 is a layout equivalent of 800×600 at 200%, not installed WebView zoom.

Screenshots of help copy failure and pending activation were inspected. Evidence is under ignored `.artifacts/desktop-shell/help-transition-*`. Clipboard stubs and synthetic IPC do not qualify the native clipboard, actual Server or installed WebView. No installer, platform IME, screen-reader or macOS acceptance was executed for these changes. Repository hooks retain the previously documented Windows Python `ty check` failures; the other hooks passed.

2026-10-22 使用上述固定工具版本，全部 62 项前端测试、typecheck、lint 和生产构建通过。新增 10 项专项测试覆盖双语帮助、固定地址复制与失败后的手动复制、离线入口、原生／未知版本，以及跨页激活等待、失败恢复、重叠请求和新草稿确认保护。原有晚到写入、未知写入及连接编辑器保护测试继续通过。

Edge 检查覆盖中英文、三种主题和上述三个窗口尺寸的 18 组组合。合成 IPC 与剪贴板桩验证帮助复制、跨页等待、旧上下文不可编辑、完成后显式选择 Scope 及新草稿导航取消；页面没有横向溢出，并检查强制高对比度视图及截图。200% 为等效布局检查。本次未执行原生剪贴板、真实 Server、安装包、IME、屏幕阅读器或 macOS 验收；仓库钩子仍有既有 Windows Python 类型检查错误，其余通过。

## Disconnected paths and Local Server guidance (#2102)

On 2026-10-22, the pinned Windows tools above passed all 68 frontend tests, typecheck, lint and production build. Six focused UI tests cover the English/Chinese disconnected Memory choices, focused navigation to the Local Server section, the existing connection editor handoff, Settings access during pending/failed native-state loading, unchanged Overview/default navigation, the editor's discard guard and exact Scope selection for an already active connection. No installer, connection activation, readiness check or local diagnostic is triggered by these navigation paths.

Headless Edge 155.0.4283.62 with synthetic IPC checked 72 combinations: English/Chinese, light/dark/system, native state disconnected/loading/error, and CSS viewports 1180×800, 800×600, 400×400 and 400×300. Both disconnected paths worked, the targeted Local Server heading received focus and remained unobscured, keyboard Tab/Enter reached the connection editor, and the pages had no horizontal overflow. A forced-colors offline view was also checked. Screenshots of the 800×600 disconnected entry and focused Local Server section were inspected. Evidence is under ignored `.artifacts/desktop-shell/local-server*`.

The 400×300 viewport at device scale factor 2 checks layout equivalent to 800×600 at 200%, not installed WebView zoom. Synthetic IPC does not establish real Server, installed-package, IME, screen-reader or macOS acceptance; those were not executed for this change. Repository hooks retain the previously documented Windows Python `ty check` failures; the other hooks passed.

2026-10-22 使用固定工具版本，全部 68 项前端测试、typecheck、lint 和生产构建通过。新增 6 项测试覆盖双语双路径、Local Server 定向焦点、现有连接编辑器接入、原生状态加载／失败时离线可达、默认总览及原有草稿／Scope 保护。进入这些页面不会安装软件、激活连接、执行诊断或探测 readiness。

Edge 合成 IPC 检查覆盖上述 72 组组合；定向焦点未被顶栏遮挡，键盘可继续进入连接编辑器，无横向溢出，并检查了离线强制高对比度视图及截图。200% 为等效布局验证。本次未执行真实 Server、安装包、平台 IME、屏幕阅读器或 macOS 验收。仓库钩子仍有既有 Windows Python 类型检查错误，其余通过。

## Pending Scope selection and targeted profile navigation (#2102)

On 2026-10-22, the pinned Windows tools above passed all 78 frontend tests, typecheck, lint and production build. Ten focused regressions cover closing pending Scope selection with Escape, the close button or backdrop; old-context editing remains paused, success updates the exact Scope, and a failure after closure appears in shared error feedback. A late Scope result cannot clear a new connection editor's draft guard. Tests also cover cancelling Scope selection before dispatch, opening the requested profile without activating it, cancelled cross-page/same-page profile navigation, repeated selection of the same profile ID, and a late save from a replaced editor.

Headless Edge 155.0.4283.62 exercised 48 synthetic-IPC combinations: English/Chinese, light/dark/system, successful/failed delayed Scope selection, and CSS viewports 1180×800, 800×600, 400×400 and 400×300. After Escape, focus returned to the page heading and old-Scope editing stayed disabled. Navigation opened the requested configuration; pending Scope completion preserved the new editor's draft confirmation. Confirm/cancel, same-ID reselection, and Memory draft navigation were exercised. Profile selection triggered no connection check, activation or save. These paths had no horizontal overflow. Pending-Scope and forced-colors error/editor screenshots were inspected; evidence is under ignored `.artifacts/desktop-shell/scope-profile*` and `scope-pending-closed-800.png`.

The 400×300 viewport at device scale factor 2 is layout equivalent to 800×600 at 200%, not installed WebView zoom. These UI checks use synthetic IPC and do not establish real native/backend, installed-package, IME, screen-reader or macOS qualification; those were not executed for this fix. Rust IPC and persistence formats remain unchanged. Repository hooks retain the previously documented Windows Python `ty check` failures; the other hooks passed.

2026-10-22 使用固定工具版本，全部 78 项前端测试、typecheck、lint 和生产构建通过。新增 10 项回归覆盖 Scope 等待时通过 Escape／关闭按钮／遮罩关闭、旧上下文暂停编辑、成功与失败恢复、晚到结果不清除新连接草稿保护，以及指定配置导航、同页／跨页取消、同 ID 重选和旧编辑器晚到保存。

Edge 合成 IPC 验证上述 48 组组合，检查焦点恢复、指定配置接入、Scope 晚到响应后的草稿保护、确认／取消、同 ID 重选及无隐式连接检查；页面无横向溢出，并检查了等待状态与强制高对比度错误视图截图。200% 为等效布局验证。本次未执行真实原生／后端、安装包、IME、屏幕阅读器或 macOS 验收。仓库钩子仍有既有 Windows Python 类型检查错误，其余通过。

## macOS application navigation and confirmations

On 2026-10-22, macOS 26.5.1 Apple Silicon checks used Node 24.14.1, pnpm 11.13.1 and Rust 1.95.0. Frontend behavior tests, typecheck, lint, generated-contract/icon checks, Rust fmt/clippy and native tests pass. Confirmation tests operate the application dialog rather than replacing a host dialog: they cover bilingual cancel/confirm, Escape, focus containment, retained drafts, two-stage connection removal, unknown-write resubmission, and Scope changes while confirmation is pending.

The actual debug `.app` was built with `pnpm --dir desktop tauri build --debug --bundles app --ci -- --locked`. Its bundled `tauri://localhost` page loads without Vite and its native state/version IPC works. In that packaged WebView, English/Chinese draft navigation and cancel-editing confirmations, explicit discard, Escape cancellation, Tab/Shift+Tab containment, and an 800×600 English confirmation were exercised. After clicking a local diagnostic and then the Scope trigger, Escape restores focus to the Scope trigger. Pending Scope selection and nested confirmation remain covered by UI behavior tests with simulated IPC; no real Server was used for these packaged UI checks.

The native navigation policy permits the platform's local application origin and rejects external, other-platform, credential-bearing and explicitly ported packaged addresses. macOS uses `tauri://localhost`; Windows uses `http://tauri.localhost`. This does not qualify signing/notarization, Intel Mac, IME, VoiceOver, native 200% zoom, Keychain, local CLI diagnostics, or installed Server workflows.

2026-10-22 在 macOS 26.5.1 Apple Silicon 上，使用上述固定工具完成前端行为测试、类型检查、lint、生成合同／图标检查及 Rust fmt、clippy 和原生测试。确认测试实际操作应用内对话框，覆盖双语确认／取消、Escape、焦点循环、草稿保留、两步移除配置、未知写入再次提交，以及确认期间 Scope 改变时不提交旧草稿。

实际构建并启动 debug `.app`，其 `tauri://localhost` 页面不依赖 Vite，原生状态和版本 IPC 正常。在打包 WebView 中验证了双语草稿导航、取消编辑、确认丢弃、Escape 取消、Tab／Shift+Tab 和 800×600 英文确认框；先点击本机诊断再点击 Scope，Escape 后焦点返回实际 Scope 按钮。等待中的 Scope 和嵌套确认由模拟 IPC 的行为测试覆盖；本次打包 UI 检查未连接真实 Server。

导航白名单按平台仅放行本地应用 origin，同时拒绝外部、其他平台、带用户信息或显式端口的打包地址。上述结果不代表签名／公证、Intel Mac、IME、VoiceOver、原生 200% 缩放、Keychain、本机 CLI 诊断或安装版 Server 工作流通过。

## Installed confirmation automation

On 2026-10-22, Windows x64 checks used the pinned Node 24.14.1, pnpm 11.13.1 and Rust 1.95.0 tools. The installed acceptance harness checks the exact accessible description of the application's `alertdialog`, clicks its localized cancel/confirm button through WebDriver and waits for that prompt to leave. A different prompt fails acceptance. Consecutive discard/removal prompts are handled independently. Single-instance acceptance also checks that cancelled navigation retains the connection draft and confirmed navigation reaches Overview. Lifecycle acceptance is included explicitly in the CI lint, formatting and Windows/Linux type checks.

Edge and Edge WebDriver 155.0.4283.62 exercised these harness methods against the current Vite UI with synthetic IPC. Calling the native alert endpoint while the application dialog was open reproduced HTTP 404 with `no such alert`. Application-dialog checks passed for rejected prompt mismatches, draft navigation cancellation/confirmation, nested Scope selection, disconnect cancellation/confirmation, consecutive profile-removal prompts and unknown-write resubmission. Cancelling the latter dispatched zero writes; explicit confirmation dispatched one. Evidence is under ignored `.artifacts/desktop-shell/confirmation-webdriver-results.json`.

All 37 focused confirmation/connection/Memory/Scope tests and all 82 frontend tests passed, together with typecheck, lint, generated-contract/icon checks and production build. All five fixture qualification regressions passed. Repository-wide `uv run --locked prek run -a` passed every hook except the existing Windows Python `ty check` failures in unchanged code (`os.O_NOFOLLOW`, `fcntl`, `resource` and `os.mkfifo`); its log is under `.artifacts/desktop-shell/confirmation-prek.log`. This browser execution uses synthetic IPC and does not qualify native single-instance launches, actual Server writes or the installed package. The complete `windows-smoke.ps1` installation workflow requires a fresh hosted CI run. Platform IME and screen-reader qualification remain pending.

2026-10-22，Windows x64 使用项目固定工具版本验证安装验收脚本。脚本核对应用内确认框的完整无障碍描述，通过 WebDriver 点击“取消”或“确认”，并等待当前提示离开；提示不符时验收失败，连续的丢弃／移除确认分别处理。单实例场景继续验证取消导航后连接草稿保留、确认后进入总览；生命周期脚本已显式纳入 CI 静态检查。

真实 Edge 与 Edge WebDriver 155.0.4283.62 操作当前 Vite 页面，IPC 使用合成数据。应用内确认框打开时，原生 alert 接口返回 `404 no such alert`。修正后的脚本通过提示不匹配拒绝、草稿导航、嵌套 Scope 确认、断开连接、连续移除确认及未知写入确认检查；未知写入取消时请求数为零，明确确认后为一。37 项专项测试、全量 82 项前端测试、typecheck、lint、生成检查和构建通过；五项 fixture 回归通过。仓库全量钩子仍有未修改 Python 代码的 Windows 类型检查错误，其余钩子通过。这些证据不代表原生单实例、真实 Server 或安装包验收通过；完整安装流程仍需托管 CI 重跑，IME 和屏幕阅读器保持待验收。

## Harness coverage and navigation policy

On 2026-10-23, the Windows CI harness checks use one PowerShell-expanded list: all five `desktop/tests/installed_*.py` modules, including `installed_boundaries.py`, plus `desktop/tests/fixture_qualification.py`. All six files passed Ruff lint, format checks and Windows/Linux `ty check` using the exact file selection and argument splatting from the workflow. All five fixture qualification regressions passed. Future `installed_*.py` modules enter the same four checks automatically.

Each platform branch of the packaged navigation allowlist explicitly requires no non-default port. The rejection table covers both `http://tauri.localhost:8000` and `tauri://localhost:8000`, alongside the existing origin and user-info restrictions. On Windows x64 with Rust 1.95.0, the focused navigation regression, all four IPC tests, `cargo fmt --check` and `cargo clippy --locked --all-targets -- -D warnings` passed. These are native policy/mock-IPC checks; no installer, real Server, macOS or Android execution was performed for this change.

2026-10-23，Windows CI 的四项 harness 静态检查共用 PowerShell 展开的文件清单：五个 `desktop/tests/installed_*.py` 模块（包含 `installed_boundaries.py`）及 `fixture_qualification.py`，共六个文件。按工作流相同的文件选择和数组参数传递方式，Ruff、格式检查及 Windows／Linux 类型检查全部通过，五项 fixture 回归通过；后续新增的同名前缀模块自动进入四项检查。

打包导航白名单的各平台分支均明确要求无非默认端口，拒绝表同时覆盖 HTTP 和 Tauri 地址的 `:8000`。Windows x64 使用 Rust 1.95.0 完成专项导航回归、全部四项 IPC 测试、fmt 和全目标 clippy。此处为原生策略及 mock IPC 验证，未执行安装包、真实 Server、macOS 或 Android 验收。

## Reopened profile drafts and same-page Scope recovery

On 2026-10-23, Windows x64 checks used Node 24.14.1, pnpm 11.13.1 and Rust 1.95.0 for generated-contract checking. A profile revision refresh resets only a clean connection editor. A new draft entered after reopening the same profile remains intact when the earlier save completes, including when its native state clears the active connection. The new draft retains its discard confirmation, and explicitly cancelling editing loads the saved revision. Scope recovery restores focus to the page title even when Connections is already the current page; its existing draft is preserved.

Regression tests reproduced draft replacement and body focus before the fixes. All 38 focused connection/confirmation/Scope/feedback tests and all 86 frontend tests passed, together with typecheck, lint, generated-contract/icon checks and production build. Headless Edge 155.0.4283.62 with synthetic IPC passed eight combinations of English/Chinese, 800×600/400×400 CSS viewports and save responses retaining/clearing the active connection. Checks cover pending-save reopening, preserved new drafts and discard guards, explicit editor reset, same-page recovery focus and absence of horizontal overflow. Evidence is under ignored `.artifacts/desktop-shell/review-draft-focus-results.json`. This does not qualify native persistence, the installed package, IME or screen-reader behavior.

2026-10-23，Windows x64 使用项目固定工具版本验证：配置版本更新仅同步没有新草稿的编辑器；保存期间重新打开同一配置后输入的新草稿及离开确认不会被旧响应覆盖，响应清除活动连接时也适用。明确取消编辑后加载已保存版本。Scope 恢复入口即使留在连接页，也将焦点恢复到页面标题并保留已有草稿。

回归测试在修复前复现了草稿被覆盖及焦点落到 body。38 项专项测试、全量 86 项前端测试、typecheck、lint、生成检查与构建通过。Edge 合成 IPC 检查覆盖中英文、800×600／400×400 及两种活动连接结果，共八组；草稿、确认、重置及恢复焦点正常，页面无横向溢出。此证据不代表原生持久化、安装包、IME 或屏幕阅读器验收通过。

Repository-wide `uv run --locked prek run -a` passed all hooks except the existing Windows Python `ty check` failures in unchanged code; see ignored `.artifacts/desktop-shell/review-draft-focus-prek.log`. 仓库全量钩子仍有未修改 Python 代码的既存 Windows 类型检查错误，其余钩子通过。

## Desktop integration with master 1da99747

On 2026-10-23, Windows x64 integration checks used Node 24.14.1, pnpm 11.13.1 and Rust 1.95.0. Packaged navigation follows master's macOS-specific origin policy, while mock IPC retains Tauri's runtime origin selection. Navigation tests preserve both branches' rejected addresses, explicit port rejection, allowed application paths and rejection of the other platform's origin. Master's native credential backends and the shared layout's draft/focus protections remain in place.

All 86 frontend tests, typecheck, lint, production build, generated-contract/icon checks and IPC export checking passed. Rust fmt, all-target clippy, all four IPC tests and all nine transport tests passed. All six installed-harness Python files passed Ruff lint/format and Windows/Linux type checks; all five fixture qualification regressions passed. Repository hooks passed except the existing Windows Python type errors in unchanged code; see ignored `.artifacts/desktop-shell/merge-master-prek.log`.

The bundled contract digest `5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c` is qualified by [Server 1da99747](#server-1da99747-protocol-qualification). The complete Rust test suite, including `bundled_qualification_allows_explicit_scope_browsing`, passes with this record. Installed Windows and macOS workflows require their separate acceptance checks.

2026-10-23，合入 master `1da99747` 后，Windows x64 的全量 86 项前端测试、类型检查、lint、构建、生成合同／图标与 IPC 导出检查通过；Rust fmt、全目标 clippy、四项 IPC 测试及六个验收 Python 文件的静态检查、五项 fixture 回归通过。导航测试同时保留 master 的平台策略与本分支的端口、跨平台来源和应用路径保护，原生凭据支持及草稿／焦点修复均保留。

九项传输测试全部通过。当前合同由上述 Server 1da99747 真实协议验收记录资格化，完整 Rust 测试（含随包兼容资格用例）通过。仓库全量钩子仍有未修改代码的既存 Windows Python 类型错误，其余钩子通过。上述结果不代表安装包或 macOS 验收通过。

## Remaining release qualification

- Clean standard-user Windows 11, including absent/present WebView2 and bootstrap recovery.
- Publisher signing and release ownership.
- Actual IME, screen reader, high contrast and complete keyboard accessibility.
- Notification cold activation, independent service login/reboot behavior and a named Agent host's capture/recall workflow.
- Maintainer ownership and approved performance budgets.

Hosted Windows CI and developer-machine checks do not replace these platform and product gates. This preview does not close #1654 or #1428.

## macOS native validation

Run on each target architecture with the pinned tools listed in the [macOS build prerequisites](README.md#build-on-macos),
including JDK 11 or newer with `java` on `PATH`. Both `lint` and `build` invoke the Java OpenAPI
Generator through `generate:check`; its first run may require downloading the pinned JAR from
Maven Central. A preinstalled JDK on a CI image does not replace this local prerequisite.

```sh
cd desktop
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path src-tauri/Cargo.toml
pnpm ipc:check
cargo run --locked --manifest-path src-tauri/Cargo.toml --example credential_probe
pnpm desktop:build
open "src-tauri/target/release/bundle/macos/PowerContext Desktop Preview.app"
```

Check that the Home page renders, Settings shows the native host ready, and a missing diagnostic
registration produces the missing-CLI state. The synthetic native Keychain probe verifies persistence
across processes and removes its own entry. Native process tests exercise output limits, timeout,
cancellation and descendant termination. The registered-launcher test executes a real synthetic CLI
and checks digest rejection and allowlisted projection; it does not qualify a real CLI installation.

Use a disposable Server and Scope for installed save/search/read/copy checks. Unit tests do not prove
those installed workflows. Validate actual Chinese IME, accessibility, Gatekeeper, signing and
notarization independently before distributing outside the internal preview.

## Existing Server connection journey validation

On 2026-10-28, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 validated the connection
journey working tree based on `aedf7beb1ecff2ea1fade6ccdd8e4bf6a298f799`.
All 130 frontend tests passed, together with TypeScript, lint, generated-contract/icon drift,
formatting and the production build. The English/Chinese first-use scenarios exercise save,
passive check, explicit activation, exact selection among duplicate Scope titles, keyboard
focus and reopening without automatic activation. Other regressions cover cancelled/confirmed
drafts, partial operation support, rejected stale states, late Scope pages and preserving the
current page during global Scope selection. UI tests use jsdom with mocked native IPC.

The native real-Server probe additionally asserts that both saving and passive checking leave
`active` unset before explicit activation and exact Scope selection. The full SQLite harness
passed anonymous loopback, static Bearer, HTTPS with an explicit CA/base path and enforced
Provider, followed by save/search/exact reads, unknown-write handling and identity/revocation
checks. Server source is `aedf7beb1ecff2ea1fade6ccdd8e4bf6a298f799`; wheel
`powercontext-1.4.2.dev36+gaedf7beb1.d20261028-py3-none-any.whl` has SHA-256
`fde108a81a872a6228aad54832f2cd8f899a53bb4a7ff5c321871891fdbc5b68`.
Normalized OpenAPI SHA-256 is
`c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`;
debug `server_probe.exe` SHA-256 is
`01bc705b75f7ccb843535b884f7d2a50b7c5d8a5e9d8432b6788dbfafe86a4bf`.
The run used the explicit disposable fixture qualification with a dirty Desktop working tree;
production qualification records are unchanged.

Rust formatting, Clippy and IPC drift checks passed. Seven Python contract/fixture tests passed.
Repository file hooks passed; default Windows `ty check` retains existing POSIX-only attribute
diagnostics in unchanged Python files, while the Linux-target type check passed.
The installed WebDriver workflow now expects activation to open the Scope dialog, exercises
that dialog directly and verifies cleared Memory after selection. That updated installed flow
was not executed locally. Native WebView/credential behavior, IME, screen readers, small windows,
scaling and signed Windows 11/macOS acceptance remain platform qualification work. The approved
local setup/default-Scope handoff remains separate work with #2117; this does not close #2103.

## Independent Ops bridge checks

The native Ops tests exercise streaming with real child processes, malformed/oversized output, deadlines, inherited
pipe handles, retained references, one-shot approvals, cancellation, resume, binding integrity and Quit continuation.
Synthetic helpers establish these bridge boundaries; they do not qualify native service-manager mutations.

Two opt-in macOS tests add distinct read-only integration evidence. Prepare the checkout's `ops` dependencies and run
from the repository root:

```sh
uv sync --locked --extra ops --extra cli
POWERCONTEXT_OPS_TEST_PYTHON="$PWD/.venv/bin/python" \
  cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml \
  --lib real_current_ops_inspect_and_preflight_with_missing_server_runtime -- --ignored
```

This test runs the current Python Ops entry in a subprocess with a disposable home, binding and missing Server Python.
An unavailable native-manager ownership observation must remain a rejected preflight, not become successful cleanup.
The following test instead copies a complete independently built Ops bundle into a disposable native bridge layout,
uses the separately supplied approved manifest digest as its packaged pin, and checks inspection/preflight and dependency
tampering through the actual native verifier and process runner:

```sh
POWERCONTEXT_OPS_TEST_BUNDLE=/absolute/approved/ops \
POWERCONTEXT_OPS_TEST_BUNDLE_SHA256="<approved-bundle-json-sha256>" \
  cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml \
  --lib actual_independent_bundle_runs_through_native_trust_and_read_only_bridge -- --ignored
```

Neither command applies a service operation or touches the real user's Server configuration/data. Supply a trusted
producer artifact and an independently approved digest; calculating a pin from an arbitrary candidate does not establish
release trust. These checks do not establish signing/notarization, Windows Task Scheduler or macOS LaunchAgent mutation,
packaged provisioning, or clean-machine acceptance. The optional packaging hook and stable independent helper location
are documented in [Local Server Ops](../docs/en/docs/operate/local-server-ops.md#provisioning-the-bridge).

上述两项可选检查分别验证源码 Ops 子进程和完整独立分发包与原生桥接的只读兼容性。测试使用临时 home、绑定和状态目录，
不执行服务修改；真实服务管理器归属无法确认时保留预检拒绝。分发包测试要求另行提供已批准的清单摘要，并检查依赖被改动时
原生端拒绝启动。这些证据不代替签名／公证、真实服务修改、随包配置或干净机器验收。

## Server d6820d88 protocol qualification

`sqlite-artifact-d6820d88-v1` identifies Server sources at `d6820d884ccd240712df477e459622f60fd6f5f5`, contract digest `f571a95d193f0dae91694ffcd6b37c93603a5ae731fddefbcdbfa9a5914762ae`, and wheel digest `5bcedfde3efa4ae028318a9b8848dec44b5604ac6f636f32bacb637ca5729f52`. The wheel `powercontext-0.1.dev0+gd6820d88-py3-none-any.whl` was built from a clean Git archive of that commit with an explicit archive-build version; all 563 runtime files match the archived sources byte for byte.

On 2026-10-26, the native `ConnectionManager` and HTTP adapter passed all four built-wheel SQLite fixture modes: anonymous loopback, Bearer, HTTPS with an explicit CA/base path, and injected-provider/enforced access. The unchanged `server_probe.rs` assertions cover writes, search, exact historical reads, lost-response writes without replay, identity changes, revocation, and Scope pagination. The qualification records the same ten Desktop operations as the preceding profile; it does not qualify the new plugin administration operations.

Validation used Linux, Python 3.12.12 and Rust 1.95.0. The unchanged connection, credential and transport modules and probe were built in an isolated native crate without Tauri/UI dependencies. On the loaded host, importing the Server took about 60 seconds, so the local fixture preparation allowance was 180 seconds instead of 20; protocol assertions and product timeouts were unchanged. This establishes native SQLite protocol compatibility, not full Tauri, installed-package, Windows/macOS service, vault or UI acceptance.

#### Source and Artifact merge 87b6fae2 protocol qualification

`sqlite-source-artifact-87b6fae2-v1` identifies the uncommitted merge of Artifact HEAD
`079de1030ec0404ad4bba5f31d2d979078a55459` and mainline
`0c1dfc289b28e9f529555fb57bf771ce51103913`, including migration compatibility adjustments.
Its `serverCommit` is a working-tree identity, not a Git commit. Source snapshot SHA-256:
`87b6fae2104aa599f626afff7647be37711151bbc8f8329257c4cab532516f55`.
This hashes sorted `path sha256` lines, each ending in a newline, for all packaged runtime
files at their `src/` paths plus `pyproject.toml`, `uv.lock` and `openapi/powercontext.yaml`.
All 663 packaged runtime files match the source byte for byte.

OpenAPI SHA-256: `37936d7897ec2d981dcc195d3b7b7a9f99c572d866c0193b37df43617077c155`.
Wheel: `powercontext-1.4.1.dev273+g079de1030.d20261031-py3-none-any.whl`.
Wheel SHA-256: `9915db1a7341622d8f22d97b1c02aa4cbd50a7c3a3be3dc9d9346cb77b3da56f`.
The development version is build metadata; the formal release version is unchanged.

On 2026-10-31, Linux x86_64, Python 3.12.12 and Rust 1.95.0 passed all four built-wheel
SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer, HTTPS with an
explicit CA/base path, and injected-provider/enforced access. The existing native probe
covers the eleven declared protocol operations, including server identity, save/search/exact
reads, Scope selection, identity changes and revocation, lost responses without replay,
and Server survival after client exit. Plugin management is outside this qualification.

The product native protocol modules and probe were copied unchanged into an isolated crate
without the Tauri GUI. The local build used vendored DBus because the host lacked development
headers, and the CI fixture profile was supplied through `OUT_DIR`. The local harness wrapper
retained the 180-second fixture startup allowance; protocol assertions and product timeouts
were unchanged. This qualifies SQLite protocol behavior for this snapshot and wheel, not
Windows/macOS execution, native credential persistence, installed UI or release readiness.
Historical qualification records retain their identities and evidence.

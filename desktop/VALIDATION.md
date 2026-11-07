# Desktop validation

## Server c527f3f6 Gateway contract qualification

`sqlite-gateway-c527f3f6-v1` qualifies the thirteen generated Desktop operations against
SQLite Server source `c527f3f65c3d3a38f53399230075eceef9364078`. Gateway lifecycle schemas
are included in the authoritative OpenAPI document; Desktop's existing operation set
and native wire models remain unchanged.

- Normalized OpenAPI SHA-256: `ee13915c5a1d53a7d3f7b59416d37a22601b0bffd14430997708f19f68a8354f`.
- Wheel: `powercontext-1.5.1.dev0+gc527f3f6-py3-none-any.whl`.
- Wheel SHA-256: `bbf4227a828a8964fa5eb5eab8b08c499cd3a76fbdcf986600c3db74aeff3106`.

All 739 packaged runtime files match that exact Git archive after CRLF normalization.
The wheel was built from the archive with an explicit build version. All historical
qualification records retain their original source, contract, artifact and operation identities.

On 2026-11-07, Linux x86_64 / WSL2 / Python 3.14.4 / Rust 1.97.1 passed anonymous
loopback, static Bearer, HTTPS with explicit CA/base path, and enforced Provider modes
through the unchanged native `server_probe` and ordinary product `ConnectionManager`.
The harness selected the bundled product record and verified the actual wheel hash before
starting its isolated real Python Server processes; no fixture qualification was injected.
All four modes preserve committed-write response loss as unknown without replay and leave
the Server alive after client exit. The Provider mode additionally passes identity-change
invalidation, same-identity revocation, 51-Scope pagination, and scoped native authorization
when Server discovery is denied. These are local protocol checks, not installed UI, system
vault, Windows, macOS, external-provider, or production-deployment acceptance.

```sh
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --no-sync python desktop/tests/real_server.py \
  --product-qualification sqlite-gateway-c527f3f6-v1 \
  --wheel desktop/.artifacts/server-wheel/powercontext-1.5.1.dev0+gc527f3f6-py3-none-any.whl
```

The debug native probe SHA-256 is
`347ac20bbdf1574776e16c487a713afae263de05b4d602d916348a76f685bcda`.
The report identifies source `c527f3f6` with the new qualification record in the working
tree. Raw reports, per-mode Server logs, the source-equivalence report, and the exact wheel
are retained under ignored `desktop/.artifacts/`.

## Gateway contract authenticated legacy qualification

The `authenticatedClients` entries for `sqlite-atomic-112db11e-v1` and
`sqlite-atomic-3cefa45a-v1` qualify their original eleven operations for client contract
`ee13915c5a1d53a7d3f7b59416d37a22601b0bffd14430997708f19f68a8354f` in Bearer mode.
Their original Server commits, Server contract digests, artifact hashes, operations and
evidence remain unchanged. Anonymous legacy use still requires an upgrade.

On 2026-11-07, the unchanged native probe passed static Bearer, HTTPS with explicit CA/base
path, and enforced Provider modes for each historical Server on Linux x86_64 / WSL2 /
Python 3.14.4 / Rust 1.97.1. The existing explicit rebuilt-source fixture path compared every
packaged runtime file with each exact Git archive before execution. The rebuilt wheels are
distinct fixture artifacts; these runs do not qualify them as the original product wheels.

| Historical Server source | Rebuilt fixture wheel | Actual fixture SHA-256 |
| --- | --- | --- |
| `112db11e885c38b997da2177e03a4065a7e35dc3` | `powercontext-1.5.1.dev0+g112db11e-py3-none-any.whl` | `567d0cd4a4c7488d6ba76325f0652593a9fddf8782eb0008e75b2b88a76c5c87` |
| `3cefa45ad08490423391148439c9eb73f1d48f58` | `powercontext-1.5.1.dev0+g3cefa45a-py3-none-any.whl` | `9831d1e045b21267d3cd6d6c04fada5f54f2180013c0de9aaf477fa6861a8f6b` |

```sh
uv run --no-sync python desktop/tests/real_server.py \
  --rebuilt-qualification sqlite-atomic-112db11e-v1 \
  --wheel desktop/.artifacts/legacy-112db11e/powercontext-1.5.1.dev0+g112db11e-py3-none-any.whl
uv run --no-sync python desktop/tests/real_server.py \
  --rebuilt-qualification sqlite-atomic-3cefa45a-v1 \
  --wheel desktop/.artifacts/legacy-3cefa45a/powercontext-1.5.1.dev0+g3cefa45a-py3-none-any.whl
```

Aggregate reports and harness output are retained under ignored `desktop/.artifacts/g3-legacy-*`.
Per-mode Server logs and process reports are under `desktop/.artifacts/real-server-*/`;
the process reports identify each fixture by its wheel SHA-256.
The probe identity and platform limits are the same as in the current product qualification above.

The native regression suite also passes with these product and authenticated-client records:

```sh
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --tests
```

One existing case is explicitly ignored. The passing suite includes the legacy compatibility
regressions and bundled-product assertions; it does not establish installed GUI or platform acceptance.

## Server 7d89a97a identity Runtime and statistics qualification

`sqlite-identity-stats-7d89a97a-v1` qualifies thirteen Desktop operations against
SQLite Server source `7d89a97a3d417326c44b5dd79810d9e9d68f0d4f`, including identity,
Memory, statistics and the existing HTTP operations used by Runtime observations.
The source includes master `20f64d72` and its managed observability contracts.

- Normalized OpenAPI SHA-256 is `9aabe2565d9cbd36075be03a83b7370e07e38e9f25232e20dd47812aba65e508`.
- The wheel is `powercontext-1.5.1.dev0+g7d89a97a-py3-none-any.whl`.
- Wheel SHA-256 is `bdd26c664afdf966269d2eafb403a7ba05c740837577e2ac954e01878b843d7a`.

All 738 packaged runtime files match the exact Git archive after CRLF normalization.
The wheel uses an explicit archive-build version. Historical qualification records
retain their original source, contract, wheel and operation identities.

On 2026-11-05, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 passed anonymous loopback,
static Bearer, HTTPS with explicit CA/base path and enforced Provider modes through
the ordinary product manager on this wheel. The probe exercises explicit readiness,
Runtime liveness/capabilities/readiness, exact Scope selection, Memory save/search/read,
all statistics periods, unknown usage, uncertain committed writes without replay,
identity changes, revocation and pagination. The scoped Provider scenario preserves
native identity, Memory and statistics when real Server discovery returns 403.

The product run uses debug probe SHA-256
`df500eb0df2447f8dccccd9d809f314e2253fa39fb6e2418153b39ae666c725c`.
It records Desktop parent `7d89a97a` with its qualification record added in the working
tree. Reports and exact wheel identity are retained under ignored
`.artifacts/nov5-followup-current/` and `.artifacts/nov5-followup-product.log`.
Usage seeding supplies synthetic fixture input. Installed UI and platform acceptance
require their corresponding execution evidence.

```sh
python desktop/tests/real_server.py --product-qualification sqlite-identity-stats-7d89a97a-v1
```

Separate `authenticatedClients` evidence covers the original eleven operations of
`sqlite-atomic-112db11e-v1` and `sqlite-atomic-3cefa45a-v1` under this client contract.
Both pass static Bearer, HTTPS CA/base path and Provider modes through the explicit
rebuilt-source fixture path, including Runtime observations supported by those Servers.
Every runtime file is compared to the record's exact Git source before execution.
The original wheel artifacts were not rerun. Anonymous legacy access requires upgrade.

The model usage recorder suite passes 32 tests on Python 3.11.15 and 32 on Python
3.14.7. Its real SQLite slice-expiry scenario also passes ten consecutive runs on
Python 3.14.7. The test retains first-attempt expiry, retry, single recorded request
and restored-connection assertions, with enough write and flush budget for the retry.
The CI workflow and migration runner match master `20f64d72`, including three SeekDB
shards with independent 180-second cases and the selected OceanBase 300-second cases.
Local execution of backend services and the complete Linux acceptance matrix remains
separate from these unit and protocol checks.

The merged native suite passes 114 tests plus the spawned-process proxy recheck.
All 195 frontend tests, 104 focused Python contract/identity/model-usage tests, Clippy,
IPC/API/JS generation checks, frontend lint/type checking/build and integration bundle
checks pass. DSH passes 448 tests with one platform skip, OpenCode passes 110 and Cindy
passes 20. CI-selection/runner checks pass 143 tests with twelve Linux-only skips.
The disposable real-Server fixture also passes all four modes.

Windows checks encounter 88 platform diagnostics and twelve diagnostics from the
baseline's `deploy/observability/stats-proxy.py` symlink checked out as a text placeholder.
Linux-target type checking passes with that placeholder excluded while the actual
`src/powercontext/observability/resources/stats-proxy.py` source is included. This local
command does not establish the complete required Linux CI result.

当前源码的同一固定 wheel 已通过四种连接模式的身份、Memory、统计及 Runtime 观察联合验证。
两个旧版保留原始十一项操作，当前已认证客户端证据分别使用源码逐文件核对后的重建测试资格。
真实 SQLite 的首次超时重试测试在 Python 3.14 连续十次通过，保留单次记录和连接恢复断言。
CI 分片和时限采用当前基线配置，完整后端与平台结果以对应远端运行记录为准。

## Server afa13bcb identity and statistics qualification

`sqlite-identity-stats-afa13bcb-v1` separately qualifies all thirteen current Desktop
operations, including `get_connection_identity`, Memory and `get_stats`, against the
same SQLite Server artifact:

- Server source: `afa13bcb1d3c1b11d3f672f53d00254588089dca`.
- Normalized OpenAPI SHA-256: `6d7cc69254c757895f82a5f71db1676f35fb85d97c268a806679180d38068f18`.
- Wheel: `powercontext-1.5.1.dev0+gafa13bcb-py3-none-any.whl`.
- Wheel SHA-256: `04192db2416fef6786f659088f8da0393e34b8675d1466d7757f6ec61fcd1d25`.

The existing twelve-operation identity record and all historical statistics records
retain their exact identities, operation sets and evidence. Statistics uses an existing
HTTP operation on this Server; no new Server build is inferred from the Desktop change.
The 686 runtime files match the exact source archive after CRLF normalization.

On 2026-11-05, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 passed anonymous loopback,
static Bearer, HTTPS with explicit CA/base path, and enforced Provider modes through the
ordinary product manager. All modes exercise native Memory save/search/exact reads and
Today/7-day/30-day statistics on this same wheel, preserving unknown token usage and
committed-write response loss without replay. The Provider case additionally grants a
reader Scope contributor access without `server.observe`: real Server discovery returns
403, while native identity checking, exact Scope selection, Memory save/search/read and
statistics succeed. Revocation under the same Principal then denies access. The harness
requires the native probe to attest this scoped scenario; an older executable cannot
silently satisfy it. Provider usage seeding is synthetic fixture input, not an actual
model-provider call.

The product harness hashes the actual wheel before extraction or Server startup and
extracts the same immutable bytes it reports. A substituted legacy wheel is rejected
with `product_qualification_mismatch`. Product mode also rejects fixture-only probes.
`--rebuilt-qualification` instead compares every runtime file against the selected
record's exact Git source and uses a separately identified injected fixture record.
Those source-equivalence results cannot qualify a different wheel as the original
product artifact. Earlier rebuilt-wheel evidence below retains its historical execution
details and must be interpreted with this distinction.

```sh
python desktop/tests/real_server.py --product-qualification sqlite-identity-stats-afa13bcb-v1
```

The debug native probe SHA-256 for the product run is
`15892a87c94e3e43beb868ce6029fb2fca66cbf4b413a2ccf8340d7058a83b47`.
The report records Desktop parent `15c51e36` with the baseline merge and review fixes
applied in a dirty working tree. Ignored evidence is under `.artifacts/nov5-product/`
and `.artifacts/nov5-product-server.log`. These loopback protocol results do not establish
installed WebView, system-vault, signing, Windows 11 or macOS acceptance.

Native regressions preserve current-profile identity requirements, legacy discovery
fences, changed-identity invalidation, independent readiness cancellation and switching
connections during a delayed probe. UI regressions check the same Access mode in the
identity menu and connection card in both languages and permit activation during a
pending diagnostic. The complete native suite passes 110 tests plus its spawned-process
proxy recheck; all 191 frontend tests pass. Frontend tests use jsdom and mocked IPC.
The default disposable qualification also passes all four real-Server modes. The explicit
rebuilt-source path passes three authenticated modes for source `112db11e`, reported as
`rebuilt-source-fixture` with ID `rebuilt-sqlite-atomic-112db11e-v1`; anonymous legacy use
remains excluded. These fixture reports are retained under `.artifacts/nov5-fixture/`
and `.artifacts/nov5-rebuilt-legacy.log`.

Seventy-two focused Python API/JS/Desktop/identity tests, generated API/JS/IPC checks,
strict Clippy, frontend lint/type checking/production build and pre-commit file hooks pass.
Linux-target type checks pass; the default Windows type hook reports 88 platform
diagnostics. Required backend and platform acceptance remains remote CI evidence.

同一固定 wheel 已联合验证十三项身份、Memory 和统计操作。强制 Provider 场景中，仅获得 Scope 权限的
用户在观测接口返回 403 时，仍通过原生身份检查、范围选择、Memory 保存／搜索／精确读取及统计；同一
Principal 的授权撤销后访问被拒绝。产品验收在启动前核对实际 wheel 摘要，重建源码的等价证据使用独立
测试资格，不能代替原产品工件验收。当前配置不会因缺失身份声明而降级；探测等待期间可以切换连接，
旧诊断结果失效。原生及前端回归通过，本机 loopback 验证不代替安装版和其他平台验收。

## Server afa13bcb current-client qualification

`sqlite-atomic-afa13bcb-v1` qualifies twelve Desktop operations for Server source
`afa13bcb1d3c1b11d3f672f53d00254588089dca`, normalized OpenAPI SHA-256
`6d7cc69254c757895f82a5f71db1676f35fb85d97c268a806679180d38068f18`, and
`powercontext-1.5.1.dev0+gafa13bcb-py3-none-any.whl`, SHA-256
`04192db2416fef6786f659088f8da0393e34b8675d1466d7757f6ec61fcd1d25`.
All 686 runtime files in `powercontext/` and `powercontext_service_bootstrap/` match the
exact Git archive after CRLF normalization. The wheel uses an explicit archive-build version.
The source includes master `63e9665c` and the readiness/connection-identity implementation.

On 2026-11-04, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 passed anonymous loopback,
static Bearer, HTTPS with explicit CA/base path, and enforced Provider modes, first with
fixture qualification and then with ordinary `ConnectionManager::new` and the bundled
product record. The existing assertions cover inactive save/passive check, explicit
readiness/activation/Scope selection, Memory save/search/exact reads, uncertain committed
writes without replay, Principal changes, revocation and Scope pagination.

Separate `authenticatedClients` entries qualify the original eleven operations of both
`sqlite-atomic-112db11e-v1` and `sqlite-atomic-3cefa45a-v1` for this client contract.
Each passed static Bearer, HTTPS with explicit CA/base path, and enforced Provider modes
through the product manager, including Principal revalidation. Their Server sources,
contracts, original wheel identities, operation sets and earlier evidence remain intact.
They do not gain `get_connection_identity` or qualify anonymous access.

The 112db11e run uses the rebuilt wheel and exact runtime manifest documented below.
The 3cefa45a run uses `powercontext-1.4.2.dev174+g3cefa45ad-py3-none-any.whl`, SHA-256
`f297342bf9b829f2a5c8891eaa120c1b8256d0a986f5720ea9caa4092a027738`.
Its 686 runtime files match source `3cefa45ad08490423391148439c9eb73f1d48f58` byte for byte,
with the same sorted runtime manifest as the original qualification:
`70dbff69bfbd0011fcab5389b6956ec21f015f3c01f8755ed0c200d2237f31e7`.
The rebuilt legacy archives differ from their original recorded wheels; the original
artifacts were not rerun. These are source-equivalence fixture results, not exact product-artifact qualification. Their runtime equivalence is checked against the recorded manifests. Historical reports record their original execution path; new rebuilt-wheel runs use explicit fixture qualification and cannot assert bundled-product evidence.

The debug native probe SHA-256 is
`c60fd8aa31c1f72cb92eb7bbb65e8bdcdf72c57457797f50225600e0435875cd`.
The Desktop catalog was dirty; Server runtime files come from exact archived sources.
Ignored reports are retained under `.artifacts/nov4-current/`, with wheel identities in
that directory and `.artifacts/nov4-3cefa45a/`. These results qualify SQLite protocol
behavior, not installed WebView, system credentials, signing or Windows 11/macOS acceptance.

The complete native suite passes 99 tests plus its spawned-process proxy recheck, and
all 173 frontend tests pass. The real child-process module includes the baseline's atomic
PID readiness-file publication. API/JS/IPC generation, Clippy, frontend lint/typecheck/build,
pre-commit file hooks and Linux-target type checks pass. The default Windows type hook
reports 88 platform diagnostics. Required remote upgrade/CI gates remain separate evidence.

本记录绑定合并基线后的准确源码、契约及 wheel，686 个运行时文件逐一核对。当前 Server 的匿名本机、
Bearer、HTTPS 自定义 CA／路径前缀和强制 Provider 四种模式，使用临时资格及普通产品管理器分别通过。
112db11e 与 3cefa45a 两个旧版的三种已认证模式也通过，保留原始资格身份和十一项操作；匿名旧版仍须升级。
旧版使用重建 wheel，运行时清单与原记录一致，未重新运行原 wheel。原生 99 项及独立进程代理复核、
前端 173 项通过；本机验证不代替远端升级门禁、安装后界面、系统凭据库、签名和平台验收。

## Authenticated Server 112db11e current-client qualification

`sqlite-atomic-112db11e-v1` retains its original Server source, contract, wheel digest,
eleven operations and evidence. Its `authenticatedClients` entry separately qualifies those
same operations for Desktop contract
`32d75b58058ae0a9a3e159f47d67ab421649cefe86f662e12e75b4215f1f35d9`.
A saved selection stays available in Bearer mode after checking a valid Principal; this
entry cannot qualify anonymous access or add `get_connection_identity`. Other historical
records remain unavailable without their own current-client evidence.

On 2026-11-03, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 ran the ordinary product
`ConnectionManager::new` against SQLite Server source
`112db11e885c38b997da2177e03a4065a7e35dc3`, using a rebuilt
`powercontext-1.4.2.dev132+g112db11e8-py3-none-any.whl`, SHA-256
`93be983401f6103b55f4992ffeccb14b223820125f666e16ccd1e2b26ebf30b9`.
This rebuilt archive differs from the original recorded wheel; the original wheel was not
rerun. All 679 `powercontext/` runtime files match the exact Git source byte for byte,
and their sorted manifest digest matches the original qualification:
`b74203e2071a6216813da33f26e05baf8b9fdbbb666f1b7d7bbdd15a5cd69707`.
The Server OpenAPI digest remains
`83dc68194770a41facea55a3fe2279bc0e8b01812a82a1d838334232c67bedc7`.
The original qualification identities have not been replaced by the rebuilt artifact.

Static Bearer, HTTPS with explicit CA/base path, and Provider/enforced-access modes all
passed. The actual old Server returns 404 for `get_connection_identity`; the product
manager qualifies only the original eleven operations through `get_access_principal`.
The probe covers inactive save/passive check, explicit activation/readiness/Scope selection,
Memory save/search/exact reads, committed writes with lost replies without replay,
Principal changes, same-identity revocation and 51 same-title Scopes. Servers remain alive
after client exit and are shut down by the harness. Anonymous old-Server access is excluded
from this qualification and remains upgrade-required.

The debug probe SHA-256 is
`80061811653dfde0455fd4cb0064f99fc5d11a732bdcaf0de5b78edd459b2567`.
The Desktop working tree is based on `17145bb1` with the client/catalog changes applied;
Server runtime sources come from the clean old Git archive. Reports are retained under
ignored `.artifacts/nov3-legacy/` and `.artifacts/nov3-legacy-real-server.log`.
Reproduce source-equivalence fixture evidence with the rebuilt wheel at its evidence path (this does not qualify the original pinned artifact):

```sh
python desktop/tests/real_server.py --rebuilt-qualification sqlite-atomic-112db11e-v1 --wheel desktop/.artifacts/nov3-legacy/powercontext-1.4.2.dev132+g112db11e8-py3-none-any.whl
```

The bundled-profile native regression fails with the old digest gate and passes with
current-client evidence. Native regressions preserve Principal invalidation and deny
anonymous use; the UI regression preserves a saved selection only in Bearer mode.
The complete native suite passes 96 tests plus its spawned-process proxy recheck;
all 153 frontend tests pass. Eight Python contract/fixture tests, Clippy, IPC/generated drift, lint and production build pass. The current 0d89e591 Server also passes all four product-manager harness modes. All pre-commit file hooks and Linux-target type checks pass; the default Windows type hook retains its 89 platform diagnostics. Frontend tests use jsdom/mocked IPC. This evidence covers
SQLite protocol behavior; installed WebView, system vault, signed packages and actual
Windows 11/macOS acceptance require their separate checks.

该配置保留原始 Server 源码、契约、wheel、十一项操作和证据，仅追加当前客户端的独立已认证验证记录。
已保存的选择在 Bearer 模式且 Principal 有效时可用；不增加轻量身份操作，也不允许匿名旧版使用。
实际重建 wheel 的摘要与原 wheel 不同，未重新运行原 wheel；679 个运行时文件与准确旧源码逐字节一致，
其排序清单摘要也与原验证记录相同。静态 Bearer、HTTPS 自定义 CA／路径前缀和强制 Provider 三种真实
SQLite 模式均通过，覆盖完整记忆读写、响应丢失不重放、Principal 变化、撤权及 Scope 分页。
旧摘要门禁已复现回归失败，修复后通过；原生 96 项及独立进程代理复核、前端 153 项通过。
这些结果不代替安装后 WebView、系统凭据库、签名安装包和 Windows 11／macOS 平台验收。

## Diagnostic and context interaction

The Connections readiness action owns independent pending/error state. It does not enter
the global connection/Scope transition counter. Scope lookup, Memory search and Memory save
remain usable while the diagnostic is pending, including after navigating away from Connections.
Diagnostic errors are associated with the probed profile ID and revision; late completion
cannot discard a new Memory draft. Connection checks/activation retain their context-transition
protection and their native concurrency boundary.

On 2026-11-02, both delayed-probe success and rejection scenarios passed through the App
component, exercising Scope lookup, search, save and retained later input before/after probe
completion. All 152 frontend tests, TypeScript, lint/generated drift and production build passed.
These tests use jsdom and deferred mocked native IPC; installed WebView interaction is not
qualified by these results.

连接页的就绪探测独立记录等待和错误，不进入全局连接／Scope 切换计数。探测尚未完成时，即使已离开连接页，
仍可查找 Scope、搜索和保存记忆；错误只关联被探测的配置 ID 和版本，晚到结果不会丢弃新草稿。
延迟探测成功与失败两种 App 流程均已覆盖，全部 152 项前端测试、类型、lint／生成漂移及生产构建通过。
此处使用模拟原生 IPC，不代替安装后 WebView 的交互验收。

## Server 0d89e591 connection identity qualification

`sqlite-atomic-0d89e591-v1` qualifies Server source
`0d89e591f9556433f1bc741fa4ce9009359ec9c3` with normalized OpenAPI SHA-256
`32d75b58058ae0a9a3e159f47d67ab421649cefe86f662e12e75b4215f1f35d9` and wheel
`powercontext-1.5.1.dev0+g0d89e591-py3-none-any.whl`, SHA-256
`23dfb2ede0d4269c14ec91f89591f66bebd0fbe52e830f77ead846eea7e540c1`.
The wheel uses an explicit archive-build version; all 684 runtime files match the exact
Git archive after CRLF normalization. This record qualifies the same twelve operations
as the 9624761b connection-identity record below against the v1.5 contract.

On 2026-11-02, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 passed all four
built-wheel SQLite modes: anonymous loopback, static Bearer, HTTPS with explicit CA/base
path, and enforced Provider. Each mode passed both disposable-fixture qualification and
ordinary `ConnectionManager::new` using the bundled source/contract/wheel record. The
debug native probe SHA-256 was
`9eed022905f81872241ae4df0dc02511f9b66414b77ccfd4898bb40b0848a4a8`.
The Desktop catalog working tree was dirty; Server runtime sources match the recorded
clean archive. Explicit readiness, private identity/context isolation, exact Scope and
Memory workflows, lost-response writes without replay, revocation and pagination retain
their probe assertions. These results do not establish installed WebView/vault or signed
Windows 11/macOS acceptance. Existing qualification records retain their original evidence.

The synchronized branch passed all 94 native suite tests plus a spawned-process proxy
isolation recheck, 150 frontend tests, Rust Clippy, generated/IPC checks and Desktop
lint/production build. DSH passed 457 tests (one platform skip) and nine actual-Server
integration tests; Cindy passed 20 tests and package creation. Access-audit classification
passed 19 tests, with Linux-target type checks for both the root and Bub harness. The
complete Bub suite cannot collect on this Windows host because an unrelated Harbor
permission test uses POSIX `os.geteuid`; its complete Linux execution remains CI evidence.

该记录绑定 v1.5 契约及准确的源码和 wheel，684 个运行时文件已逐一核对。匿名本机、Bearer、HTTPS 自定义
CA／路径前缀和强制 Provider 四种真实 SQLite Server 模式，均使用临时资格及普通产品连接管理器分别通过。
原生套件 94 项及独立进程代理复核、前端 150 项、DSH 457 项及 9 项真实 Server 集成、Cindy 20 项及打包、
Access 审计 19 项均通过；一项 DSH 平台测试跳过。完整 Bub 套件受本机缺少 POSIX `os.geteuid` 限制，
其 Linux 运行结果须以 CI 为准。历史资格记录及安装版平台验收范围保持独立。

## Server 9624761b connection identity qualification

`sqlite-atomic-9624761b-v1` qualifies Server source
`9624761bf3e88b9275562602ef6eac5c5fc54562` with normalized OpenAPI SHA-256
`45872d54c03a235ecbab13eab346efb2fd102d8bc2937f683fbb82d40bd11edf` and wheel
`powercontext-1.4.2.dev0+g9624761b-py3-none-any.whl`, SHA-256
`609b020d37f5d93867d4a103eabfcbda8a9397546eea544cd30957386210e6d1`.
The wheel was built from that exact Git archive with an explicit archive-build version.
All 670 runtime files match the archived sources after CRLF normalization.

On 2026-11-01, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 exercised the actual
debug native adapter against independent built-wheel SQLite Server processes in four modes:
anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and enforced Provider.
All four passed first with disposable fixture evidence and then with ordinary
`ConnectionManager::new` selecting the bundled product record by its exact source,
contract and wheel identities. The twelve qualified operations are `get_connection_identity`,
`get_server_info`, `get_liveness`, `get_readiness`, `get_capabilities`, `get_access_principal`,
`list_scopes`, `get_scope`, `get_default_scope`, `remember_memory`, `search_memory`, and
`get_artifact_revision`. The existing `get_access_principal` anonymous-mode 503 remains
an expected error; explicit anonymous evidence comes only from the new identity operation.

The probe verifies saved/passively checked profiles remain inactive with no readiness
observation, explicit readiness produces a separate timestamp without changing generation,
and explicit activation/Scope selection permits Memory save, search and exact reads.
Existing lost-response/no-replay, original write target, Principal change/revocation and
Scope pagination assertions passed. The native probe executable SHA-256 was
`10871e418986ebb196910b407f711bc8b5879f033f8e31471e1acc914cdbe7c4`.
The Desktop test helper/catalog working tree was dirty; Server runtime sources match the
recorded clean archive. These are protocol results, not signed-package or installed UI acceptance.

Native tests: 95 passed, including no readiness requests during ordinary anonymous/authenticated
actions, access-mode changes, unavailable/denied identity without fallback, independent
capability denial, failed explicit readiness preserving Scope/Memory, and stale probe cancellation.
Frontend: 150 passed, including English/Chinese explicit action and accessible explanation;
these use jsdom/mocked IPC. Server/client regressions: 114 passed. A real SQLite/Access ASGI
regression additionally rejects all readiness entry points after startup and verifies a user
without `server.observe` can obtain identity and save/read in an independently granted Scope.
Server startup readiness is a separate lifecycle responsibility and is unchanged.

The new public operation does not certify arbitrary remote binaries or authorize resource
operations. Anonymous legacy Servers without its qualified identity contract require an upgrade;
qualified authenticated legacy profiles retain Principal checks. Existing product qualification
records retain their original source, wheel, contract and operation evidence. Installed WebView,
system vault, IME/accessibility/scaling and Windows 11/macOS qualification remain separate.

该记录绑定准确的 Server 源码、wheel 和契约，670 个运行时文件已逐一核对。真实 SQLite Server 的匿名本机、
Bearer、HTTPS 自定义 CA／路径前缀及强制 Provider 四种模式均通过；随后使用普通产品连接管理器和打包资格记录
再次通过。验证覆盖轻量连接身份、显式就绪探测、精确 Scope、记忆读写、原始写入目标、响应丢失不重放及撤权。
原生 95 项、前端 150 项、Server／客户端 114 项测试通过。前端使用模拟 IPC；实际 SQLite／Access 集成另外确认：
无观察权限但有独立 Scope 授权时仍可确认身份并读写，普通请求不会调用模型或 Provider 就绪探测。
Server 启动时的就绪检查归属独立生命周期，未在本次改变。

匿名旧版 Server 缺少已验证的轻量身份接口时需要升级；已验证的已认证旧版配置继续复核 Principal。
这些证据不代替安装后的 WebView、系统凭据库、输入法／无障碍／缩放和 Windows 11／macOS 平台验收。

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

## Deployment context validation

On 2026-10-28, Windows 10 x64 / Python 3.11.15 / Rust 1.95.0 ran the deployment-identity
working tree based on `aedf7beb1ecff2ea1fade6ccdd8e4bf6a298f799`.
All 70 native tests and 117 frontend tests passed, together with Clippy, Rust formatting,
IPC drift, TypeScript, frontend lint and the production build. Nine native HTTP regression tests
cover deployment replacement with an unchanged Principal, late Scope/Memory results, activation
races, Principal changes during reads, qualified legacy transitions and dispatched writes.
Replacement scenarios use synthetic TCP responses; they do not establish real deployment replacement
or installed-platform acceptance. The bilingual unknown-write regression checks the original Server ID,
preserved draft and explicit retry confirmation.

The real SQLite Server harness passed anonymous loopback, static Bearer, HTTPS with an explicit CA
and base path, and an enforced Provider. It exercised native save/search/exact reads, original write
deployment metadata, uncertain writes without replay, Principal changes/revocation and independent
Server survival. Exact inputs:

- Server sources: `aedf7beb1ecff2ea1fade6ccdd8e4bf6a298f799`.
- Wheel: `powercontext-1.4.2.dev36+gaedf7beb1.d20261028-py3-none-any.whl`;
  SHA-256 `fde108a81a872a6228aad54832f2cd8f899a53bb4a7ff5c321871891fdbc5b68`.
- Normalized OpenAPI SHA-256: `c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`.
- Debug `server_probe.exe` SHA-256: `a56d4db19650459c53094cd1a09fddfc40b6daa98f90dcf5f7d0c5514481390a`.

This run used the explicit disposable `desktop-ci-fixture` qualification, not a new production
catalog record. The report records a dirty Desktop working tree; historical qualification records
are unchanged. Native Manager p95 round trips on these small local fixtures were 24.86–53.66 ms for
save, 19.77–37.51 ms for search and 17.64–36.04 ms for exact reads, including identity revalidation.
These are observations, not approved budgets, UI measurements or remote-network guarantees.
Readiness separation, signed Windows 11/macOS packages and installed acceptance remain open.
Repository file hooks passed; default Windows `ty check` reports existing POSIX-only attributes in
unchanged Python files. `ty check --python-platform linux` passed.

## Submission recovery checks

On 2026-10-30, Windows 10 x64 / Rust 1.95.0 checks against baseline `90355b8e` cover
retry discovery failure with the original unknown receipt retained, and concurrent read disconnection
while a dispatched write is paused. Loopback TCP fixtures verify only the original mutation reaches
the Server, and that the pending receipt becomes unknown under its original operation identity.
All 89 native suite cases pass; proxy isolation additionally reruns its case in a spawned process.
All 146 frontend cases, TypeScript, lint, formatting, production build, Clippy and IPC drift checks pass.
Full App tests in English and Chinese verify retained original and edited retry inputs, recovery before
the write finishes, individual discard, late unknown receipts after discard, and conclusive late success
without restoring private results. TCP fixtures exercise the native Manager; jsdom tests mock IPC.
These checks do not establish installed WebView or live external Server acceptance of the new paths.

## Rejected submission recovery and Python CI checks

On 2026-10-31, Windows 10 x64 checks reproduce a paused save returning 403 after a concurrent
read disconnects the context. The native Manager retains the failed receipt and original target
without replay; the application keeps the rejected text available for explicit copy/discard.
English and Chinese App regressions verify copy, cancelled discard and explicit discard of rejected input.
All 90 native suite cases and 148 frontend tests pass, together with TypeScript, lint, production build,
Rust formatting, Clippy and IPC drift. Proxy isolation also reruns in a spawned process.
All 14 deployment-identity cases pass with real loopback sockets and synthetic HTTP responses;
App state-refresh tests use mocked IPC and do not qualify installed WebView behavior.

Python 3.11.15 checks pass 38 cases across the affected MySQL admission/runner, worker logging
and ZCode service-chain modules; 57 runner cases are skipped by their platform/backend guards.
The admission fixture accepts the maintenance connection's missing-database keyword while
retaining its read-only assertion. The real worker test waits for a completion observation
before consuming captured descriptors. ZCode checks use Node 24.22.1 with a real HTTP Server
and SQLite, including a response deliberately delayed past Stop's unchanged 800 ms request
budget: the result remains unknown, another Stop does not replay the mutation, and independent
reads verify committed memory, exact provenance and recall in a new session. A normal response
also exercised the cursor-reached path. These local checks do not establish Linux/Python matrix
success; consult the new CI run for those results.

## Native session observation checks

Windows x64 checks for the native session collector use Rust 1.95.0, Node 24.14.1 and pnpm 11.13.1.
`cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, all 62 native tests and generated IPC
checking pass. The new regressions exercise real loopback HTTP sockets with synthetic responses, including
readiness 503, request versus user-operation counting, millisecond latency, cancellation, unknown writes,
invalid decoded receipts, reset during an in-flight write, bounded retention, safe projection and collector
contention/poisoning. Mock Tauri IPC checks exercise the actual capability configuration and reject untrusted
windows and origins; session commands also work without connection state.

Desktop lint, TypeScript checking, all 111 UI tests and the frontend production build pass. The Desktop OpenAPI
contract checks pass without contract changes. Website lint and link validation pass, including both locale
versions of the [session observation contract](../docs/en/development/desktop-session-observations.md).
`uv lock --locked` and `uv run --locked pytest tests/test_desktop_contract.py -q` pass.

Repository hooks pass except `ty check` on Windows, which reports POSIX-only attributes in unchanged Python files
under `src/powercontext/builtin/code/` and `tests/builtin/test_native_code.py`. The explicit Linux-target check,
`uv run --locked ty check --python-platform linux`, passes. The native tests do not qualify an actual Server,
installed application, clipboard interaction or macOS execution. The This session UI and installed copy flow have their own checks below; native tests alone do not establish platform acceptance.

## This session view and clipboard checks

The This session page is integrated with native summary, reset and preview IPC. Node 24.14.1 and pnpm 11.13.1
pass lint, TypeScript checking, all 147 UI tests and the frontend production build. The focused session/navigation
checks cover both languages, localized Server discovery rows and coverage, loading/empty/failure/unsupported states,
request/action outcomes and milliseconds,
retention/loss coverage, explicit preview and exact text copying, clipboard failure/manual recovery, modal focus,
reset cancellation/failure and stale asynchronous results. The installed harness additions pass Ruff and targeted
Windows/Linux type checks. Contract/fixture regression checks pass without HTTP or native contract changes.

Repository-wide type checks report existing POSIX-only attribute diagnostics on Windows and missing `database`
arguments in three SeekDB publication tests on both Windows and the explicit Linux target. These files match
master commit `176b3f92`; the focused Desktop harness type checks and all other repository hooks pass.

Headless Edge 155.0.4283.62 with synthetic IPC and clipboard exercised 24 combinations of English/Chinese,
light/dark/system and CSS viewports 1180×800, 800×600, 400×400 and 400×300. Checks found no page-level horizontal
overflow, and exercised preview text equality, explicit copying, Escape/focus restoration and clipboard failure in
forced colors. The 400×300 case uses device scale factor 2; this is browser layout evidence, not installed WebView
zoom or screen-reader acceptance. Screenshots and reports are under ignored `.artifacts/desktop-session-view/`.

A local Windows x64 check installed an unsigned debug NSIS bundle with the isolated identifier
`com.powercontext.desktop.sessionchecks`, packaged `http://tauri.localhost/` resources and actual WebView2
155.0.4283.62. It used a preconfigured synthetic connection because profile saving is outside this local check.
Against a separate no-model SQLite Server process, the installed app saved, searched and read an exact Memory,
then exercised both session tables, the native safe preview, and complete clipboard-text equality by pasting into
its Memory editor. Both Chinese and English copy flows passed. Reset cancellation/confirmation, native window
clearing, retained session start/lifetime loss and independent exact Memory readback after reset passed.
An 800×600 native window also displayed the preview without horizontal page overflow.

The installed executable SHA-256 is
`d86cac5f22d8aebd8a400cf1e0e4bd6172ab924416a57eee00fc138e841e0b45`.
The CI-fixture Server source is `2066de01dbd27d08eb01a192b5b22be01c688235`; its built-wheel SHA-256 is
`f87da56c813fe0e994ba60c29e8ba99eb48567dc561ac9f19ac2979e0599c1a6`, with contract digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`. These are local functional checks, not a
normal product-build qualification record, clean-machine installation/signing acceptance, macOS execution, native
200% zoom, IME or assistive-technology qualification. `installed_session.py` is included in the hosted Windows
installed-app harness so the same preview/copy/reset assertions also run against its installed artifact.

## Installer/Ops diagnostic projection checks

Session summary schema 2 includes an independent 64-snapshot `installerOps` projection wired to the native Ops
bridge. Windows x64, Rust 1.95.0, Node 24.14.1 and pnpm 11.13.1 pass all 79 native tests, 150 UI tests, eight focused
Python contract/fixture tests, Clippy with warnings denied, Rust formatting, generated IPC checks, frontend lint,
TypeScript checking and the production build. Website lint and bilingual link validation pass.

Native projection tests preserve the existing operation UUID and all eight Ops outcomes, retain a known phase and
attempt after a lost reply, exclude private target/endpoint/request fields, and keep HTTP/action aggregates empty
for Ops work. They cover the independent capacity/evictions, reset during an invocation, lifetime losses, contention,
poisoning and rejection of noncanonical references. Bilingual UI fixtures exercise the distinct phase/outcome table,
unknown phases, safe codes, empty/unavailable/incomplete coverage and explicit exact-text copying including Ops data.
These are typed native fixtures and mocked UI/clipboard evidence, not actual installer-failure or service-manager
acceptance. A macOS-only bridge regression exercises a real synthetic helper failure and supported status lookup
after session reset; it was not executed on this Windows host.

The installed Windows harness expects schema 2, connected Ops collection and reset/lifetime-loss semantics for both
windows. The existing installed-app/clipboard evidence above retains its exact executable and Server identities;
it does not qualify the Ops diagnostic table or clipboard path with installer failures. Actual installed failure flows
and macOS execution remain platform acceptance work. Repository-wide typing retains the master baseline diagnostics
described above; focused Windows/Linux typing of the installed harness passes.

## Native discovery acceptance

The native `get_server_info` adapter and full real-Server harness were exercised on Windows x64 on 2026-10-23 with Rust 1.95.0 and Python 3.11.15. The Server sources, project configuration and lock matched baseline commit `c535d816`; the wheel was built at Desktop commit `f2b442fc41f724e206a50f2415a03476abdcdf76` with an unrelated dirty documentation tree. The wheel `powercontext-1.4.1.dev138+gf2b442fc4.d20261023-py3-none-any.whl` had SHA-256 `677812c860dd6f12866afa3b6b3e2e0529a6d37b427187c0ce4f3cd732349551`; the normalized contract digest was `5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`. All four isolated SQLite Server configurations passed: anonymous loopback, static Bearer, HTTPS with an explicit CA and reverse-proxy base path, and an enforced authentication Provider. Checks covered discovery, Scope selection, Memory save/search/exact revision reads, Principal changes, same-Principal revocation, uncertain writes without replay, pagination and independent Server survival. The Server environment included the declared `jieba==0.42.1` dependency. Results are recorded under ignored `.artifacts/real-server.json`.

The native suite passed all 61 tests, including synthetic HTTP regressions for missing/denied/unavailable/invalid/incompatible discovery, optional-field additions, operation membership, duplicate deployment-identity fields, qualification isolation, cancellation, credentials and uncertain writes. A synthetic HTTP regression verifies that a qualified connection blocks Scope requests after discovery returns 503 and allows them again after an explicit successful recheck. The bundled-product qualification test also exercised explicit Scope browsing through the normal manager. All 116 frontend tests passed, including distinct bilingual discovery diagnostics, replacement of a temporary-failure diagnostic after a successful recheck, and per-operation Memory/Scope gating with restriction reasons while independently supported operations remain available, including exact-read gating for records returned by a save. Clippy with `--all-targets -- -D warnings`, Rust formatting, generated contract drift, IPC export drift, frontend lint/type checks/build and seven focused Python contract/fixture tests passed. Generation was executed from a Windows checkout whose path contains a space. Repository hooks passed except default Windows `ty check`, which reported POSIX-only attributes in unchanged Python modules; `ty check --python-platform linux` passed. Synthetic HTTP and UI fixtures establish local status/recovery handling, not installed-host or release acceptance.

The historical Server c535d816 protocol qualification below records the specific source, wheel and operation identities independently of the CI-only fixture. It does not establish installed UI or release acceptance. The focused `server_probe <fixture.json> discovery-only` mode calls discovery without readiness or Memory requests and accepts the full probe's fixture format. Historical product records remain unchanged; the locally generated qualification file is a CI-only fixture. This historical discovery qualification retains its original contract digest; the `cc5980f0`, `dd62723d` and `4ffdf4e9` records retain their ten independently qualified operations and do not add discovery qualification. The `cc5980f0` acceptance predates the native discovery adapter and does not establish real-Server acceptance of the combined client and Server sources. The combined statistics/discovery records below retain their own source-specific qualification evidence and original contract digests. Deployment-aware read/write fencing, explicit readiness separation, local setup approval handoff and installed Windows/macOS qualification remain separate acceptance requirements.

## Compatibility scope

`sqlite-identity-stats-7d89a97a-v1` matches the bundled API contract and qualifies
all thirteen operations, including identity, statistics and Runtime observations,
for its exact Server source and wheel above. The historical
`sqlite-observability-stats-4568b499-v1` and `sqlite-observability-4568b499-v1` records
retain their twelve-operation and eleven-operation evidence, respectively.

[compatibility.json](src-tauri/src/connections/compatibility.json) preserves each recorded Server source, normalized OpenAPI contract digest, qualification-wheel digest and operation set. The `sqlite-atomic-112db11e-v1` record qualifies its eleven recorded SQLite protocol operations against the exact Server source and wheel below. Separate `authenticatedClients` evidence qualifies those operations for the current client contract in Bearer mode with a valid Principal; it does not replace the original Server contract or qualify anonymous access. Historical records retain their exact identities and evidence, including the separate `sqlite-atomic-1da99747-linux-v1` qualification. Ordinary builds require explicitly selecting the record whose tested Server source and wheel match the deployment, then checking the connection before business operations become available. Records with a different contract digest remain disabled unless separate authenticated-client evidence covers the current contract and selected authentication mode. Saved unavailable selections remain visible until explicitly replaced. Selecting a profile does not extend its recorded operation set or qualify a different Server source. CI builds have their own artifact identities and do not change product qualification.

Statistics additionally require `get_stats` in the selected record's independently qualified operation set.

The real Server harness covers SQLite with anonymous loopback, static Bearer, HTTPS with explicit CA/base path, and injected-provider/enforced access. It exercises save/search/exact reads, identity changes, binding revocation, ambiguous writes without replay and Scope pagination. Installed UI checks cover repeated launches preserving the existing editor, late committed-save responses preserving a new Scope draft, explicit connection/Scope selection, clipboard copies, connection isolation, draft cancellation, byte limits and independent Server survival after Desktop exit. Historical record `sqlite-atomic-f3a90b2f-v1` remains preserved with its original identities and is disabled for this contract.

Selecting a compatibility profile does not attest the remote binary identity. Existing `sqlite-1.1.1-v1` / `sqlite-63f918b7-v1` / `sqlite-ab43e3a7-v1` / `sqlite-f1089f4e-v1` / `sqlite-6e237568-v1` / `sqlite-legacy-58f7f4f6-v1` selections require explicitly choosing an available matching profile and rechecking the connection. Desktop reads memories only through Atomic Memory references, so Servers that answer only with Memory entry citations are not qualified. CLI diagnostics separately verify the registered executable path and digest, then validate the diagnostic response schema. The observed version is optional report information; a registration does not require a version.

### Current-Scope statistics qualification

#### Server 4568b499 statistics product qualification

`sqlite-observability-stats-4568b499-v1` qualifies all twelve Desktop operations,
including `get_stats` and discovery, against Server source
`4568b4999f2a69350d00dabd4f1414ac9776c737`.
Normalized OpenAPI SHA-256:
`de74d806343998bae877effb732b1606b3eb27ee9c31fbc39509ee3937fa2a4a`.
Wheel: `powercontext-1.4.2.dev238+g4568b4999-py3-none-any.whl`.
Wheel SHA-256: `5c11517fc8966fe34e953adedb370ef270595da1b0c37a2efd941f8b589ea563`.
All 736 packaged runtime files match this Server source byte for byte, and the
combined Desktop checkout has no changes to those runtime sources.

On 2026-11-05, macOS 26.5.2 ARM64, Python 3.11.15 and Rust 1.95.0 passed all four
built-wheel SQLite modes: anonymous loopback, static Bearer loopback, explicit-CA
HTTPS with a base path, and enforced authentication Provider. The default native
probe, built without `ci-fixtures`, ran from clean Desktop commit
`e4d5e873c42cf160d9233a74a3d2d5a9da1524a6`. It selected the checked-in record
through ordinary `ConnectionManager::new` profile checking, activation and
exact-Scope selection. The report confirms `qualificationSource: bundled product records`
and the exact qualification ID. An independent disposable fixture run at clean
Desktop commit `581cf26bfe1125cba34e20cb165901d98de5f5eb` passed the same modes.

Statistics reads cover empty and persisted inventory, UTC today/7d/30d,
zero-filled and partial token reports, Scope isolation and permission revocation.
The exact-Scope adapter accepts the contract's optional `processing` and
`bootstrap_delivery` fields while retaining null estimator handling and selection
validation. A public manager regression reproduced `InvalidResponse` before this
adapter repair and passes after it. Partial usage is explicit Runtime-recorder
fixture input; it is not a real model-provider invocation. The probe also verifies
independent Memory operations, identity changes, lost write responses without
replay, pagination and Server survival after client exit.

```sh
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run python desktop/tests/real_server.py --product-qualification sqlite-observability-stats-4568b499-v1
cargo +1.95.0 test --locked --manifest-path desktop/src-tauri/Cargo.toml --lib --tests
```

The matching-digest product gate failed with only eleven-operation records and
passes with this separate record. All 46 master records and all 42 prior feature
records retain their exact source, contract, wheel and operation identities.
The native suite passed 133 tests with two explicit ignores; all seven statistics
tests and all 183 frontend tests passed. Type checking, Clippy, Rust formatting,
generated IPC checks and all 62 API/Desktop contract tests passed. Local reports
are retained under ignored `desktop/.artifacts/real-server.json`,
`desktop/.artifacts/stats-fixture-581cf26b.json` and `desktop/.artifacts/server-wheel/`.
These results establish the local native/SQLite protocol. Installed UI, native
credentials, Windows execution, external production deployments and release
acceptance require separate evidence. Discovery does not attest remote binary identity.

#### Server 63e9665c statistics product qualification

`sqlite-stats-63e9665c-v1` qualifies all twelve Desktop operations, including
`get_stats` and discovery, against clean Server source
`63e9665c689fc7a37018dacb7f5236951a9a618d` and normalized OpenAPI digest
`d59da9040814d0f2b25d930ecb36b93d932ff8906440606d259d3b9456364d3d`.
Wheel: `powercontext-1.5.1.dev25+g63e9665c6-py3-none-any.whl`.
Wheel SHA-256: `0d4d9c5f52425a12debcedb18ff8596109e69a9fd1ca6025460c84d2b217db51`.
All 686 packaged runtime files in `powercontext/` and
`powercontext_service_bootstrap/` match the clean source byte for byte.
The sorted newline-terminated `wheel/path sha256` manifest hashes to
`2b2557255d64d1c3ef83f9ad374e4128e64aefe71e7746e08f669cdd6ff2b3c3`.

On 2026-11-04, macOS ARM64 with Python 3.11.15 and Rust 1.95.0 passed all four
built-wheel SQLite modes: anonymous loopback, static Bearer loopback, explicit-CA
HTTPS with a base path, and enforced authentication Provider. The Desktop client
ran the integrated working tree based on `daddf658` and this Server commit;
the report records that uncommitted Desktop tree. The Server wheel came from
the separate clean source checkout identified above. The native probe
was built without `ci-fixtures` and used ordinary `ConnectionManager::new`
profile selection, checking, activation and exact-Scope selection. It exercised
statistics for UTC today/7d/30d, persisted inventory, zero and partial token
reports, independent Memory operations, identity/revocation and unknown writes
without replay. Partial usage is seeded Runtime-recorder input, not a live
model-provider call. The harness verifies the selected product-record receipt
and wheel/contract/operation identity.

```sh
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --locked python desktop/tests/real_server.py --product-qualification sqlite-stats-63e9665c-v1
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --test session bundled_product_qualification_covers_every_desktop_operation
```

The matching-digest product gate rejected the eleven-operation qualification
before this statistics record was added, then passed with the new record. The separate
`sqlite-atomic-3cefa45a-v1` record retains its exact eleven-operation evidence;
all historical records retain their original identities and digests. Local
reports are under ignored `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/server63-runtime-manifest.txt`. These checks establish the
local built-wheel/native SQLite path; installed Windows/Intel, signed packages,
external production deployments and full release acceptance require separate
evidence. Discovery alone does not attest a remote binary.

新记录在实际构建的 SQLite Server 上验证十二项操作，使用普通产品资格选择链，
没有注入 CI fixture 资格；历史记录及其来源身份均保留。部分 token 上报为显式
Runtime 测试输入，不代表真实模型调用，也不代替安装包、签名或发布验收。

#### Server 9c8f061c statistics product qualification

`sqlite-stats-9c8f061c-v1` qualifies all twelve bundled Desktop operations,
including `get_stats` and discovery, against Server source
`9c8f061cf76b4002641e8ca8a5d23a469f433e56` and normalized OpenAPI digest
`83dc68194770a41facea55a3fe2279bc0e8b01812a82a1d838334232c67bedc7`.
The qualification wheel is
`powercontext-1.4.1.dev314+g5bb0b7eb5.d20261103-py3-none-any.whl`, SHA-256
`250be633f359e42eee52a07e5b7815aa423ed1346e79374dff1e9f59891dc7d1`.
Its version reflects the Desktop checkout used to build it during integration;
all 684 packaged runtime files in `powercontext/` and
`powercontext_service_bootstrap/` match the recorded Server commit byte for byte.
The sorted newline-terminated `wheel/path sha256` manifest hashes to
`fb0bbf08c3be0993feb0cb7126be377681aedcee59f75ebbdeb6ed380dfa7c0c`.

On 2026-11-03, macOS ARM64, the isolated built-wheel SQLite Server passed
anonymous loopback, static Bearer loopback, explicit-CA HTTPS with a base path,
and enforced authentication Provider modes. The native probe was built without
`ci-fixtures`. In product-qualification mode it uses `ConnectionManager::new`
and selects this checked-in record through the ordinary connection check,
activation and exact-Scope selection path; it does not inject a qualification.
All four modes completed explicit statistics reads through that manager for
UTC today/7d/30d, persisted inventory, zero and partial usage, and independent
Memory operations. Statistics revalidates Principal and deployment identity before and after the read.
Existing identity/revocation, context cancellation and
uncertain-write checks also passed. Partial model usage is seeded into the
Runtime recorder; this is not an actual model-provider invocation.

Reproduce with a wheel whose digest matches the record and a default native build:

```sh
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --locked python desktop/tests/real_server.py --product-qualification sqlite-stats-9c8f061c-v1
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --test session bundled_product_qualification_covers_every_desktop_operation
```

The harness rejects a mismatched wheel/digest or an incomplete operation set.
It also requires the native probe to return the selected product record ID and
product qualification source; a stale fixture-only executable cannot satisfy
product acceptance merely by ignoring the input flag.
The regression reads only checked-in product records, even when CI fixture
features are enabled. It requires one matching-digest record covering every
bundled operation; historical partial qualifications remain unchanged.
The regression failed before the statistics product record was added and passed
afterwards. Ignored local evidence is in
`desktop/.artifacts/stats-9c8f061c/wheel-identity.json`,
`desktop/.artifacts/stats-9c8f061c/product-server.log`, and
`desktop/.artifacts/stats-9c8f061c/real-server.json` (`qualificationSource: bundled product records`).

These checks establish a local built-wheel/native SQLite path. They do not
establish installed Windows/Intel acceptance, signing/notarization, external
production deployment, or release/upgrade qualification. Operators must match
the recorded Server source and wheel; discovery alone does not attest a remote binary.


#### Server 5032075a statistics product qualification

`sqlite-stats-5032075a-v1` qualifies all twelve bundled Desktop operations,
including `get_stats` and discovery, against Server source
`5032075ad31d6cbd3fc333175a578a9b3e64020e` and normalized OpenAPI digest
`0f6befcd87645b83e63ea6393ba57002387302b4e53cf85fc474a218292f4104`.
The qualification wheel is
`powercontext-1.4.1.dev288+g4f0be5efa.d20261102-py3-none-any.whl`, SHA-256
`e15c277dccf95c230b242ba9043ad912cd3c44c59346c2dfc7e4952ddc19d14e`.
Its version reflects the Desktop checkout used to build it during integration;
all 670 packaged runtime files in `powercontext/` and
`powercontext_service_bootstrap/` match the recorded Server commit byte for byte.
The sorted newline-terminated `wheel/path sha256` manifest hashes to
`0f889be03894ee03b264e8d3f11e4bec443a08c727413593736303244565026f`.

On 2026-11-02, macOS ARM64, the isolated built-wheel SQLite Server passed
anonymous loopback, static Bearer loopback, explicit-CA HTTPS with a base path,
and enforced authentication Provider modes. The native probe was built without
`ci-fixtures`. In product-qualification mode it uses `ConnectionManager::new`
and selects this checked-in record through the ordinary connection check,
activation and exact-Scope selection path; it does not inject a qualification.
All four modes completed explicit statistics reads through that manager for
UTC today/7d/30d, persisted inventory, zero and partial usage, and independent
Memory operations. Existing identity/revocation, context cancellation and
uncertain-write checks also passed. Partial model usage is seeded into the
Runtime recorder; this is not an actual model-provider invocation.

Reproduce with a wheel whose digest matches the record and a default native build:

```sh
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --locked python desktop/tests/real_server.py --product-qualification sqlite-stats-5032075a-v1
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --test session bundled_product_qualification_covers_every_desktop_operation
```

The harness rejects a mismatched wheel/digest or an incomplete operation set.
It also requires the native probe to return the selected product record ID and
product qualification source; a stale fixture-only executable cannot satisfy
product acceptance merely by ignoring the input flag.
The regression reads only checked-in product records, even when CI fixture
features are enabled. It requires one matching-digest record covering every
bundled operation; historical partial qualifications remain unchanged.
The regression failed before the statistics product record was added and passed
afterwards. Ignored local evidence is in
`desktop/.artifacts/stats-5032075a/wheel-identity.json`,
`desktop/.artifacts/stats-5032075a/product-server.log`, and
`desktop/.artifacts/stats-5032075a/real-server.json` (`qualificationSource: bundled product records`).

These checks establish a local built-wheel/native SQLite path. They do not
establish installed Windows/Intel acceptance, signing/notarization, external
production deployment, or release/upgrade qualification. Operators must match
the recorded Server source and wheel; discovery alone does not attest a remote binary.


#### Server 0b0a1c71 statistics product qualification

`sqlite-stats-0b0a1c71-v1` qualifies all twelve bundled Desktop operations,
including `get_stats` and discovery, against Server source
`0b0a1c71f56bf00fb9ce1cffb7d7a185cc50752d` and normalized OpenAPI digest
`37936d7897ec2d981dcc195d3b7b7a9f99c572d866c0193b37df43617077c155`.
The qualification wheel is
`powercontext-1.4.1.dev276+g9e7d8d8da.d20261101-py3-none-any.whl`, SHA-256
`a4371b7102ed22008ca0002f6e99c811df0917bcbd8bad0c4138f315dbcb6d26`.
Its version reflects the Desktop checkout used to build it during integration;
all 665 packaged runtime files in `powercontext/` and
`powercontext_service_bootstrap/` match the recorded Server commit byte for byte.
The sorted newline-terminated `wheel/path sha256` manifest hashes to
`5949a03b7c47da3191dec0cbe4c5fa056d93194e95732fee63a3869a529c6e58`.

On 2026-11-01, macOS ARM64, the isolated built-wheel SQLite Server passed
anonymous loopback, static Bearer loopback, explicit-CA HTTPS with a base path,
and enforced authentication Provider modes. The native probe was built without
`ci-fixtures`. In product-qualification mode it uses `ConnectionManager::new`
and selects this checked-in record through the ordinary connection check,
activation and exact-Scope selection path; it does not inject a qualification.
All four modes completed explicit statistics reads through that manager for
UTC today/7d/30d, persisted inventory, zero and partial usage, and independent
Memory operations. Existing identity/revocation, context cancellation and
uncertain-write checks also passed. Partial model usage is seeded into the
Runtime recorder; this is not an actual model-provider invocation.

Reproduce with a wheel whose digest matches the record and a default native build:

```sh
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --locked python desktop/tests/real_server.py --product-qualification sqlite-stats-0b0a1c71-v1
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml --test session bundled_product_qualification_covers_every_desktop_operation
```

The harness rejects a mismatched wheel/digest or an incomplete operation set.
It also requires the native probe to return the selected product record ID and
product qualification source; a stale fixture-only executable cannot satisfy
product acceptance merely by ignoring the input flag.
The regression reads only checked-in product records, even when CI fixture
features are enabled. It requires one matching-digest record covering every
bundled operation; historical partial qualifications remain unchanged.
The regression failed before the statistics product record was added and passed
afterwards. Ignored local evidence is in
`desktop/.artifacts/stats-current/wheel-identity.json`,
`desktop/.artifacts/stats-current/product-server.log`, and
`desktop/.artifacts/real-server.json` (`qualificationSource: bundled product records`).

These checks establish a local built-wheel/native SQLite path. They do not
establish installed Windows/Intel acceptance, signing/notarization, external
production deployment, or release/upgrade qualification. Operators must match
the recorded Server source and wheel; discovery alone does not attest a remote binary.


#### Server 27404931 statistics and discovery qualification

`sqlite-stats-27404931-v1` qualifies Server sources at
`274049318d1ed61885b2f75b202c261e6c60715b`, normalized contract digest
`c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`, and wheel
`powercontext-1.4.1.dev185+g434859f8d.d20261028-py3-none-any.whl`, SHA-256
`e944f46e2a04c0101c47667700667a34b805ca625f3ef8c69e73837784040d3b`.
All 617 packaged runtime files match that Server commit byte for byte. The sorted
`path sha256` manifest, newline terminated, hashes to
`31cf15628d1a45127b2c93a06ce0ea2df34efb195218075b501f22fca4b30853`.
The build included local Desktop merge changes; Server runtime, OpenAPI and
lockfile sources match the recorded commit.

On 2026-10-28, macOS ARM64, Python 3.12.13 and Rust 1.95.0 exercised all twelve
native operations, including `get_server_info` and `get_stats`, against that
wheel in anonymous loopback, Bearer loopback, explicit-CA HTTPS/base path and
enforced-provider modes. All four passed, preserving discovery qualification,
exact-Scope statistics, partial token reports, context isolation, authorization,
exact reads and unknown-write handling. Partial reports are explicit Runtime
recorder fixture inputs, not actual provider calls. Reports and source comparison
are generated under `.artifacts/stats-27404931/` and `.artifacts/real-server.json`.

The App mounts Statistics through `Observation.panels.inventory`; bilingual
App navigation regressions exercise the named tab, exact Scope and explicit load.
The Windows installed journey uses that same tab for its real Server table
comparisons. Local App tests use mocked IPC; installed-platform execution on the
current Desktop head and full upgrade acceptance retain their separate CI gates.

#### Server bad4c0c2 statistics qualification

`sqlite-stats-bad4c0c2-v1` qualifies Server sources at
`bad4c0c23fb6f8febbb91d5da050a6765a5887a2`, normalized contract digest
`c12901b88b8e958c9cd63469298b9a04b9d5e2b0b4564d83b50f7ae95542519a`, and wheel
`powercontext-1.4.1.dev180+g7f6ef3c02.d20261027-py3-none-any.whl`, SHA-256
`0f5e5be815b143a63dc605d93c741b0251717e1db0af2c32fe1a98acad758c7d`.
All 609 packaged runtime files match the recorded Server commit byte for byte.
The sorted `path sha256` manifest, newline terminated, hashes to
`4d0dc40f50bdd5bfb151c1169ad6c41af3466ad777d7ee65a26a39f7852fb083`.
The wheel was built with local Desktop merge changes; Server runtime, OpenAPI
and dependency-lock sources match the recorded commit.

On 2026-10-27, macOS ARM64 with Python 3.12.13 and Rust 1.95.0 ran the native
`ConnectionManager` / `server_probe` against independent SQLite Server processes
from that wheel. Anonymous loopback, Bearer loopback, HTTPS with CA/base path and
enforced-provider modes passed. Exact-Scope statistics cover empty and persisted
inventory, today/7d/30d UTC periods, zero usage, partial token reports and permission
revocation without fallback. The partial reports are explicit inputs to the real
Runtime recorder, not evidence of model-provider calls. The merged harness retains
bounded request-completion diagnostics and Server cleanup; the four runs completed.
Source identity and harness output are generated under `.artifacts/stats-bad4c0c2/`
and `.artifacts/real-server.json`. This is protocol qualification for the recorded
sources and wheel; installed-platform and release acceptance remain separate.

#### Server 31ecdd61 statistics qualification

`sqlite-stats-31ecdd61-v1` qualifies Server sources at
`31ecdd61beadb7ac8de5da20998dead9a9e07845`, normalized contract digest
`3165f114185aa403b563c936d5fbd0829388628f4a37540d9d1519202a07871c`, and wheel
`powercontext-1.4.1.dev157+g0cc9036b0.d20261026-py3-none-any.whl`, SHA-256
`7a17cf9c641d1fa7ae9635204b605aedca8e33f0ceddc0bf8fcd9dd02667cb4b`.
All 552 packaged runtime files match that Server commit byte for byte. The sorted
`path sha256` manifest, newline terminated, hashes to
`9dbada6473027441b6b4934803d9efe718e84a48b4f77723d41b0de414eea125`.
The wheel was built with local Desktop merge changes; its Server runtime,
OpenAPI contract and dependency lock match the recorded sources.

On 2026-10-26, macOS ARM64 with Python 3.12.13 and Rust 1.95.0 ran the native
`ConnectionManager` / `server_probe` against independent SQLite processes from
that wheel. Anonymous loopback, Bearer loopback, HTTPS with CA/base path and
enforced-provider modes passed. The eleven operations include exact-Scope
statistics: empty and persisted inventory, today/7d/30d UTC periods, zero usage,
partial token reports and permission revocation. Partial reports are seeded via
the real Runtime recorder, not observed model-provider calls. Identity changes,
exact reads and ambiguous writes retain the existing harness checks.
Evidence is generated under `.artifacts/stats-31ecdd61/` and `.artifacts/real-server.json`.
This qualifies the recorded protocol and sources; installed UI and release
acceptance require the checks for their corresponding Desktop head.

#### Server 1da99747 statistics qualification (historical)

`sqlite-stats-1da99747-v1` records Server commit
`1da997476651c2850ff7141e2b7cd512d0152a90`, contract digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`, and wheel
`powercontext-1.4.1.dev139+g1da997476.d20261024-py3-none-any.whl` with SHA-256
`4eda168954bb5950aa4f0bbc0d534b2b2bdb15ff43f97de7df279963c46c70a8`.
All 457 Python runtime files in that wheel match the checkout's runtime sources;
their sorted `path sha256` manifest, newline terminated, hashes to
`49fe8325db1955ee250c3a76a8efad3d87fc393dc1007a95817524f38ded41ad`.
The Desktop adapter and harness had local changes during this run; the Server
runtime, OpenAPI contract and dependency lock matched the recorded commit.

On macOS 26.7 ARM64, Python 3.12.13 and Rust 1.95.0, the native `ConnectionManager`
and `ServerApi` passed the isolated built-wheel harness in anonymous loopback,
Bearer loopback, HTTPS with explicit CA/base path, and enforced-provider modes.
The eleven recorded operations include `get_stats`. Statistics checks exercise
the exact Scope, genuine empty inventory, `today`/`7d`/`30d` UTC periods,
persisted Memory inventory, no-model zero usage, and same-Principal permission
revocation without selection fallback. Partial provider usage is explicitly
seeded through the real Runtime recorder: two generation requests, unknown input
tokens and nine output tokens survive persistence, aggregation and native parsing.
This seeded input does not establish that an actual model provider emitted it.

Synthetic native tests separately cover cancellation, changed Scope, independent
Memory cancellation, response bounds, and invalid response selection/period.
The harness reports are generated under `.artifacts/`; reproduce them with
`fixture_qualification.py`, the `server_probe` example and `real_server.py`.
These results qualify the observed Server protocol.

The bundled macOS ARM64 debug app was also exercised against two isolated real
Servers on this host, using an isolated application identifier and an 800 × 600
window. The build used Desktop `34c61c5f` plus the main-window zoom permission
in this PR; its executable SHA-256 is
`43d0d149d3194739171ffb70a57736280c14d2cd3d63fe47001c8f2b3381065d`.
Observed results include explicit activation after restart, empty inventory,
Chinese and English partial-token displays, clearing on Scope/connection changes,
and the second Server's independent one-entry inventory with zero usage.
Command+0 followed by five Command+= steps exercised 200% webview zoom, including
a successful statistics reload. The zoom IPC permission is restricted to the
trusted local main window. This was a bundled debug-app check, not installer,
IME, screen-reader, signing or complete accessibility acceptance.

`installed_statistics.py` extends the Windows installed WebDriver journey with
independent real `/v1/stats` table comparisons, all three UTC periods, bilingual
unknown-token handling, and Scope/connection isolation. Its fixture and Python
checks ran locally; the Windows UI journey requires actual Windows execution.
Hosted workflows at `34c61c5f` required maintainer approval (`action_required`),
so that head had no Windows acceptance result. Intel and release gates remain
separate.

<a id="current-server-protocol-qualification"></a>

### Server protocol qualification records

#### Server 112db11e protocol qualification

`sqlite-atomic-112db11e-v1` qualifies Server source
`112db11e885c38b997da2177e03a4065a7e35dc3` with OpenAPI digest
`83dc68194770a41facea55a3fe2279bc0e8b01812a82a1d838334232c67bedc7`.
The wheel is `powercontext-1.4.2.dev132+g112db11e8-py3-none-any.whl`, SHA-256
`785121dd4d4859735d836ad4122acabf21309edb4c99b62305797456f28586ee`.
All 679 packaged runtime files match that commit byte for byte, without missing or extra files.
The sorted runtime manifest digest is `b74203e2071a6216813da33f26e05baf8b9fdbbb666f1b7d7bbdd15a5cd69707`
(one `wheel/path sha256` line per file, with a final newline). The API is prepared for 1.5.0;
the distribution retains its Git-derived development version until a release tag is created.

On 2026-11-02, Linux x86_64 and Python 3.14.6 ran the unchanged `desktop/tests/real_server.py`
against the extracted wheel. Anonymous loopback, static Bearer, HTTPS with an explicit CA and
base path, and injected-provider/enforced-access modes all passed. The eleven operations cover
discovery, readiness, Scope selection, Atomic Memory saving, full-text search, and exact reads.
The provider case also verified identity invalidation, same-identity revocation, and 51 same-title
Scopes. Every mode verified a committed write with a lost response without replay and Server
survival after the client exited. All four fixture Servers shut down normally without forced kills.

The Rust 1.95.0 native probe was built from the product Cargo lock with `ci-fixtures`, in the
Linux Tauri build container. Its SHA-256 is
`e0d31a5b1e1079efc01bcaf26a037fafe8a44d3f9225bc800007c1e60e953ebe`.
A local executable wrapper dispatched the unmodified probe into that container over the shared
loopback network. Earlier runs exceeded the fixture's startup window on CPUs with substantial
VM steal time. The passing run used `taskset -c 0,1,8,9`; no assertions or timeouts were changed.
The report records Desktop commit `3c8cfaa59dd76868f1f62d8b6e1df3539bde105b` and a dirty working
tree containing README, screenshot, and release-copy changes. Runtime, OpenAPI, and native client
sources still matched the qualified source commit.

This record qualifies the listed SQLite protocol operations. Context preparation, receipts,
plugin operations, model quality, and database upgrades are outside its operation set. Windows/macOS
packaging, signing, and native credential persistence remain separate checks.

本记录绑定上述源码、契约和 wheel，679 个运行时文件逐一核对一致，四种真实 SQLite／HTTP 模式全部通过。
未修改验收断言或超时；通过 CPU 亲和性避开已观测到的虚拟机调度等待。该记录不替代数据库升级、
Windows／macOS 安装包、签名、原生凭据持久化或模型质量验收。

#### Server 3cefa45a protocol qualification

`sqlite-atomic-3cefa45a-v1` qualifies Server sources at
`3cefa45ad08490423391148439c9eb73f1d48f58` with OpenAPI digest
`d59da9040814d0f2b25d930ecb36b93d932ff8906440606d259d3b9456364d3d`.
The wheel is `powercontext-1.4.2.dev174+g3cefa45ad-py3-none-any.whl`, SHA-256
`cead75231f0f6961735dd117f454731d989ba96880a08c988c24aa0bae71c38e`.
All 686 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the recorded source bytes. The sorted runtime manifest digest is
`70dbff69bfbd0011fcab5389b6956ec21f015f3c01f8755ed0c200d2237f31e7`
(one `wheel/path sha256` line per file, with a final newline).

On 2026-11-03, macOS 26.5.2 ARM64 with Python 3.12.13 ran the unchanged
`desktop/tests/real_server.py` against independent SQLite Server processes loaded from this wheel.
Anonymous loopback, static Bearer, HTTPS with an explicit CA/base path, and injected-provider/enforced
access all passed. The eleven operations cover Server discovery, liveness/readiness, Principal,
capabilities, Scope listing/detail/default, Atomic Memory save/search, and exact revision reads.
The provider case verified identity changes, same-identity revocation, and 51 same-title Scopes.
Every mode verified a committed write with a lost response without replay and Server survival after
client exit. All four fixture Servers shut down normally. `desktopWorkingTreeDirty=false`.

The Rust 1.95.0 native probe was built from the product Cargo lock. Its SHA-256 is
`0c156f7a2447449f5ecdcc94d997f3766c9109fe1d5e3da749db25004e1d6c0c`.
Ignored local reports are under `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This record qualifies the listed SQLite
protocol operations. Installed UI, platform packaging, persistent native credentials, model
protection behavior, and database upgrades require their separate checks.

Reproduce from the recorded clean commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --no-sync python desktop/tests/fixture_qualification.py --server-commit 3cefa45ad08490423391148439c9eb73f1d48f58
uv run --no-sync python desktop/tests/real_server.py
```

#### Server 2d782c2a protocol qualification

`sqlite-atomic-2d782c2a-v1` qualifies Server sources at
`2d782c2a1283cf7b987f5aac924841d8dcd841c0` for OpenAPI digest
`c90024304fa0430c91e63904e992b4828cb2b5467f2d625862973544378aafed`.
The wheel is `powercontext-1.4.2.dev150+g2d782c2a1-py3-none-any.whl`, SHA-256
`a47c43bdf457587ee802553660b5de340d081a46bed00cc6c3eeb3b87a0a26db`, built from that clean commit.
All 679 packaged runtime files match its archived sources byte for byte, with no missing or extra runtime files.
The sorted runtime manifest digest is `703eb7c951207367526efb7ef8409fdae86556165fe64a77fc3861bc995f8aa4`
(one `wheel/path sha256` line per file, with a final newline). This development wheel does not establish a published release.

On 2026-11-01, Linux x86_64 and Python 3.14.6 ran the unchanged `desktop/tests/real_server.py` against the
extracted wheel and independent SQLite Servers. All four modes passed: anonymous loopback, static Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The eleven recorded operations
include Server discovery, Atomic Memory save/search/exact revision reads, and Scope selection. The probes
verified identity changes, same-identity revocation, Scope pagination, committed writes with lost responses
without replay, and Server survival after client exit. All four Servers exited normally without forced
termination. The report records `desktopWorkingTreeDirty=false`; all repository timeouts and assertions were unchanged.

The standalone native client used 13 product source/support files from the recorded commit: eleven Rust
sources, including the existing `server_probe.rs` and session observations, plus two JSON files. All 307
resolved dependency versions, sources and checksums match the product Cargo lock. It was compiled with Rust
1.95.0; the probe binary SHA-256 is `83f9c1abe387c99762dfd09b9b48a87863cec4c4b6d09394bed8865112e9e5c4`.
The Tauri GUI shell was excluded because the local WebKit development dependency was unavailable.

Recall manifest, context preparation/receipts, context checking and Source/Artifact plugin operations are
outside this record's qualified operation set. Inference protection was disabled; no active input encoding
was applied. This record qualifies the recorded SQLite protocol behavior; Windows/macOS packaging, native
vault persistence, installed UI, signing, IME and screen-reader behavior require separate platform checks.

本记录绑定上述精确源码、契约和 wheel；679 个运行时文件的完整集合及字节逐一核对一致，四种真实 SQLite／HTTP 模式均通过。
全部 Server 正常退出，未强杀；未修改仓库的超时或断言。
11 项已验证操作包括 Server 发现，不包括 Recall manifest、上下文准备与回执、context/check 或 Source／Artifact 插件操作。
推理保护未启用，也未使用主动输入编码；该记录不替代 Windows／macOS 安装包、GUI 或原生凭据库验收。

#### Server e4fbead8 protocol qualification

`sqlite-atomic-e4fbead8-v1` qualifies Server sources at
`e4fbead875626a55f850933f4ef59a48cc10de48` for OpenAPI digest
`f6ef63adfc6eed12410e0559b9c2cbbefc9ab7cb5ec1d2dd4cf807e6b9ac618c`.
The wheel is `powercontext-1.4.2.dev117+ge4fbead87-py3-none-any.whl`, SHA-256
`e19a4a250e5d1b60ae714439f60a9abf79135408b5a8c4fcc9a5747fa2fc20b0`, built from that clean commit.
All 670 packaged runtime files match its archived sources byte for byte, with no missing or extra runtime files.
The sorted runtime manifest digest is `4758ca788c8ef24c158738694ab21c6aeb408c231edef6e8fd30882019e032f3`
(one `wheel/path sha256` line per file, with a final newline). This development wheel does not establish a published release.

On 2026-11-01, Linux x86_64 and Python 3.14.6 ran the unchanged `desktop/tests/real_server.py` against the
extracted wheel and independent SQLite Servers. All four modes passed: anonymous loopback, static Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The eleven recorded operations
include Server discovery, Atomic Memory save/search/exact revision reads, and Scope selection. The probes
verified identity changes, same-identity revocation, Scope pagination, committed writes with lost responses
without replay, and Server survival after client exit. All four Servers exited normally without forced
termination. The report records `desktopWorkingTreeDirty=false`.

An earlier attempt reached the unchanged startup-readiness timeout during a high-load period, before any
business assertion ran. Its Server exited normally without forced termination. The successful retry used
the same clean source, wheel and native probe, with all repository timeouts and assertions unchanged.

The standalone native client used 13 product source/support files from the recorded commit: eleven Rust
sources, including the existing `server_probe.rs` and session observations, plus two JSON files. All 307
resolved dependency versions, sources and checksums match the product Cargo lock. It was compiled with Rust
1.95.0; the probe binary SHA-256 is `c1259f75e377dbd93ab7dd1b73ca05e88f62ec8f8e9658c037e0478046285aac`.
The Tauri GUI shell was excluded because the local WebKit development dependency was unavailable.

Recall manifest, context checking and Source/Artifact plugin operations are outside this record's qualified
operation set. Inference protection was disabled; no active input encoding was applied. This record qualifies
the recorded SQLite protocol behavior; Windows/macOS packaging, native vault persistence, installed UI,
signing, IME and screen-reader behavior require separate platform checks.

本记录绑定上述精确源码、契约和 wheel；670 个运行时文件的完整集合及字节逐一核对一致，四种真实 SQLite／HTTP 模式均通过。
首次尝试在高负载期间达到原有启动超时；相同源码、wheel 和原生探针重试通过，未放宽超时或断言。
11 项已验证操作包括 Server 发现，不包括 Recall manifest、context/check 或 Source／Artifact 插件操作。
推理保护未启用，也未使用主动输入编码；该记录不替代 Windows／macOS 安装包、GUI 或原生凭据库验收。

#### Server bbd7b1d8 protocol qualification

`sqlite-atomic-bbd7b1d8-v1` qualifies Server sources at
`bbd7b1d8469411d23777d9c60cb0484f9f0d54b8` for OpenAPI digest
`78120757a326c02230194f5b546b33ea6b7e438544a2d94aa03a1fe4b6626cf7`.
The wheel is `powercontext-1.4.2.dev102+gbbd7b1d84-py3-none-any.whl`, SHA-256
`07a9fffc49736a5a4d093248e8bb2bb99753e88b48076f05979d63a0137180d0`, built from that clean commit.
All 654 packaged runtime files match its sources byte for byte. The sorted runtime manifest digest is
`d44bb964bdc53b7a2d509baea103a21a7d10b8d62efb81514cf2a4c60f62ce7d`
(one `wheel/path sha256` line per file, with a final newline). This development wheel does not establish a published release.

On 2026-10-31, Linux x86_64 ran the unchanged `desktop/tests/real_server.py` against the extracted wheel and
independent SQLite Servers. All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit
CA/base path, and injected-provider/enforced access. The eleven recorded operations include Server discovery,
Atomic Memory save/search/exact revision reads, and Scope selection. The probes verified identity changes,
same-identity revocation, Scope pagination, committed writes with lost responses without replay, and Server
survival after client exit. All four Servers exited normally without forced termination. The report records
`desktopWorkingTreeDirty=false`. Recall manifest, context checking and Source plugin operations are outside
this record’s qualified operation set. Inference protection was disabled; no active input encoding was applied.

The standalone native client used 13 unchanged product source/support files: eleven Rust sources, including
the existing `server_probe.rs` and session observations, plus two JSON files. All 307 resolved dependency
versions and checksums match the product Cargo lock. It was compiled with Rust 1.95.0; the probe binary
SHA-256 is `330929a954666108489a1595108f188de539c344a932c1f6695c34c3029b45fa`. The Tauri GUI shell was excluded because
the local WebKit development dependency was unavailable. This record qualifies the recorded SQLite protocol
behavior; Windows/macOS packaging, native vault persistence, installed UI, signing, IME and screen-reader
behavior require separate platform checks.

本记录绑定上述精确源码、契约和 wheel；654 个运行时文件逐一核对一致，四种真实 SQLite／HTTP 模式均通过。
11 项已验证操作包括 Server 发现，不包括 Recall manifest、context/check 或 Source 插件操作。
推理保护未启用，也未使用主动输入编码；该记录不替代 Windows／macOS 安装包、GUI 或原生凭据库验收。

#### Server d3850bf6 protocol qualification

`sqlite-atomic-d3850bf6-v1` qualifies Server sources at
`d3850bf646993db5aa11cabc83d53b1e82571e6c` for OpenAPI digest
`d057f59a140211376772979f68f777295c88cf1c445bed605d85ce7b0e0c03bb`.
The wheel is `powercontext-1.4.2.dev90+gd3850bf64-py3-none-any.whl`, SHA-256
`e6d606616347271946e30cd9980c248ee5d6b5c92fa91e78f06fa2bf92e7d273`, built from that clean commit.
All 629 packaged runtime files match its sources byte for byte. The sorted runtime manifest digest is
`8362001e52b7f57d934c30d6044f41cd00a5941de8fa24fc34af79e6db208668`
(one `wheel/path sha256` line per file, with a final newline). This development wheel does not establish a published release.

On 2026-10-30, Linux x86_64 ran `desktop/tests/real_server.py` against the extracted wheel and independent
SQLite Servers. All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit CA/base path,
and injected-provider/enforced access. The 11 recorded operations include Server discovery, Atomic Memory
save/search/exact revision reads, and Scope selection. The probes also verified identity changes and
revocation, Scope pagination, committed writes with lost responses without replay, and Server survival
after client exit. All four Servers exited normally without forced termination. Recall manifest and
context checking are not in this record's qualified operation set. Inference protection was disabled;
no active input encoding was applied.

The first attempt exceeded the harness's default startup readiness budget under high host load. An isolated
runner increased only the startup retries from 100 to 600 at 200 ms intervals (a nominal 20-to-120-second
polling-delay budget). Server sources, native request deadlines and business assertions were unchanged;
the repository harness retains its original timeout. The successful report records
`desktopWorkingTreeDirty=false` for the clean source checkout.

The standalone native client used 13 unchanged product source/support files: 11 Rust sources, including
the existing `server_probe.rs` and session observations, plus two JSON files. All 307 resolved dependency
versions and checksums match the
product Cargo lock. The probe binary SHA-256 is
`aca028110ca48a2c67dd045d22a3407befe344617af39b9131a106ec2419e805`.
After adding this record, all eight unchanged product native session tests passed in the standalone
harness without CI fixture qualification, including the bundled-qualification Scope-browsing gate.
The Tauri GUI shell was excluded because the local WebKit development dependency was unavailable.
This record qualifies the recorded SQLite protocol behavior; Windows/macOS packaging, native vaults,
installed UI, signing, IME and screen-reader behavior require separate platform checks.

本记录绑定上述精确源码、契约及 wheel，四种真实 SQLite／HTTP 模式均通过。11 项已验证操作包括
Server 发现，不包括 Recall manifest 或 context/check。高负载下首次启动等待超时，独立验收脚本仅将
启动轮询从 100 次放宽为 600 次，未改 Server、客户端请求期限或业务断言。推理保护未启用，也未使用
主动输入编码；该记录不替代 Windows／macOS 安装包、GUI 或原生凭据库验收。

#### Server 0d86e94b protocol qualification

`sqlite-atomic-0d86e94b-v1` qualifies Server sources at
`0d86e94b6803b1eeaaf91231b717d716ad9de134` for OpenAPI digest
`7442678e3a9b33600c1cb64d531cc09972a7e3996dd740ed96f969c6649f842c`.
The wheel is `powercontext-0.1.dev1148+g0d86e94b6-py3-none-any.whl`, SHA-256
`a295f16eaa66c911a1ee65b5d4e0937f7a1fc9533a41e1fe678d2f4fb0ab80e8`, built from that clean commit.
All 620 packaged runtime files match its sources byte for byte. The sorted runtime manifest digest is
`a2ebf5262a70b5cb877bd76c531a7cbb71cbe571bcd381e0bd98dca60d797712`
(one `wheel/path sha256` line per file, with a final newline). This development wheel does not establish a published release.

On 2026-10-27, Linux x86_64 with Python 3.14.6 ran the existing `desktop/tests/real_server.py`
against the extracted wheel and independent SQLite Servers. All four modes passed: anonymous loopback,
static Bearer, HTTPS with an explicit CA/base path, and injected-provider/enforced access.
The ten recorded operations cover Atomic Memory save/search/exact revision reads, Scope selection and
pagination, identity changes and revocation, committed writes with lost responses without replay, and
Server survival after client exit. The report records `desktopWorkingTreeDirty=false`.

The native client compiled the unmodified product `connections`, `credentials`, `error`, and `transport`
modules and existing `server_probe.rs` in a standalone library. All 240 dependency versions and checksums
match the product Cargo lock. The Tauri GUI shell was excluded because the local GLib development
libraries are below the required version. These results qualify the recorded SQLite protocol behavior;
Windows/macOS packaging, native vaults, installed UI, signing, IME, and screen-reader behavior require
separate platform checks. Protection and encoding retain their disabled/off defaults for this qualification.

Reproduce from the recorded clean checkout with an empty wheel output directory and the native build dependencies:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --no-sync python desktop/tests/fixture_qualification.py --server-commit 0d86e94b6803b1eeaaf91231b717d716ad9de134
uv run --no-sync python desktop/tests/real_server.py
```

本记录绑定上述精确源码、契约及 wheel；四种真实 SQLite／HTTP 连接场景均已通过。
保护和编码使用默认关闭配置。该记录不替代 Windows／macOS 安装包、GUI 或原生凭据库验收。

#### Server f3a90b2f protocol qualification

`sqlite-atomic-f3a90b2f-v1` records Server sources at
`f3a90b2fbc9edf9ab503e532fdc1c29418746a7b` for OpenAPI digest
`9c35d3d80035213a52598a62cd748fd5febb3a9afde69a5908b0dee40aaf73c2`.
The wheel is `powercontext-1.4.2.dev140+gf3a90b2fb-py3-none-any.whl`, SHA-256
`76f142ccbe406411c60b5ba91429350ea8ebbd1f449275a82b6cfd5213981f9e`, built from that clean commit.
All 672 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the recorded source bytes. The sorted runtime manifest digest is
`6c8570ecaf816db49d1d5b45ecbabbcdfaa552e38199103bcbdce8181e907f08`
(one `wheel/path sha256` line per file, with a final newline). Generated distribution version
metadata is excluded. OpenAPI, project configuration and the lock file also match that commit.

On 2026-11-01, macOS 26.5.2 ARM64 with Python 3.12.13 and Rust 1.95.0 ran
`desktop/tests/real_server.py` against independent SQLite Server processes loaded from this wheel.
All four modes passed: anonymous loopback, static Bearer, HTTPS with explicit CA/base path,
and an injected provider with enforced access. The eleven recorded operations cover discovery,
health, Principal, Scope selection and pagination, Atomic Memory save/search/exact revision reads,
identity changes, same-identity revocation, committed writes with lost responses without replay,
and Server survival after client exit. The native probe uses the committed product modules and
locked dependencies; its SHA-256 is
`e94b4df948cc5f1a4230a03c9f9cb4a422c9a0d5bdc2ddff7d964a0e8f828459`.
The harness records `desktopWorkingTreeDirty=false`.

Reproduce from the recorded commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --frozen python desktop/tests/fixture_qualification.py --server-commit f3a90b2fbc9edf9ab503e532fdc1c29418746a7b
uv run --frozen python desktop/tests/real_server.py
```

Ignored local evidence is in `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This qualifies the listed SQLite protocol
behavior on that host. It does not establish Windows/Intel execution, installed GUI workflows,
persistent native credentials, signing, notarization or complete release-upgrade acceptance.
All historical records retain their original source, contract, artifact and operation identities.

本记录绑定上述准确源码、契约和 wheel；672 个运行时文件均与记录提交逐字节一致。
四种真实 SQLite／HTTP 连接模式均通过，覆盖所列十一项协议操作、身份变化、同身份撤权、
Scope 分页及响应丢失后不重放已提交写入。该验证不替代 Windows／Intel、安装后 GUI、
原生凭据持久化、签名、公证或完整升级验收。全部历史记录保留原有身份、操作集合和证据。

#### Server 581be595 protocol qualification

`sqlite-atomic-581be595-v1` records Server sources at
`581be595a51b06fdbed208349fc8c2066c80b1b3` for OpenAPI digest
`4aeefdd602a11ebece4c74f120b0cc8671aa3cd62386822326198ca7a4b88ca5`.
The wheel is `powercontext-1.4.2.dev112+g581be595a-py3-none-any.whl`, SHA-256
`ded0d06803205e6b34a93cf9c33e76897f65b5880e2530e5501006a5d05ce6c8`, built from that clean commit.
All 667 packaged runtime files in `powercontext/` and `powercontext_service_bootstrap/`
match the recorded source bytes. The sorted runtime manifest digest is
`10a7e17f59654d053d8c9c8ac568d6f3be9f54707384d27fa18aca72c5d233fb`
(one `wheel/path sha256` line per file, with a final newline). Distribution version metadata
is outside this runtime manifest. OpenAPI, project configuration and the lock file also match
that commit.

On 2026-11-01, macOS 26.5.2 ARM64 with Python 3.12.13 and Rust 1.95.0 ran the existing
`desktop/tests/real_server.py` harness against independent SQLite Server processes loaded from
this wheel. All four modes passed: anonymous loopback, static Bearer, HTTPS with an explicit
CA/base path, and an injected provider with enforced access. The eleven recorded operations
cover Server discovery, liveness/readiness, access principal/capabilities, Scope listing,
detail and default, Atomic Memory save/search and exact Artifact revision reads. The native
connection manager also passed identity changes, same-identity revocation, Scope pagination,
committed writes with lost responses without replay, and Server survival after client exit.

The native `server_probe` compiled the unchanged product modules and locked dependencies;
its executable SHA-256 is
`a4dfd54788c843a37c1ee4919513d92da5925519a2264ca9be273a0fd0540ca7`.
All native build source, Cargo manifest and lock bytes match the recorded commit. The completed
harness report records `desktopWorkingTreeDirty=false`.

Reproduce from the recorded commit with an empty wheel output directory:

```sh
uv build --wheel --out-dir desktop/.artifacts/server-wheel
cargo +1.95.0 build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe
uv run --frozen python desktop/tests/fixture_qualification.py --server-commit 581be595a51b06fdbed208349fc8c2066c80b1b3
uv run --frozen python desktop/tests/real_server.py
```

Ignored local evidence is in `desktop/.artifacts/real-server.json` and
`desktop/.artifacts/qualification-wheel-identity.json`. This qualifies the listed SQLite
protocol behavior on that host. It does not establish native Windows/Intel execution,
installed GUI workflows, persistent native credentials, signing, notarization or full
release-upgrade acceptance. All twenty-four historical records retain their exact identities
and evidence.

本记录绑定上述准确源码、契约和 wheel；667 个运行时文件均与记录提交逐字节一致。
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

#### Server bae5d714 protocol qualification

`sqlite-atomic-bae5d714-v1` qualifies Server source
`bae5d714d577c10fa71d7bf43d40d4f8a61d628b` for OpenAPI digest
`0f6befcd87645b83e63ea6393ba57002387302b4e53cf85fc474a218292f4104`.
The wheel `powercontext-1.3.1.dev389+gbae5d714d-py3-none-any.whl` has SHA-256
`6ed5b4dd83bac3ba8b2ca9a9d89dcd4bc016a796f2cf13d0c45b8f103e1c680d`.

On 2026-11-01, macOS 15.7.4 ARM64, Python 3.11.15 and Rust 1.97.1 ran
`desktop/tests/real_server.py` against that wheel in independent SQLite Server
processes. Anonymous loopback, static Bearer, HTTPS with an explicit CA and
base path, and injected-provider/enforced access all passed. The eleven
recorded operations cover Server identity, health/readiness, principal and
capabilities,
Scope listing/detail/default, Atomic Memory save/search and exact Artifact
revision reads. The native probe also verified Scope pagination, identity
changes, same-identity revocation, committed writes with lost responses
without replay, and independent Server survival after the client exited.
The report is retained in ignored `desktop/.artifacts/real-server.json`.

This qualifies the listed SQLite protocol operations for the recorded source
and wheel. It does not establish Windows or macOS installed-application
qualification, native credential persistence, signing, notarization or
release acceptance.

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

#### Server 1da99747 Linux protocol qualification

`sqlite-atomic-1da99747-linux-v1` records a separate qualification wheel for the same Server
contract as `sqlite-atomic-1da99747-v1`. It identifies Server sources at
`1da997476651c2850ff7141e2b7cd512d0152a90` and normalized OpenAPI digest
`5878999ce9e239d6642e4da858074b281ed465629432c578b3584eec47cbce6c`.
The qualification wheel is `powercontext-1.4.1.dev139+g1da997476-py3-none-any.whl`,
SHA-256 `92c71e27f406df8663b6cac894ecef9fe8aff17454a5a0dda736b09624c305d9`.
All 540 packaged runtime files match the recorded Server commit byte for byte; the OpenAPI
contract, project configuration and lock also match. The sorted runtime source-manifest digest
is `da5b32b18298795299135c585c618d8238683eb596b2121f4904f61da23c3786`
(one `wheel/path sha256` entry per line, with a final newline).

The existing real-Server harness passed against this isolated wheel and SQLite in all four modes:
anonymous loopback, static Bearer, HTTPS with an explicit CA and base path, and an injected
provider with enforced access. The ten recorded operations cover Scope selection and pagination,
Atomic Memory save/search/exact revision reads, identity changes, same-identity revocation,
committed writes with lost responses without replay, and independent Server survival after the
client exits.

Qualification ran on Linux x86_64 with Python 3.12.3 and Rust 1.95.0. The standalone Rust client
compiled the unmodified product connection, transport, credential and error modules, plus
`server_probe.rs`, from Desktop merge commit `5bab8fc3720087e537a1a5cd1da613fd044780b7`.
Every client dependency version and checksum matches the product Cargo lock. The Tauri GUI shell
was excluded. This establishes Server protocol compatibility for the recorded sources and wheel;
it does not qualify Linux Desktop support, native macOS/Windows packaging, credential stores,
or installed UI workflows. Historical records retain their original identities and evidence.

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

On 2026-11-01, Linux x86_64, Python 3.12.12 and Rust 1.95.0 passed all four built-wheel
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

## Managed observability protocol qualification

`sqlite-observability-9f279e4e-v1` qualifies the eleven declared Desktop protocol operations
against Server runtime sources at `9f279e4ee8b83894ac86dea49e9c1daeffc1c563`.

OpenAPI SHA-256: `44194cfe8ab1516f87831d6f1290c74b2bb9fae84dfee42705e86adbc3cf98cd`.
Wheel: `powercontext-1.4.2.dev123+g9f279e4ee-py3-none-any.whl`.
Wheel SHA-256: `f29f48354a64ffaacc1c55bb7e462c0530dd08860c6e9b949504af20f3bcab71`.

On 2026-11-02, macOS 26.5.2 arm64, Python 3.14.7 and Rust 1.95.0 passed all four
built-wheel SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The native
`server_probe` was built from the current product sources and generated contract.
The probe verified server identity, save/search/exact reads, Scope selection, lost write
responses without replay, identity changes, revocation, and Server survival after client exit.

The packaged runtime files match the recorded source bytes. Local evidence is retained under
ignored `desktop/.artifacts/real-server.json` and `desktop/.artifacts/server-wheel/`.
This record covers local SQLite protocol behavior; installed UI, native credentials,
Windows, and external production systems require their own validation.

## Managed observability with inference privacy

`sqlite-observability-a9f6fd36-v1` qualifies the eleven declared Desktop protocol operations against
Server runtime sources at `a9f6fd36a572ea33612138bf5aab99a5e032827c`.

OpenAPI SHA-256: `ad6c1e7690899b3c8677ffcdfbb06e7af3122d96b85f2a63e25d89b1abb1b201`.
Wheel: `powercontext-1.4.2.dev134+ga9f6fd36a-py3-none-any.whl`.
Wheel SHA-256: `53e8b83243ebf8517bc69f875a3d7bf4c3d2e94816c46b13d1294f97e3409b83`.

On 2026-11-02, macOS 26.5.2 arm64, Python 3.14.7 and Rust 1.95.0 passed all four
built-wheel SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The native
`server_probe` was built from the matching product sources and generated contract.
The probe verified identity, save/search/exact reads, Scope selection, lost write responses
without replay, revocation, and Server survival after client exit.

All 716 packaged runtime files match the recorded source bytes. Evidence is retained
under ignored `desktop/.artifacts/real-server.json` and `desktop/.artifacts/server-wheel/`.
The separate privacy/Provider transport regression verifies admission and response-release
behavior with a controlled HTTP transport; it is not external Provider or backend evidence.
This record covers local SQLite protocol behavior. Installed UI, native credentials, Windows,
and external production systems require their own validation.

## Server da44b918 protocol qualification

`sqlite-observability-da44b918-v1` qualifies the eleven declared Desktop protocol operations
against Server runtime sources at `da44b91805e91be667beef5ac4f6c2d15de9de93`.

OpenAPI SHA-256: `4d8334f58b97ff18f7130d8efc28ac4cec8f7c5a1cfb6f297b6654bf1b06c6d3`.
Wheel: `powercontext-1.4.2.dev154+gda44b9180-py3-none-any.whl`.
Wheel SHA-256: `f5e58f0119f441b5081da2ff0d38d842511fa9a3e8fb2b3369d3014e5de9a850`.

On 2026-11-03, macOS 26.5.2 arm64, Python 3.14.7 and Rust 1.95.0 passed all four
built-wheel SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer,
HTTPS with an explicit CA/base path, and injected-provider/enforced access. The native
`server_probe` was built from the matching product sources and generated contract.
It verified save/search/exact reads, Scope selection, lost write responses without replay,
identity changes, revocation, and Server survival after client exit.

All 719 packaged runtime files match the recorded source bytes. Evidence is retained under
ignored `desktop/.artifacts/real-server.json` and `desktop/.artifacts/server-wheel/`.
This record covers local SQLite protocol behavior. Installed UI, native credentials, Windows,
and external production systems require their own validation. Historical records retain
their source, contract and wheel identities.

## Server 4568b499 protocol qualification

`sqlite-observability-4568b499-v1` qualifies the eleven declared Desktop protocol operations
against Server runtime sources at `4568b4999f2a69350d00dabd4f1414ac9776c737`.

OpenAPI SHA-256: `de74d806343998bae877effb732b1606b3eb27ee9c31fbc39509ee3937fa2a4a`.
Wheel: `powercontext-1.4.2.dev238+g4568b4999-py3-none-any.whl`.
Wheel SHA-256: `5c11517fc8966fe34e953adedb370ef270595da1b0c37a2efd941f8b589ea563`.

On 2026-11-04, macOS arm64, Python 3.11.15 and Rust 1.95.0 passed all four built-wheel
SQLite modes in `desktop/tests/real_server.py`: anonymous loopback, Bearer, HTTPS with an
explicit CA/base path, and injected-provider/enforced access. The native `server_probe`
was built from the matching product sources and generated contract. The harness supplied
the exact wheel and contract through its disposable fixture profile and exercised the native
ConnectionManager, including save/search/exact reads, Scope selection, lost write responses
without replay, identity changes, revocation, and Server survival after client exit.

All 736 packaged runtime files match the recorded Git source bytes. Evidence is retained
under ignored `desktop/.artifacts/real-server.json` and `desktop/.artifacts/server-wheel/`.
This record covers local SQLite protocol behavior. It does not establish installed UI,
native credential persistence, Windows execution, external production acceptance or release readiness.

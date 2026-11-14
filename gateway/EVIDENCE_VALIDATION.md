# Gateway evidence validation / Gateway 证据验收

- Date / 日期: 2026-11-07.
- Implementation commit / 实现提交: `2b3a1c5a48999ccae6a78499e61ba6d9a78bfdb5`.
- Merged master / 已合并 master: `b8b189091e7a412300cf1292531208a8ead0e325`, including #2513 capacity limits and context refresh. / 包含 #2513 容量限制与上下文刷新。
- Platform / 平台: Darwin arm64, Rust `1.95.0 (59807616e 2026-04-14)`, Python 3.13.15.
- Scope / 范围: [#2463](https://github.com/oceanbase/powercontext/issues/2463),
  [planning discussion / 规划讨论 #2433](https://github.com/oceanbase/powercontext/discussions/2433).
- G3 dependency / 依赖: [#2462](https://github.com/oceanbase/powercontext/issues/2462) was completed by
  [#2538](https://github.com/oceanbase/powercontext/pull/2538), merged as `3585149a678dd3dae7470b2653394137a951d9b6`.
  The production HTTP/1 connection owner now stops pending execution on request disconnect, total deadline and
  bounded shutdown. `CallRecorder` copies the confirmed stopped terminal and model-send state from the shared
  lifecycle before exporting a dropped call; a handed-off interrupted attempt remains `possibly_sent` and usage
  remains unknown. See the lifecycle integration revalidation below.
  G3 依赖由 #2538 完成，合并提交为 `3585149a678dd3dae7470b2653394137a951d9b6`。生产 HTTP/1 连接所有者现会在
  请求断连、总期限和有界 shutdown 时停止 pending execution。`CallRecorder` 在导出被销毁的调用前，从共享生命周期复制
  已确认的 stopped 终态与发送状态；已交接后被中断的尝试仍记为 `possibly_sent`，usage 保持 unknown。详见下方生命周期复核。

Related PR snapshots reviewed during the original implementation / 初始实现期间复核的相关 PR 源码快照:

| PR | Head | Relevant ownership / 相关职责 |
| --- | --- | --- |
| [#2470](https://github.com/oceanbase/powercontext/pull/2470) | `35ec28045b56c2e26cc5558e0edd642d0ddc45e3` | Native protocol behavior and ingress / 原生协议与入口 |
| [#2457](https://github.com/oceanbase/powercontext/pull/2457) | `a676623b8675468500181eb770cbc73a02d44415` | Protection modes and lifecycle facts / 保护模式与生命周期事实 |
| [#2435](https://github.com/oceanbase/powercontext/pull/2435) | `ce0e256f5a8367526b31ce749c5a5a20b1c2c2af` | Prefix capture and projection receipts / 前缀捕获与投影 receipt |

These changes do not incorporate the unmerged PRs or reinterpret Source projection receipts as model usage.
本实现不包含这些未合并 PR，也不将 Source 投影 receipt 当作模型 usage。

## Commands and results / 命令与结果

Cargo was invoked through `/Users/lioky/.cargo/bin/cargo` because the shell's mise cargo shim had no selected version.
下列 Cargo 命令使用上述实际可执行文件运行，避免未选择版本的 mise shim。

| Command | Result / 结果 |
| --- | --- |
| `cargo fmt --manifest-path gateway/Cargo.toml --all --check` | Passed / 通过 |
| `cargo clippy --locked --manifest-path gateway/Cargo.toml --all-targets -- -D warnings` | Passed / 通过 |
| `cargo test --locked --manifest-path gateway/Cargo.toml --workspace` | 185 exercised behavior tests passed; 4 live-Core cases returned early / 185 个实际行为测试通过，4 个真实 Core 用例提前返回 |
| `cargo test --locked --manifest-path gateway/Cargo.toml -p pc-gateway-transport --test usage_timing` | 5 passed, including actual 100 ms body-read timeouts versus premature EOF for JSON/SSE, with headers-only or partial-body observations and no retry / 5 个通过，含真实 100 ms 正文读取超时与提前 EOF 的 JSON/SSE 区分、仅响应头/部分正文事实及不重试 |
| `cargo test --locked --manifest-path gateway/Cargo.toml -p pc-gateway-core --test executor_semantics` | 74 passed; actual reqwest body timeouts through normal/degraded executor paths export readable timeout attempts, unknown usage and no release or retry / 74 个通过；真实 reqwest 正文超时经正常/降级 executor 输出可读超时尝试、未知 usage，不释放、不重试 |
| `make check` | Passed, including lock/policy checks, hooks, types and 61 integration-manifest tests / 通过，包含锁文件/策略检查、hooks、类型及 61 个集成清单测试 |
| `uv run prek run -a` | Passed with all implementation files staged / 实现文件暂存后通过 |
| `make docs-test` | Passed: bilingual links and static build, 1097 public pages verified / 双语链接与静态构建通过，验证 1097 个公开页面 |
| `git diff --check` | Passed / 通过 |

## Lifecycle integration revalidation / 生命周期集成复核

- Date / 日期: 2026-11-14.
- Base / 基线: `3585149a678dd3dae7470b2653394137a951d9b6` (#2538 merged into master).
- Platform / 平台: Darwin arm64, Rust `1.95.0`.
- `mise exec rust@1.95.0 -- cargo test --locked --workspace -- --skip real_core_capture_commit_loss_recovers_same_record_and_prepare_is_not_replayed`
  passed: 247 tests; the single skipped test requires `PC_TEST_PYTHON` and a running real Python Core.
  通过 247 个测试；唯一跳过项需要设置 `PC_TEST_PYTHON` 并运行真实 Python Core。
- The unfiltered workspace command passed the Gateway app, HTTP/1 connection-owner, evidence-process, evidence-output,
  transport, protocol and executor suites, but the real-Core recovery test could not start because `PC_TEST_PYTHON`
  was unset. It is not real-Core acceptance evidence.
  未过滤的 workspace 命令中 Gateway app、HTTP/1 connection-owner、evidence-process、evidence-output、transport、protocol
  和 executor suites 均通过；真实 Core 恢复测试因未设置 `PC_TEST_PYTHON` 而无法启动，因此不算真实 Core 验收证据。
- The Python lifecycle E2E file was present, but could not start in this checkout: the available installed Python
  environment imports a different workspace package and lacks `powercontext.builtin.authz`. The E2E result reported
  in #2538 remains that PR's validation, not a rerun here.
  Python lifecycle E2E 文件存在，但本 checkout 无法启动：当前可用 Python 环境导入了另一工作区的 package，缺少
  `powercontext.builtin.authz`。#2538 中报告的 E2E 结果属于该 PR 的验证，本轮未复跑。

The #2538 lifecycle regression tests cover real HTTP/1 disconnect, total deadline and shutdown ownership. Executor
evidence tests verify each confirmed stop maps to a readable `stopped` call terminal, preserves `possibly_sent`,
keeps usage unknown and emits no release. The production evidence-process fixture separately verifies real Gateway
JSONL output with a real local HTTP provider/Core fixture. Together with #2506's usage, queue-loss, privacy and
transport fixtures, all local Gateway-owned #2463 acceptance criteria are covered; real hosted-provider, third-party
agent adoption and external telemetry-backend ingestion/readback were not executed.

#2538 的生命周期回归覆盖真实 HTTP/1 断连、总期限与 shutdown owner。Executor evidence 测试验证每种已确认停止原因都会映射为可读
`stopped` 调用终态、保留 `possibly_sent`、usage 保持 unknown 且不会释放响应。生产 evidence-process fixture 另以真实 Gateway
子进程和本地 HTTP Core/provider fixture 核对 JSONL 输出。结合 #2506 的 usage、队列丢失、隐私及 transport fixture，#2463
所有由本地 Gateway 控制的验收项均已覆盖；未执行真实托管 provider、第三方 agent 采用行为及外部遥测后端采集/读回。

The workspace runner prints 189 passing test cases. Four `live_context_check` cases return early when the real Core
at `127.0.0.1:8000` is unavailable; readiness was unavailable here. Those four are not real-Core acceptance evidence.
workspace 输出共 189 个通过用例；其中 4 个 `live_context_check` 因真实 Core 不可达而提前返回，不能算作真实 Core 验收。

## Observed behavior / 实际覆盖

| Acceptance / 验收项 | Evidence / 证据 |
| --- | --- |
| Correlated call terminals and actual attempts / 关联终态与真实尝试 | Executor tests cover success, zero-send block/withhold/expiry, retry/NotSent, Indeterminate, upstream HTTP error, release refusal/expiry, degraded execution and pending-future destruction / 覆盖成功、零发送拒绝/过期、重试/NotSent、不确定、上游 HTTP 错误、释放拒绝/过期、降级和 pending future 销毁 |
| Usage through real transport / 真实 transport usage | Local HTTP JSON/SSE fixtures verify Chat/Responses input, output, cached reads, known zero and missing/malformed usage; cache writes stay unknown / 核对两种协议的输入、输出、缓存读、已知零及缺失/畸形 usage，缓存写保持未知 |
| Capacity/evidence integration / 容量与证据集成 | Real fixed-length/chunked overflow returns 400 with no prefix or resend; JSONL links one `buffer_limit_exceeded` attempt to a `withheld`/`possibly_sent` call with unknown usage and observed status/first byte. Normal/degraded executor variants agree with lifecycle snapshots / 真实定长/chunked 超限返回 400，不释放前缀、不重发；JSONL 将一次超限尝试与 `withheld`/`possibly_sent` 调用关联，usage 未知且保留状态码/首字节，正常/降级 executor 与生命周期快照一致 |
| Body timeout classification / 正文超时分类 | Real local HTTP fixtures send headers with or without a partial body, then stall: timeout stays `timeout_awaiting_response`; premature EOF stays `lost_after_handoff`. Readable JSONL preserves observed status/first byte, `possibly_sent`, unknown completion/usage, one attempt and no release/retry in normal/degraded execution / 真实本地 HTTP fixture 发送头部（可含部分正文）后停住，超时保留 timeout 分类，提前 EOF 保留连接丢失分类；正常/降级执行的 JSONL 保留状态/首字节、可能送达、未知完整响应/usage、单次尝试且不释放/重试 |
| SSE evidence framing / SSE 证据 framing | Real transport reads 80/7/20 input/output/cache-read from lowercase and mixed-case media types, including first-event Chat/Responses usage behind one leading UTF-8 BOM. Protocol/body tests preserve original offsets through BOM and reject stripping a second/interior BOM / 真实 transport 在小写/混合大小写媒体类型及带一个开头 UTF-8 BOM 的 Chat/Responses 首事件读出 80/7/20 input/output/cache-read；协议/body 测试保留原始偏移，不移除第二个/流中间的 BOM |
| Bounded retention and visible loss / 有界保留与可见丢失 | A held writer with capacity 4 receives 100000 extra submissions: exactly 4 queue, 99996 drop; 1 record remains in flight, and recovered output reports loss / 容量 4 且 writer 卡住时额外提交 10 万条，4 条排队、99996 条丢弃、1 条在写，恢复输出含丢失计数 |
| Total stream loss / 整条输出流丢失 | Real Gateway with closed stdout reports nonzero write failures and zero writes on stderr; unread stdout reports nonzero drops, then another summary after 30 seconds without new requests or recovery. 511 requests return HTTP 200 with original usage; diagnostics exclude synthetic content/credentials and OS error text / 真实 Gateway 关闭 stdout 时 stderr 报告非零写失败与零写出；不读取 stdout 时报告非零丢弃，停止请求且不恢复后 30 秒再次汇总。511 次请求返回 200 并保留 usage，诊断不含合成正文/凭据或 OS 异常文本 |
| Diagnostic isolation / 诊断隔离 | A controlled blocked diagnostic writer cannot retain the sink or delay 10000 submissions and sink destruction / 受控诊断 writer 卡住时，不保留 sink、不延迟 10000 次提交及 sink 销毁 |
| Business isolation / 业务隔离 | Blocking/full and I/O-error writers preserve release/withhold decisions and pending-send cancellation/drop facts; errors are counted without their text / 慢写/队列满/I/O 错误不改变释放与 withhold 结论及发送中取消/销毁事实，错误仅计数不输出原文 |
| Timing and strict order / 计时与 strict 顺序 | Header-wait fixture is included in first-byte timing; incomplete responses retain observed status/first byte; release approval precedes framework handoff, first body frame and first model-content observation; model TTFT is null / 首字节包含响应头等待，部分失败保留已知状态与首字节，批准先于框架交接、首 frame 与首次模型内容观测，模型 TTFT 为 null |
| Privacy / 隐私 | Synthetic prompt/response/model/request-ID/credential/provider-error/I/O-error markers are absent from evidence; process test also checks stderr / 合成 prompt、响应、模型名、请求 ID、凭据、provider 异常及 I/O 异常标记未出现在证据中，进程测试另检查 stderr |
| Shared lifecycle / 共享生命周期 | Snapshot and evidence use the same terminal/send enums and one ManagedOutcome-to-terminal mapping; normal/refusal/error/degraded/drop scenarios agree / 快照与证据共享终态/发送枚举和唯一 ManagedOutcome 终态映射，正常/拒绝/错误/降级/销毁场景一致 |
| Operational vocabulary / 运维词汇 | Serialized call/attempt/delivery records verify operation, unit, common success/failure/blocked/null outcome and duration projection while preserving nested domain facts; abandoned intent/deadline facts never invent cancelled / 序列化输出验证 operation、unit、通用 outcome 与耗时投影，保留嵌套领域事实，不将 abandoned 的意图/期限推断为 cancelled |
| Startup privacy / 启动隐私 | Spawned TCP requests embed synthetic Core URL credentials/path and invalid injection text; UDS plus rejected steering startup embeds socket/file paths. Markers are absent from stdout/stderr while diagnostics remain readable / 真实 TCP 进程植入 Core URL 凭据/路径及非法 injection 文本，UDS/steering 启动拒绝植入 socket/文件路径；stdout/stderr 不含标记且保留可读诊断 |
| Real production composition / 真实生产装配 | Spawned binary handles 1000 requests with 6000 readable records and 1000 distinct call IDs; explicit Noop handles 100 requests with empty stdout and an evidence-disabled startup diagnostic / 真实二进制处理 1000 次请求，输出 6000 条可读记录、1000 个不同调用 ID；显式 Noop 处理 100 次请求，stdout 为空且启动诊断说明证据关闭 |
| Both ingress formats / 两种入口格式 | In-process public-router tests send 200 calls through actual reqwest to local HTTP fixtures, covering Chat JSON/SSE and Responses JSON/replayed SSE, and read 1200 serialized production-sink records / 进程内公开路由用真实 reqwest 完成 200 次本地 HTTP 调用，覆盖 Chat JSON/SSE 与 Responses JSON/重放 SSE，读取生产 sink 的 1200 条序列化输出 |

Body-read timeout regression tests failed before the fix: the real transport and exported attempt both reported
`lost_after_handoff`. After classification is preserved before discarding the reqwest error, the same fixtures pass.
Transport fixtures use an actual 100 ms timeout; executor/export fixtures use 250 ms and a fake Gate with the real
reqwest transport and production JSONL sink. These are local HTTP and executor acceptance, not a spawned-Gateway
short-timeout configuration, a real Core or hosted-provider claim. Raw reqwest errors are not retained or exported;
no public enum values, schema fields, timeout configuration or retry behavior changed.
正文超时回归在修复前失败：真实 transport 与导出的尝试均误记 `lost_after_handoff`；
在丢弃 reqwest 错误前保留分类后，同样 fixture 通过。transport 使用真实 100 ms 超时；
executor/export 使用 250 ms、FakeGate、真实 reqwest transport 与生产 JSONL sink。
这些验证本地 HTTP 与执行器行为，不证明 Gateway 子进程的短超时配置、真实 Core 或真实供应商能力。
不保留或导出原始 reqwest 错误；未修改公共枚举值、schema 字段、超时配置或重试行为。

The combined implementation preserves startup-confirmed input intersection, rendered-body limits (including
refreshed context), the response-buffer ceiling and separate fallback capacity from #2513. Capacity and evidence
process tests run against the same compiled production binary. The compatibility-ceiling test sends oversized
Content-Length headers and reads the early 413 before upload, avoiding a client BrokenPipe race while preserving
the pre-send rejection assertion. No HTTP OpenAPI change is introduced by this reconciliation.
合并后的实现保留 #2513 的启动容量交集、最终渲染限制（含刷新上下文）、响应缓冲上限与独立 fallback 容量。
容量与证据进程测试使用同一个生产二进制。兼容性上限测试发送超限 Content-Length 请求头后直接读取提前返回的
413，避开客户端上传 BrokenPipe 竞态，同时保留发送前拒绝的断言。本次协调未修改 HTTP OpenAPI 契约。

The production host reads aggregate sink stats on an independent thread every second, emits one warning/error
per first loss category and periodic 30-second summaries to stderr. These reports do not need a successful stdout
record. The reporter releases its sink reference before logging and has no record queue. Both worker startups must
succeed to enable production evidence. Shutdown does not join blocked writers or promise a final summary; logging
filters and loss of stderr remain operational limits. JSONL schema, Python allowlists and dependency versions are unchanged.
生产宿主在独立线程每秒读取聚合计数，首次各类丢失仅通知一次，之后每 30 秒通过 stderr 汇总，
不依赖 stdout 成功写出。reporter 在日志 I/O 前释放 sink 引用且没有记录队列。两个线程均成功启动后才启用生产证据。
关闭时不 join 阻塞 writer，也不保证最终汇总；日志过滤和 stderr 丢失仍是运维边界。
未修改 JSONL schema、Python 白名单或依赖版本。

The spawned-process workload alternates JSON and mixed-case `Text/Event-Stream; charset=utf-8` responses.
Of 1000 enabled calls, 500 SSE responses contain a leading BOM with the sole content delta and usage in the first event;
all 1000 call records preserve 101/13/61 input/output/cache-read and have exactly one first-model-content observation.
The same two response formats are exercised with evidence disabled; SSE headers and complete original bytes are preserved.
真实子进程交替处理 JSON 与混合大小写媒体类型的 SSE；启用证据的 1000 次调用中，500 次 SSE 带开头 BOM，
唯一内容 delta 与 usage 位于首事件。1000 条调用记录均保留 101/13/61 input/output/cache-read，
且每次恰有一条首模型内容观测。关闭证据时也覆盖两种格式，SSE 响应头及完整原始字节保持不变。

Protocol fixtures cover text, refusals and function output, metadata/control decoys, escaped/Unicode content,
LF/CRLF multiline SSE, malformed/empty output and 256 KiB content. Body-observer tests split each sample at every
byte position, verify unchanged outgoing bytes, defer first-content observation until the matching frame, and
verify that control-only or dropped-before-content output does not invent content delivery.
协议 fixture 覆盖文本、拒绝与函数输出、元数据/控制事件干扰、转义/Unicode 内容、LF/CRLF 多行 SSE、
畸形/空输出及 256 KiB 内容。body 观察器测试在样本每个字节位置切分，验证输出字节不变、
首次内容观测等待对应 frame，以及只有控制事件或读到内容之前丢弃时不伪造内容交付。

## Evidence limits / 证据边界

Local Core/provider HTTP fixtures and fake-Gate executor tests are distinct from a real PowerContext Core,
hosted model provider, external telemetry backend, or native agent host. Those external environments were not
executed. The spawned-process test establishes the Rust Gateway's production assembly and local HTTP behavior,
not a third-party agent's cancellation, socket-write visibility or task adoption. No paid model calls were made.
`first_model_content` observes the framework polling a frame containing the first encoded byte of supported
model output; it is separate from `first_body_frame` and does not prove socket writing or client receipt.
Unsupported/empty output and bodies dropped before content have no content event; missing records and sink loss
must remain unavailable evidence. The actual total-deadline, cancellation and shutdown owner are implemented by
[#2538](https://github.com/oceanbase/powercontext/pull/2538); this report verifies the evidence mapping for those
confirmed stops. Common operational fields
align with #2388 vocabulary, but its current Server OTLP pipeline does not ingest this stdout stream or preserve
nested evidence/loss automatically; external backend ingestion/storage/readback was not executed.

本地 Core/provider HTTP fixture、FakeGate 执行器测试不等同于真实 PowerContext Core、真实模型供应商、外部观测后端
或原生 agent 宿主；这些外部环境未执行。真实子进程测试证明 Rust Gateway 生产装配及本地 HTTP 行为，
不证明第三方 agent 的取消能力、实际 socket 写出或任务采用，没有调用付费模型。
`first_model_content` 记录框架轮询到包含受支持模型输出首个编码字节的 frame，与 `first_body_frame` 分开，
不证明 socket 写出或客户端收到。不支持/空输出及内容之前丢弃的 body 不产生内容事件；记录缺失与 sink 丢失
均不能当作耗时为零。#2462 的实际总期限、取消与 shutdown owner 已由 #2538 实现；本报告验证这些已确认停止原因对应的
evidence 映射。
通用运维字段已与 #2388 词汇对齐，但其当前 Server OTLP pipeline 不会自动采集此 stdout 流，
也不会自动保全嵌套 evidence/loss；未执行外部后端采集、存储与读回。

No OpenAPI contract, Python operational fields or database records changed. The Gateway lockfile adds only the
workspace dependency `pc-gateway-evidence` → `pc-gateway-types`; no external dependency versions changed.
The Gateway's client-visible HTTP 503 `gate_paused.detail` is normalized for Core-unavailable and malformed-response
errors; the bilingual deployment contract documents these fixed values and the client migration behavior.
OpenAPI generation/contract regeneration and Python logging formatter tests are therefore not applicable.
The bilingual [output contract](../docs/en/docs/operate/gateway-evidence.md) /
[输出契约](../docs/zh/docs/operate/gateway-evidence.md) defines retention, unknown usage, interruption facts,
framework boundaries and deployment/Rust migration behavior. No deployment or GitHub issue closure was performed.

未修改 OpenAPI 契约、Python operational 字段或数据库记录，无需重新生成 OpenAPI 或验证 Python formatter。
Gateway 锁文件仅新增 evidence → types 的 workspace 依赖，未变更外部依赖版本。
Gateway 客户端可见的 HTTP 503 `gate_paused.detail` 对 Core 不可用及响应畸形错误进行了归一化，
双语部署契约记录了固定值与客户端迁移行为。
双语契约定义保留、未知 usage、中断事实、框架边界及部署/Rust 迁移行为；未执行部署或关闭 GitHub Issue。

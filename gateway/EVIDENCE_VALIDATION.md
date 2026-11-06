# Gateway evidence validation / Gateway 证据验收

- Date / 日期: 2026-11-06.
- Implementation commit / 实现提交: `2a686028262f9ac10f1d9f3c95ad7c4e923aba38`.
- Master base / master 基线: `69b2d7e2ac1aacc05f386d04d4616aa9254b3f5d`; latest master rechecked at `24e0017e0504f2980300ed87b21412bb425b1744` (no additional Gateway changes).
- Platform / 平台: Darwin arm64, Rust `1.95.0 (59807616e 2026-04-14)`, Python 3.13.15.
- Scope / 范围: [#2463](https://github.com/oceanbase/powercontext/issues/2463),
  [planning discussion / 规划讨论 #2433](https://github.com/oceanbase/powercontext/discussions/2433).
- G3-0 dependency / 依赖: [#2499](https://github.com/oceanbase/powercontext/pull/2499) is included in the base.
  Pending execution destruction records abandonment plus cancellation/deadline observations; the owner still decides
  when to stop the future. Cancellation intent and deadline passage alone do not interrupt execution.
  pending 执行销毁时记录 abandoned 与取消/期限事实；仍由所有者决定何时停止 future，信号与期限本身不中断执行。

Related open PR snapshots checked / 已核查相关开放 PR 源码快照:

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
| `cargo test --locked --manifest-path gateway/Cargo.toml --workspace` | 158 exercised behavior tests passed; 4 live-Core cases returned early / 158 个实际行为测试通过，4 个真实 Core 用例提前返回 |
| `cargo test --locked --manifest-path gateway/Cargo.toml -p pc-gateway-app` | Real spawned Gateway, 1000 enabled + 100 disabled requests passed / 真实 Gateway 子进程，启用 1000 次与关闭 100 次请求通过 |
| `make check` | Passed, including lock/policy checks, hooks, types and 61 integration-manifest tests / 通过，包含锁文件/策略检查、hooks、类型及 61 个集成清单测试 |
| `uv run prek run -a` | Passed with all implementation files staged / 实现文件暂存后通过 |
| `make docs-test` | Passed: bilingual links and static build, 1084 public pages verified / 双语链接与静态构建通过，验证 1084 个公开页面 |
| `git diff --check` | Passed / 通过 |

The workspace runner prints 162 passing test cases. Four `live_context_check` cases return early when the real Core
at `127.0.0.1:8000` is unavailable; readiness was unavailable here. Those four are not real-Core acceptance evidence.
workspace 输出共 162 个通过用例；其中 4 个 `live_context_check` 因真实 Core 不可达而提前返回，不能算作真实 Core 验收。

## Observed behavior / 实际覆盖

| Acceptance / 验收项 | Evidence / 证据 |
| --- | --- |
| Correlated call terminals and actual attempts / 关联终态与真实尝试 | Executor tests cover success, zero-send block/withhold/expiry, retry/NotSent, Indeterminate, upstream HTTP error, release refusal/expiry, degraded execution and pending-future destruction / 覆盖成功、零发送拒绝/过期、重试/NotSent、不确定、上游 HTTP 错误、释放拒绝/过期、降级和 pending future 销毁 |
| Usage through real transport / 真实 transport usage | Local HTTP JSON/SSE fixtures verify Chat/Responses input, output, cached reads, known zero and missing/malformed usage; cache writes stay unknown / 核对两种协议的输入、输出、缓存读、已知零及缺失/畸形 usage，缓存写保持未知 |
| Bounded retention and visible loss / 有界保留与可见丢失 | A held writer with capacity 4 receives 100000 extra submissions: exactly 4 queue, 99996 drop; 1 record remains in flight, and recovered output reports loss / 容量 4 且 writer 卡住时额外提交 10 万条，4 条排队、99996 条丢弃、1 条在写，恢复输出含丢失计数 |
| Business isolation / 业务隔离 | Blocking/full and I/O-error writers preserve release/withhold decisions and pending-send cancellation/drop facts; errors are counted without their text / 慢写/队列满/I/O 错误不改变释放与 withhold 结论及发送中取消/销毁事实，错误仅计数不输出原文 |
| Timing and strict order / 计时与 strict 顺序 | Header-wait fixture is included in first-byte timing; incomplete responses retain observed status/first byte; release approval precedes framework handoff, first body frame and first model-content observation; model TTFT is null / 首字节包含响应头等待，部分失败保留已知状态与首字节，批准先于框架交接、首 frame 与首次模型内容观测，模型 TTFT 为 null |
| Privacy / 隐私 | Synthetic prompt/response/model/request-ID/credential/provider-error/I/O-error markers are absent from evidence; process test also checks stderr / 合成 prompt、响应、模型名、请求 ID、凭据、provider 异常及 I/O 异常标记未出现在证据中，进程测试另检查 stderr |
| Real production composition / 真实生产装配 | Spawned binary handles 1000 requests with 6000 readable records and 1000 distinct call IDs; explicit Noop handles 100 requests with empty stdout and an evidence-disabled startup diagnostic / 真实二进制处理 1000 次请求，输出 6000 条可读记录、1000 个不同调用 ID；显式 Noop 处理 100 次请求，stdout 为空且启动诊断说明证据关闭 |
| Both ingress formats / 两种入口格式 | In-process public-router tests send 200 calls through actual reqwest to local HTTP fixtures, covering Chat JSON/SSE and Responses JSON/replayed SSE, and read 1200 serialized production-sink records / 进程内公开路由用真实 reqwest 完成 200 次本地 HTTP 调用，覆盖 Chat JSON/SSE 与 Responses JSON/重放 SSE，读取生产 sink 的 1200 条序列化输出 |

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
must remain unavailable evidence. Closing #2463 still requires integration acceptance of actual total-deadline and
cancellation interruption owned by [#2462](https://github.com/oceanbase/powercontext/issues/2462). The shared control
and future-destruction observations tested here do not establish automatic interruption.

本地 Core/provider HTTP fixture、FakeGate 执行器测试不等同于真实 PowerContext Core、真实模型供应商、外部观测后端
或原生 agent 宿主；这些外部环境未执行。真实子进程测试证明 Rust Gateway 生产装配及本地 HTTP 行为，
不证明第三方 agent 的取消能力、实际 socket 写出或任务采用，没有调用付费模型。
`first_model_content` 记录框架轮询到包含受支持模型输出首个编码字节的 frame，与 `first_body_frame` 分开，
不证明 socket 写出或客户端收到。不支持/空输出及内容之前丢弃的 body 不产生内容事件；记录缺失与 sink 丢失
均不能当作耗时为零。关闭 #2463 仍需验收 #2462 所拥有的实际总期限与取消中断集成；
本报告验证的共享控制与 future 销毁观测不能证明自动中断。

No OpenAPI contract, Python operational fields, database records or dependency lockfiles changed.
The Gateway's client-visible HTTP 503 `gate_paused.detail` is normalized for Core-unavailable and malformed-response
errors; the bilingual deployment contract documents these fixed values and the client migration behavior.
OpenAPI generation/contract regeneration and Python logging formatter tests are therefore not applicable.
The bilingual [output contract](../docs/en/docs/operate/gateway-evidence.md) /
[输出契约](../docs/zh/docs/operate/gateway-evidence.md) defines retention, unknown usage, interruption facts,
framework boundaries and deployment/Rust migration behavior. No deployment or GitHub issue closure was performed.

未修改 OpenAPI 契约、Python operational 字段、数据库记录或依赖锁文件，无需重新生成 OpenAPI 或验证 Python formatter。
Gateway 客户端可见的 HTTP 503 `gate_paused.detail` 对 Core 不可用及响应畸形错误进行了归一化，
双语部署契约记录了固定值与客户端迁移行为。
双语契约定义保留、未知 usage、中断事实、框架边界及部署/Rust 迁移行为；未执行部署或关闭 GitHub Issue。

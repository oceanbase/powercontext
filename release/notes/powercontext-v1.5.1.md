# PowerContext v1.5.1

## English

PowerContext 1.5.1 improves the evidence behind automatic memories, adds subscription model providers and managed monitoring, and strengthens Gateway and installation workflows.

### Memory and Source evidence

- Declare complete, partial or unknown coverage for supported Hermes and Gateway capture projections, with bounded reason codes. These declarations do not establish completeness of the original conversation, authority, consent or extraction eligibility, and historical captures are not backfilled. ([#2451](https://github.com/oceanbase/powercontext/pull/2451))
- Retain versioned Source evidence declarations and exact Atomic Memory revision snapshots. Registration changes and projection rebuilds preserve historical evidence; missing or unverifiable declarations remain neutral, without trust-based ranking or extra write authority. Source citations also retain sequence and line-range locators. ([#2004](https://github.com/oceanbase/powercontext/pull/2004), [#2296](https://github.com/oceanbase/powercontext/pull/2296))
- Preserve supported ISO time qualifiers from selected evidence during automatic extraction and reconciliation. Reject exact redundant revisions with bounded recovery, retaining genuinely new facts. Existing undated memories are not backfilled, and semantic equivalence remains model-dependent. ([#2359](https://github.com/oceanbase/powercontext/pull/2359), [#2358](https://github.com/oceanbase/powercontext/pull/2358))
- Apply the existing related-memory threshold to earlier candidates in the same Source window when using built-in prompts. OceanBase uses native `minimum_should_match` filtering when supported and falls back on older backends. Vector matching can add embedding work; fewer reconciliation calls do not establish lower total cost. ([#2489](https://github.com/oceanbase/powercontext/pull/2489), [#2498](https://github.com/oceanbase/powercontext/pull/2498))

### Subscription providers and Agent integrations

- Use `openai-codex:<model>` with an existing file-backed Codex CLI login, or `github-copilot:<model>` with an existing OAuth token and a Chat Completions model, for Memory, Experience and Profile generation. Topic Memory, Dream and built-in inference protection are unsupported for these providers. Codex CLI remains responsible for renewal; Server and workers reload credentials without consuming refresh tokens or modifying the login file. ([#2432](https://github.com/oceanbase/powercontext/pull/2432))
- Add experimental MiniMax setup/configuration/doctor and target Bub 0.6.1. Codex and OpenCode can disable prompt-time recall independently of capture. Share packaged Skills and Python helpers, preserve explicit Memory kinds, improve Scope error reporting, and remove duplicate adapter trust notices while preserving Server policy and historical-context boundaries. ([#2205](https://github.com/oceanbase/powercontext/pull/2205), [#2492](https://github.com/oceanbase/powercontext/pull/2492), [#2453](https://github.com/oceanbase/powercontext/pull/2453), [#2455](https://github.com/oceanbase/powercontext/pull/2455), [#2380](https://github.com/oceanbase/powercontext/pull/2380), [#2318](https://github.com/oceanbase/powercontext/pull/2318), [#2446](https://github.com/oceanbase/powercontext/pull/2446))
- Add DSH npm release packaging and Desktop v4 message compatibility. Registry installation requires successful `powercontext-dsh` publication and the documented first-publisher setup. The separate DSH configuration UI and cross-agent handoff RFC is a design proposal. ([#2501](https://github.com/oceanbase/powercontext/pull/2501), [#2528](https://github.com/oceanbase/powercontext/pull/2528))

### Gateway alpha and Server reliability

- Require a valid Gateway key on non-loopback listeners, authenticate model requests before consuming their bodies, and retain separate Core authentication for forwarded requests. Preserve native protocol behavior and align final rendered-request and buffered-response limits with Core inspection capacity. ([#2523](https://github.com/oceanbase/powercontext/pull/2523), [#2470](https://github.com/oceanbase/powercontext/pull/2470), [#2513](https://github.com/oceanbase/powercontext/pull/2513))
- Reuse prepared code evidence with per-file revalidation. Gateway code injection remains opt-in and may refresh stale evidence once before sending to the provider. Export bounded, body-free JSONL call records that distinguish attempts, release decisions, delivery observations and unknown usage. Gateway remains alpha and buffers complete responses for inspection; delivery observations do not establish client receipt. ([#2425](https://github.com/oceanbase/powercontext/pull/2425), [#2499](https://github.com/oceanbase/powercontext/pull/2499), [#2506](https://github.com/oceanbase/powercontext/pull/2506))
- Authenticate protected Server requests before body reading, reject blank Scope identifiers on Dream/tag/state routes, drain failed SQLite connection workers, and support ownership migration when enabling access enforcement. Add bounded Topic Memory failure diagnostics. ([#2512](https://github.com/oceanbase/powercontext/pull/2512), [#2494](https://github.com/oceanbase/powercontext/pull/2494), [#2502](https://github.com/oceanbase/powercontext/pull/2502), [#2468](https://github.com/oceanbase/powercontext/pull/2468), [#2304](https://github.com/oceanbase/powercontext/pull/2304), [#2396](https://github.com/oceanbase/powercontext/pull/2396))

### Monitoring, Desktop and installation

- Add an opt-in Grafana/Prometheus/Loki/Tempo/OpenTelemetry Collector stack, with optional Phoenix, and `powercontext observability up|status|check|down|connect`. Service, Scope, processing and Provider diagnostics correlate content-free observations; Gateway stdout evidence needs separate collection. ([#2388](https://github.com/oceanbase/powercontext/pull/2388))
- Guide Desktop users from Overview through an existing Server connection and exact Scope selection to the Memory workspace. Add inventory/usage, session diagnostics and Runtime observations. Readiness is an explicit diagnostic operation, separate from identity validation and authorized Memory work. Candidate assembly and publication tooling do not by themselves qualify signed installers or local provisioning. ([#2490](https://github.com/oceanbase/powercontext/pull/2490), [#2220](https://github.com/oceanbase/powercontext/pull/2220), [#2344](https://github.com/oceanbase/powercontext/pull/2344), [#2474](https://github.com/oceanbase/powercontext/pull/2474), [#2361](https://github.com/oceanbase/powercontext/pull/2361), [#2475](https://github.com/oceanbase/powercontext/pull/2475), [#2477](https://github.com/oceanbase/powercontext/pull/2477))
- Add release-aware Bash and PowerShell installers with prerequisite provisioning, exact/latest stable version selection, independent download mirrors and matching Agent refs. Improve website Markdown exports, SEO, build memory use and package metadata validation. ([#1892](https://github.com/oceanbase/powercontext/pull/1892), [#2454](https://github.com/oceanbase/powercontext/pull/2454), [#2536](https://github.com/oceanbase/powercontext/pull/2536), [#2535](https://github.com/oceanbase/powercontext/pull/2535))

### Installation and compatibility

```bash
uv tool install --force "powercontext[cli,server]==1.5.1"
```

Use `powercontext-v1.5.1` for Agent integration refs. Upgrade Server, SDK, CLI and integrations together, and reopen Agent sessions.

- Source evidence and locator storage require registered migrations through `v1_5_0_004`. Immutable schema revision names retain their original `1.5.0` numbering; an application version bump does not rename them.
- The policy remains `upgrade-required`. Normal publication requires all twelve installed-1.3.0 upgrade configurations across SQLite, seekDB and OceanBase against the exact target artifact. The separate 1.4.0 bridge and intermediate releases are not automatically certified by that matrix.
- Upgrades from 1.3.0 and 1.4.0 require passing full Server acceptance on SQLite, seekDB and OceanBase. These migration paths are not yet verified; do not upgrade existing databases.
- SDK callers injecting MiniMax embedding or Jev reranking clients must use `httpx2.AsyncClient`. Custom Atomic Memory prompts should follow the current extraction/reconciliation contracts.
- Windows installation remains experimental. Desktop signing, local setup integration and clean-machine acceptance are separate requirements. Official MiniMax CLI 0.6.9/0.7.2 plugin-only invocation remains unqualified in the documented affected environment; consult the integration guide for qualified configurations.

## 中文

PowerContext 1.5.1 完善自动记忆的证据保留，新增订阅模型接入与托管监控，并加强 Gateway 和安装流程。

### 记忆与 Source 证据

- Hermes 与 Gateway 为受支持的采集投影声明完整、部分或未知的覆盖状态，并提供有界原因码。这些声明不代表原始对话完整、内容权威、用户同意或提取资格，历史采集记录也不回填。（[#2451](https://github.com/oceanbase/powercontext/pull/2451)）
- 保留版本化 Source 证据声明与 Atomic Memory 精确版本快照；登记变化或投影重建后仍可核查历史证据。缺少声明或声明无法验证时保持中性，不增加基于信任的排序权重或写入权限。Source 引用同时保留序号与行范围定位信息。（[#2004](https://github.com/oceanbase/powercontext/pull/2004)、[#2296](https://github.com/oceanbase/powercontext/pull/2296)）
- 自动提取与协调保留所选证据中受支持的 ISO 时间限定信息。拒绝内容完全相同的冗余修订，通过有界纠正恢复并保留真正新增的事实。已有无日期记忆不回填，语义等价判断仍依赖模型。（[#2359](https://github.com/oceanbase/powercontext/pull/2359)、[#2358](https://github.com/oceanbase/powercontext/pull/2358)）
- 使用内置 Prompt 时，同一 Source 窗口的前序候选也按已有相关性阈值筛选。OceanBase 支持时使用原生 `minimum_should_match` 过滤，旧后端保留回退路径。向量匹配可能增加 Embedding 工作，协调调用减少不等于总成本降低。（[#2489](https://github.com/oceanbase/powercontext/pull/2489)、[#2498](https://github.com/oceanbase/powercontext/pull/2498)）

### 订阅模型与 Agent 集成

- 通过 `openai-codex:<model>` 复用已有、保存在文件中的 Codex CLI 登录，或通过 `github-copilot:<model>` 使用已有 OAuth token 与 Chat Completions 模型，支持 Memory、Experience、Profile 生成。这两类 Provider 不支持 Topic Memory、Dream 及内置推理保护。续期仍由 Codex CLI 负责，Server 与 Worker 重新读取凭据，不使用 refresh token，也不改写登录文件。（[#2432](https://github.com/oceanbase/powercontext/pull/2432)）
- 新增试验性 MiniMax 安装、配置与 doctor，适配 Bub 0.6.1。Codex、OpenCode 可单独关闭提示时召回而保留采集。共享打包的 Skill 与 Python 辅助代码，保留显式 Memory 类型，完善 Scope 错误提示；移除重复适配器信任提示时，仍保留 Server 策略与历史上下文边界。（[#2205](https://github.com/oceanbase/powercontext/pull/2205)、[#2492](https://github.com/oceanbase/powercontext/pull/2492)、[#2453](https://github.com/oceanbase/powercontext/pull/2453)、[#2455](https://github.com/oceanbase/powercontext/pull/2455)、[#2380](https://github.com/oceanbase/powercontext/pull/2380)、[#2318](https://github.com/oceanbase/powercontext/pull/2318)、[#2446](https://github.com/oceanbase/powercontext/pull/2446)）
- 增加 DSH npm 发布打包和 Desktop v4 消息兼容。通过仓库安装 `powercontext-dsh` 需完成实际发布和首次发布者配置；独立的 DSH 配置界面及跨 Agent 交接 RFC 仍为设计提案。（[#2501](https://github.com/oceanbase/powercontext/pull/2501)、[#2528](https://github.com/oceanbase/powercontext/pull/2528)）

### Gateway alpha 与 Server 稳定性

- 非回环 Gateway 监听必须配置有效密钥，在读取模型请求正文前完成认证；转发请求仍遵守独立的 Core 认证。保留原生协议行为，最终请求与响应缓冲限制对齐 Core 检查容量。（[#2523](https://github.com/oceanbase/powercontext/pull/2523)、[#2470](https://github.com/oceanbase/powercontext/pull/2470)、[#2513](https://github.com/oceanbase/powercontext/pull/2513)）
- 代码证据复用按文件重新验证。Gateway 代码注入需显式启用，过期证据可在发送到 Provider 前刷新一次。输出有界且不含正文的 JSONL 调用记录，区分调用尝试、放行决定、交付观测与未知用量。Gateway 仍为 alpha，缓冲完整响应后检查；交付观测不证明客户端已经收到内容。（[#2425](https://github.com/oceanbase/powercontext/pull/2425)、[#2499](https://github.com/oceanbase/powercontext/pull/2499)、[#2506](https://github.com/oceanbase/powercontext/pull/2506)）
- Server 对受保护请求先认证再读正文，Dream、标签和状态路由拒绝空白 Scope 标识，清理失败的 SQLite 连接 Worker，并支持启用访问强制检查时的所有权迁移。增加有界的 Topic Memory 失败诊断。（[#2512](https://github.com/oceanbase/powercontext/pull/2512)、[#2494](https://github.com/oceanbase/powercontext/pull/2494)、[#2502](https://github.com/oceanbase/powercontext/pull/2502)、[#2468](https://github.com/oceanbase/powercontext/pull/2468)、[#2304](https://github.com/oceanbase/powercontext/pull/2304)、[#2396](https://github.com/oceanbase/powercontext/pull/2396)）

### 监控、桌面端与安装

- 提供可选 Grafana、Prometheus、Loki、Tempo、OpenTelemetry Collector 监控栈与 Phoenix，以及 `powercontext observability up|status|check|down|connect`。服务、Scope、处理与 Provider 诊断关联不含正文的观测；Gateway 标准输出中的证据需单独采集。（[#2388](https://github.com/oceanbase/powercontext/pull/2388)）
- Desktop Overview 引导连接已有 Server、选择精确 Scope 并进入记忆工作区；增加库存与用量、会话诊断和 Runtime 观测。就绪检查改为显式诊断，与身份验证及获授权的记忆操作分开。候选组装和发布工具本身不代表签名安装包或本地部署已完成验收。（[#2490](https://github.com/oceanbase/powercontext/pull/2490)、[#2220](https://github.com/oceanbase/powercontext/pull/2220)、[#2344](https://github.com/oceanbase/powercontext/pull/2344)、[#2474](https://github.com/oceanbase/powercontext/pull/2474)、[#2361](https://github.com/oceanbase/powercontext/pull/2361)、[#2475](https://github.com/oceanbase/powercontext/pull/2475)、[#2477](https://github.com/oceanbase/powercontext/pull/2477)）
- 新增按版本安装的 Bash、PowerShell 脚本，准备依赖，选择精确版本或最新稳定版，分别配置下载镜像并匹配 Agent ref。改进网站 Markdown 导出、SEO、构建内存使用及包元数据校验。（[#1892](https://github.com/oceanbase/powercontext/pull/1892)、[#2454](https://github.com/oceanbase/powercontext/pull/2454)、[#2536](https://github.com/oceanbase/powercontext/pull/2536)、[#2535](https://github.com/oceanbase/powercontext/pull/2535)）

### 安装与兼容性

```bash
uv tool install --force "powercontext[cli,server]==1.5.1"
```

Agent 集成使用 `powercontext-v1.5.1` ref。Server、SDK、CLI 与集成需协调升级，并重新打开 Agent 会话。

- Source 证据与定位信息需要执行登记迁移，推进至 `v1_5_0_004`。不可变 schema revision 保留原始 `1.5.0` 编号，不随应用版本更新而重命名。
- 升级策略仍为 `upgrade-required`。正常发布必须针对精确目标产物，通过已安装 1.3.0 在 SQLite、seekDB、OceanBase 上的全部十二种升级配置验收。该矩阵不自动认证独立的 1.4.0 桥接或中间发行版本。
- 1.3.0 和 1.4.0 升级必须通过 SQLite、seekDB 和 OceanBase 的完整 Server 验收；这些迁移路径目前尚未验证，请勿升级已有数据库。
- 自行注入 MiniMax Embedding 或 Jev 重排客户端的 SDK 调用方需使用 `httpx2.AsyncClient`。自定义 Atomic Memory Prompt 应遵循当前提取与协调契约。
- Windows 安装仍为试验性支持。Desktop 签名、本地安装整合及干净机器验收需独立完成。官方 MiniMax CLI 0.6.9/0.7.2 在已记录的受影响环境中，仍未通过仅插件路径调用验收；可用配置请参照集成指南。

**Full changelog / 完整变更：** [powercontext-v1.5.0...powercontext-v1.5.1](https://github.com/oceanbase/powercontext/compare/powercontext-v1.5.0...powercontext-v1.5.1)

**Release scope / 版本范围：** [Merged PRs labeled 1.5.1 / 已合并的 1.5.1 标签 PR](https://github.com/oceanbase/powercontext/pulls?q=is%3Apr+is%3Amerged+label%3A1.5.1)。Closed, unmerged PRs are excluded; changes already in v1.5.0 are not new in this release. 已关闭但未合并的 PR 不计入交付；已在 v1.5.0 中的变更不重复列为新增。

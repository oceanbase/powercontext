# PowerContext v1.5.2

## English

PowerContext v1.5.2 makes Agent long-term memory more efficient, transparent and easier to use, with single-Server performance improvements, native Dashboard observability, stronger Desktop and plugin support, OAuth subscription transports, and more reliable cross-tool handoffs.

- Improve single-Server efficiency through batched writes, reusable query structures, optimized vector conversion, and fewer repeated computations and database round trips. Maintainer-provided release measurements report approximately 190% higher write throughput, 171% higher query throughput, and 64%–67% lower CPU cost than v1.5.1. These measurements were not rerun during release preparation. The repository does not include the workload, hardware, concurrency, sample count, measurement date or reproduction command for these figures; they are not independently reproducible release acceptance evidence or a throughput guarantee for every deployment or end-to-end model extraction. ([#2567](https://github.com/oceanbase/powercontext/pull/2567), [#2532](https://github.com/oceanbase/powercontext/pull/2532), [#2449](https://github.com/oceanbase/powercontext/pull/2449))

- Add native Dashboard observability views for processing progress, failure diagnostics, Token usage and runtime state. Inspect saved Sources, generated Artifacts, Dream inputs and outputs, and read-only processing status. Historical Sources without a recorded save time retain an unknown value. ([#2557](https://github.com/oceanbase/powercontext/pull/2557), [#2568](https://github.com/oceanbase/powercontext/pull/2568), [#2539](https://github.com/oceanbase/powercontext/pull/2539), [#2619](https://github.com/oceanbase/powercontext/pull/2619))

- Improve Desktop local Server installation, recovery and management, package independent Ops resources, and add Linux systemd --user service integration. Protocol checks and local setup support do not replace signed-installer or clean-machine platform acceptance. ([#2306](https://github.com/oceanbase/powercontext/pull/2306), [#2503](https://github.com/oceanbase/powercontext/pull/2503), [#2471](https://github.com/oceanbase/powercontext/pull/2471))

- Load directory plugins that generate, render, search and change their own Artifacts, with plugin APIs and sandboxed Dashboard pages. Plugins can declare their own Source types. Plugins also gain per-plugin databases on OceanBase and seekdb (`powercontext_plugin_<name>`), Dashboard forwarding to their own Artifact API, Scope listing limited to their own readable Scopes, and fixes for user-change evidence and write/governance consistency. Hot reload, plugin data upgrades, plugin MCP tools and standalone Source collection are not included. ([#2616](https://github.com/oceanbase/powercontext/pull/2616), [#2660](https://github.com/oceanbase/powercontext/pull/2660))

- Add Gateway alpha OAuth subscription transports and improve request deadlines, cancellation and bounded shutdown. Provider capabilities differ: Gemini context injection is disabled, and Claude CLI is single-turn/text-only and unavailable under enforcing or unknown Core protection. Gateway continues to buffer complete responses for inspection. ([#2563](https://github.com/oceanbase/powercontext/pull/2563), [#2538](https://github.com/oceanbase/powercontext/pull/2538), [#2578](https://github.com/oceanbase/powercontext/pull/2578))

- Persist and recover DSH handoff deliveries, expose connection status and update checks, and embed the Dashboard in the sidebar. Add the Claude Desktop Code handoff adapter; native launch stays disabled by default until the receiving environment can be verified. Offer optional PowerContext long-term memory for dsh-pet. ([#2603](https://github.com/oceanbase/powercontext/pull/2603), [#2604](https://github.com/oceanbase/powercontext/pull/2604), [#2569](https://github.com/oceanbase/powercontext/pull/2569), [#2614](https://github.com/oceanbase/powercontext/pull/2614), [#2589](https://github.com/oceanbase/powercontext/pull/2589), [#2654](https://github.com/oceanbase/powercontext/pull/2654), [#2608](https://github.com/oceanbase/powercontext/pull/2608))

- Strengthen privacy handling in DSH automatic recall and capture, request confirmation for OpenCode writes under default permissions, and detect Memory protocol mismatches across Hosts. Improve Codex setup, preserve OpenClaw project bindings during capture, and checkpoint in-session auto-compaction. ([#2625](https://github.com/oceanbase/powercontext/pull/2625), [#2534](https://github.com/oceanbase/powercontext/pull/2534), [#2495](https://github.com/oceanbase/powercontext/pull/2495), [#2626](https://github.com/oceanbase/powercontext/pull/2626), [#2601](https://github.com/oceanbase/powercontext/pull/2601), [#2622](https://github.com/oceanbase/powercontext/pull/2622))

- Bound Topic Memory related history, preserve the healthy processing prefix before a failing or oversized look-ahead, and prevent concurrent head conflicts from consuming window attempts. Keep Memory merges within the tag limit and allow portable Source exports to retain recorded absolute working directories. ([#2632](https://github.com/oceanbase/powercontext/pull/2632), [#2530](https://github.com/oceanbase/powercontext/pull/2530), [#2597](https://github.com/oceanbase/powercontext/pull/2597), [#2596](https://github.com/oceanbase/powercontext/pull/2596), [#2572](https://github.com/oceanbase/powercontext/pull/2572))

- Upgrade Server, SDK, CLI and integrations together. The directory-plugin framework replaces the previous managed plugin APIs and configuration. Migration v1_5_0_005 refuses databases that retain old plugin registrations or data. There is currently no supported in-place upgrade path for these databases. Unloading plugins, deleting plugin directories, or exporting archives does not clear the retained framework records, even when no Artifacts were generated. Keep the database on the previous version until a supported migration is available. A separate fresh database can use the new version, but this does not migrate old plugin data; do not manually delete framework records to bypass the check. Portable Artifact revision records use schema version 3 and still restore version 2. Database upgrade support is limited to source-version, target-artifact, backend and configuration combinations that pass full Server acceptance. ([#2616](https://github.com/oceanbase/powercontext/pull/2616))

- Update imports from `powercontext.server.authz` to `powercontext.builtin.authz`, from `powercontext.cli.env_file` to `powercontext.env_file`, and from `powercontext.cli.inference_notice` to `powercontext.server.inference_notice`. `powercontext.paths.default_scheduler_path` has been removed. The `scheduler_path` argument is deprecated and ignored; passing a non-None value emits `DeprecationWarning`. Remove that argument from runtime construction. ([#2244](https://github.com/oceanbase/powercontext/pull/2244))

### Installation and compatibility

```bash
uv tool install --force "powercontext[cli,server]==1.5.2"
```

Use `powercontext-v1.5.2` for Agent integration refs and reopen Agent sessions. The removed plugin surface includes `/v1/admin/artifact-plugins/*`, `powercontext.artifact_plugins`, and `POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR`; use `POWERCONTEXT_SERVER_PLUGINS_DIR` for directory plugins. Immutable schema revision names retain their original numbering. See the [plugin guide](https://github.com/oceanbase/powercontext/blob/powercontext-v1.5.2/docs/en/docs/develop/plugins.md) and [database migration guide](https://github.com/oceanbase/powercontext/blob/powercontext-v1.5.2/docs/en/docs/operate/database-migrations.md).

## 中文

PowerContext v1.5.2 让 Agent 的长期记忆更高效、更透明，也更易用。本次更新围绕单 Server 性能、Dashboard 原生可观测性、Desktop 与插件体验、OAuth 订阅传输，以及更可靠的跨工具交接展开。

- 通过批量写入、查询结构复用、向量转换优化，以及减少重复计算和数据库往返，提升单 Server 的处理效率。维护者提供的发布测量结果相比 v1.5.1，写入吞吐提升约 190%，查询吞吐提升约 171%，CPU 成本降低 64%–67%。本次发布准备未重新运行这些测量。仓库未提供这些数字对应的负载、硬件、并发数、样本数、测量日期或复现命令；它们不构成可独立复现的发布验收证据，也不代表所有部署或端到端模型提取的吞吐保证。 ([#2567](https://github.com/oceanbase/powercontext/pull/2567), [#2532](https://github.com/oceanbase/powercontext/pull/2532), [#2449](https://github.com/oceanbase/powercontext/pull/2449))

- 新增 Dashboard 原生可观测性面板，集中展示记忆处理进度、异常诊断、Token 用量和运行状态。可查看已保存的 Source、生成的 Artifact、Dream 输入输出，以及只读处理状态；历史 Source 未记录保存时间时仍显示未知。 ([#2557](https://github.com/oceanbase/powercontext/pull/2557), [#2568](https://github.com/oceanbase/powercontext/pull/2568), [#2539](https://github.com/oceanbase/powercontext/pull/2539), [#2619](https://github.com/oceanbase/powercontext/pull/2619))

- 完善 Desktop 本地 Server 的安装、恢复与管理，打包独立 Ops 资源，并新增 Linux systemd --user 服务集成。协议检查与本地安装支持不替代签名安装包和各平台干净机器验收。 ([#2306](https://github.com/oceanbase/powercontext/pull/2306), [#2503](https://github.com/oceanbase/powercontext/pull/2503), [#2471](https://github.com/oceanbase/powercontext/pull/2471))

- 支持从目录加载插件，由插件生成、展示、搜索和修改自身 Artifact，并提供插件 API 与沙箱化 Dashboard 页面。插件可声明自身 Source 类型。 插件还支持 OceanBase 与 seekdb 独立数据库（`powercontext_plugin_<name>`）、Dashboard 转发自身 Artifact API，以及仅列出自身可读 Scope，并修复用户修改证据和写入与治理状态的一致性问题。本次不包含热重载、插件数据升级、插件 MCP 工具和独立 Source 采集。 ([#2616](https://github.com/oceanbase/powercontext/pull/2616), [#2660](https://github.com/oceanbase/powercontext/pull/2660))

- Gateway alpha 新增 OAuth 订阅传输支持，完善请求超时、取消和有界关闭。各 Provider 能力不同：Gemini 不启用上下文注入，Claude CLI 仅支持单轮文本，且在 Core 保护处于强制执行或未知状态时不可用。Gateway 仍缓冲完整响应以完成检查。 ([#2563](https://github.com/oceanbase/powercontext/pull/2563), [#2538](https://github.com/oceanbase/powercontext/pull/2538), [#2578](https://github.com/oceanbase/powercontext/pull/2578))

- DSH 支持交接投递的持久化与恢复、连接状态与更新检查，并在侧栏嵌入 Dashboard。新增 Claude Desktop Code 交接适配器；在能够验证接收环境前，原生启动默认保持禁用。为 dsh-pet 提供可选的 PowerContext 长期记忆能力。 ([#2603](https://github.com/oceanbase/powercontext/pull/2603), [#2604](https://github.com/oceanbase/powercontext/pull/2604), [#2569](https://github.com/oceanbase/powercontext/pull/2569), [#2614](https://github.com/oceanbase/powercontext/pull/2614), [#2589](https://github.com/oceanbase/powercontext/pull/2589), [#2654](https://github.com/oceanbase/powercontext/pull/2654), [#2608](https://github.com/oceanbase/powercontext/pull/2608))

- 加强 DSH 自动召回与采集中的隐私保护，在 OpenCode 默认权限下要求写入确认，并检测跨 Host 的 Memory 协议不匹配。完善 Codex 安装，保留 OpenClaw 采集时的项目绑定，并为会话内自动压缩记录检查点。 ([#2625](https://github.com/oceanbase/powercontext/pull/2625), [#2534](https://github.com/oceanbase/powercontext/pull/2534), [#2495](https://github.com/oceanbase/powercontext/pull/2495), [#2626](https://github.com/oceanbase/powercontext/pull/2626), [#2601](https://github.com/oceanbase/powercontext/pull/2601), [#2622](https://github.com/oceanbase/powercontext/pull/2622))

- 限制 Topic Memory 相关历史候选规模，在后续内容失败或过大时保留可正常处理的前缀，并避免并发版本冲突消耗窗口尝试次数。保证 Memory 合并不超过标签上限，支持导出记录了绝对工作目录的 Source。 ([#2632](https://github.com/oceanbase/powercontext/pull/2632), [#2530](https://github.com/oceanbase/powercontext/pull/2530), [#2597](https://github.com/oceanbase/powercontext/pull/2597), [#2596](https://github.com/oceanbase/powercontext/pull/2596), [#2572](https://github.com/oceanbase/powercontext/pull/2572))

- Server、SDK、CLI 与集成需协调升级。目录插件框架替换旧的托管插件 API 与配置。迁移 v1_5_0_005 会拒绝仍保留旧插件登记或数据的数据库；这些数据库目前没有受支持的原地升级路径。卸载插件、删除插件目录或导出归档不会清除框架保留的记录，即使没有生成 Artifact 也是如此。请保留数据库并继续使用旧版本，等待受支持的迁移方案。可以使用独立的新数据库运行新版本，但这不会迁移旧插件数据；不要手工删除框架记录来绕过检查。可移植 Artifact 修订记录使用 schema version 3，并继续支持恢复 version 2。数据库升级支持仅限于通过完整 Server 验收的源版本、目标产物、后端与配置组合。 ([#2616](https://github.com/oceanbase/powercontext/pull/2616))

- 将导入路径 `powercontext.server.authz` 改为 `powercontext.builtin.authz`，`powercontext.cli.env_file` 改为 `powercontext.env_file`，`powercontext.cli.inference_notice` 改为 `powercontext.server.inference_notice`。`powercontext.paths.default_scheduler_path` 已移除。`scheduler_path` 参数已废弃且被忽略；传入非 None 值会发出 `DeprecationWarning`。请从运行时构造调用中移除该参数。 ([#2244](https://github.com/oceanbase/powercontext/pull/2244))

### 安装与兼容性

```bash
uv tool install --force "powercontext[cli,server]==1.5.2"
```

Agent 集成使用 `powercontext-v1.5.2` 引用，升级后重新打开 Agent 会话。移除的旧插件接口包括 `/v1/admin/artifact-plugins/*`、`powercontext.artifact_plugins` 和 `POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR`；目录插件改用 `POWERCONTEXT_SERVER_PLUGINS_DIR`。不可变 schema revision 名称保留原始编号。参见[插件指南](https://github.com/oceanbase/powercontext/blob/powercontext-v1.5.2/docs/zh/docs/develop/plugins.md)和[数据库迁移指南](https://github.com/oceanbase/powercontext/blob/powercontext-v1.5.2/docs/zh/docs/operate/database-migrations.md)。

**Full changelog / 完整变更：** [powercontext-v1.5.1...powercontext-v1.5.2](https://github.com/oceanbase/powercontext/compare/powercontext-v1.5.1...powercontext-v1.5.2)

**Release scope / 版本范围：** [Merged PRs labeled 1.5.2 / 已合并的 1.5.2 标签 PR](https://github.com/oceanbase/powercontext/pulls?q=is%3Apr+is%3Amerged+label%3A1.5.2). Closed, unmerged PRs and design-only proposals are excluded from delivered features. 已关闭但未合并的 PR 和仅提出设计方案的内容不计入已交付功能。

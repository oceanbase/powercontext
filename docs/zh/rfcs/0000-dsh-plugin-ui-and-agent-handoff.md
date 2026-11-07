---
title: DSH 插件界面、内嵌 Dashboard 与跨 Agent 交接
description: 通过插件实现 DSH 连接状态、现有 Dashboard 内嵌及向其他 Agent 精确交接的设计提案。
---

- Proposal Name: `dsh_plugin_ui_and_agent_handoff`
- Start Date: 2026-11-06
- Status: Draft
- RFC PR: 尚未创建；RFC 编号在创建 RFC PR 后分配
- Tracking Issue: [oceanbase/powercontext#2514](https://github.com/oceanbase/powercontext/issues/2514)
- Related RFCs: [RFC 0048](0048_handoff_artifact.md)、[RFC 1223](1223_human_agent_work_continuity.md)、[RFC 1345](1345_scope_organization_and_agent_integration.md)、[RFC 1783](1783-handoff-session-rollover.md) 和 [RFC 1629](1629_dashboard_profile_and_handoff_markdown_export.md)

# Summary

扩展 PowerContext DSH 插件，提供连接状态入口、现有 Dashboard 的内嵌展示，以及从最新回复交给其他 Agent 继续工作的动作。用户仍通过 `powercontext setup` 安装。插件使用现有 DSH Client 扩展位置和自身的 Host HTTP 接口；在通过验证的本机部署中，由 Node Host 代码通过受限系统启动器和目标应用支持的链接打开目标。

交接流程调用 PowerContext 已有的 Handoff 操作准备并保存完整载体，在指定目录打开新会话，预填简短接续提示，由用户发送。目标核对实时条件并确认收到这份精确 Handoff。打开应用不等于交付已确认。首期必须完成三个目标：Codex Desktop、Claude Desktop 的 Code 会话、Claude Code CLI。仅支持本人使用的本机 DSH 部署。

本设计不依赖修改 DSH 源码、Electron IPC、普通外链策略或内置应用目录，不增加 PowerContext Task/Transfer 领域对象，不自动提交里程碑，也不引入受管 Agent 运行时。本文是设计提案，不代表相关界面或启动能力已经发布。

# Motivation

目前用户需要自行串联插件诊断、打开 Dashboard、整理当前工作、保存正确的 Handoff、选择目标目录和说明如何继续等动作。这容易丢失未完成工作、选错 Scope，或把应用已启动误认为交接已完成。

集成应当就地回答三个问题：PowerContext 是否连接、当前项目发生了什么，以及如何让另一个 Agent 继续这次任务。同时必须区分宿主能力和模型选择、临时交付和里程碑提交、接收确认和最终任务完成。

DSH 修改不在交付范围内。所有实现 PR 均属于 PowerContext。某项功能如果需要当前不存在的 DSH 扩展能力，就应在该宿主版本上保持不可用，而不是要求分发修改过的 DSH。

# Guide-level explanation

## 连接与 Dashboard

DSH 左下角用户区域上方显示小尺寸 PowerContext 图标。状态颜色同时配有文字或无障碍标签：

| 状态 | 含义 |
| --- | --- |
| 灰色 | 未配置或已禁用 |
| 黄色 | 正在连接或重试 |
| 绿色 | 在已配置访问模式下，通过成功响应验证连接与必要就绪条件 |
| 红色 | 连接、认证或必要能力失败 |

观测过期后不能一直保持绿色。Scope 解析、近期召回、Source 接收、Memory 生成和上下文注入分别展示。插件安装不等于 Server 启动，Source 已接收也不等于 Memory 已生成。

点击入口在右侧面板打开现有 PowerContext Dashboard，优先直接嵌入 URL，只在必要时补登录态或容器适配。本机 Server 开启 Dashboard 且关闭认证时，无需登录；开启认证时保留 token 要求。切换会话时同步选中 Scope；没有会话时使用已有 overlay 或外部入口，不创建空会话。首期验收重点是查看运行详情；内嵌不新增权限，也不移除原网页已有确认。另做一套削减权限的 Dashboard 不是前提。

## 交给其他 Agent 继续

动作仅出现在整个会话最新的合格完成回复下方。点击后展开 Agent 二级菜单，展示名称、本地图标、可用性和可处理的限制。支持悬浮、点击及键盘操作。

正常流程为：

```text
选择目标 → 检查 Agent 是否安装 → 检查其中的 PowerContext
         → 缺少插件时询问安装：否则取消，是则安装并验证
         → 核对目录 / Server / Scope
         → 调用 PowerContext Handoff → 保存返回的完整载体
         → 打开新会话并预填 → 用户发送
         → PowerContext Continue 与接收确认
```

目标 Agent 未安装时停止，弹出“请先安装 [Agent] 再交接”，不自动安装 Agent，也不准备 Handoff。Agent 已安装而缺少 PowerContext 时，询问“是否先在 [Agent] 安装 PowerContext 并继续？”选择否取消；选择是只安装该集成，验证通过后继续。已安装但损坏或禁用的情况单独识别。

插件保留源会话和目标已有草稿。目标应用首次使用时可能要求确认目录。目标使用自己的模型设置和凭据；PowerContext 不选择或借用订阅、模型账户。

目标从未打开过某个已有本地目录时，使用它支持的新会话入口，不创建应用私有项目记录。目录缺失时让用户选择有效目录；clone 或新建目录不属于自动交接路径。

没有通过预填验证的目标提供复制、导出或配置指引。开发框架是集成目标，不是可以直接启动的应用。用户可以在启动前检查交接包；尚未确认或固定保留的包不自动删除。

DSH 展示等待动画和真实阶段：检查环境、安装 PowerContext、准备 Handoff、打开目标或等待接收方。需要用户操作时明确提示，例如“请在 Claude Code CLI 发送已预填的提示”。不显示虚构百分比，也不因应用已打开就暗示它已经开始工作。

## 示例

用户在 DSH 中得到一条关于尚未修复测试失败的回复，然后选择 Codex。插件先检查本机启动条件、Codex 和其中的 PowerContext 集成；缺少集成时先取得安装同意并验证安装结果，再固定源回复边界，将当前任务信息交给现有 PowerContext Handoff 流程。它保存完整 Prepared Handoff，检查目标目录和 Scope，然后打开 Codex 新会话，预填引用该交接包的简短提示。

用户发送后，Codex 读取精确交接包，核对当前仓库和授权，调用 Continue，再使用预分配的 receipt source ID 写入确认。DSH 只有读回并验证确认后才显示已接收。如果 Codex 只是打开，状态仍是“等待接收方确认”。

# Reference-level explanation

## 1. 源码基线与职责

已检查的源码基线为 PowerContext `961b1aa2574f410a6412aff4cecd56d7a5a07288` 和 DSH `ce7073d51254c26cd63b22a60835b8f948deb447`（`0.2.2-alpha.2`）。PowerContext DSH 包版本为 `0.0.2`。这些是 checkout 事实，不是已支持发行版的声明。

验收基准是 DSH 当前最新的官方发布版本，不是已检查的 checkout。2026-11-06 核实的最新发布为 [v0.2.2-rc.1](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.2-rc.1)，发布于 2026-11-04，官方标记为 prerelease/候选版本。实施开始和发布验收时重新核对官方列表，固定实际版本和安装包。首期不要求兼容全部旧版，不静默升级 DSH，也不把不断变化的“latest”声明为已经测试的范围。

该 DSH checkout 已有以下机制：

| 机制 | 源码位置 |
| --- | --- |
| 侧栏底部动作 | `packages/client/ui-sidebar/src/client/SidebarRoot.tsx` |
| 模型回复动作 | `packages/client/ui-chat/src/client/chat/TurnTailNodeView.tsx` |
| 右侧栏标签 | `packages/client/ui-sidebar-browser/src/client/index.ts` |
| 通过 `dsh.client` 与 `./client` 发现外部 Client | `packages/client/modules/src/index.ts` |
| 带身份检查的 Host 路由 | `packages/host/open-in-app/src/index.ts` |
| Node Host 启动进程 | `packages/host/open-in-app/src/resolver.ts`、`apps/desktop/src/host-process.ts` |
| 应用 origin 转发到 Host | `apps/desktop/src/main.ts`、`apps/desktop/src/web-document.ts` |
| 按作用域创建 Agent 与工具策略 | `packages/core/agent/src/index.ts`、`packages/core/tools/src/index.ts` |

DSH Desktop 壳将应用页面中非壳资源路径转发到 Host，并附加壳持有的认证 Cookie，但会剥离响应里的 `Set-Cookie`。现有 Browser 插件使用隔离的 Desktop webview；本提案不假定它共享主应用身份。

PowerContext 负责 Client 包、Host 接口、目标适配器、交付记录和 Dashboard 嵌入支持。Handoff 校验和授权仍由 PowerContext 对应业务服务负责。DSH 与目标应用均作为不修改的依赖。

## 2. 打包与 setup

在 `integrations/dsh/plugins/powercontext/` 中增加独立浏览器入口，导出 `./client`，声明 DSH Client 并打包 UI 资源。现有 `src/client.ts` 仍是调用 Server 的 HTTP 客户端。构建、发布包和插件配置一起修改，不能依赖只在开发环境有效的模块解析。

无 UI 的宿主将界面服务作为可选能力；没有 Client 时现有工具和生命周期行为继续工作。卸载时释放路由、视图、订阅和组件。`powercontext setup` 继续选择正确的 Web/Desktop profile 及 Desktop 随包 runtime，分别报告安装、能力和连接结果，不静默启动 Server。

只有通过真实发行包安装和 UI 路由验证后才能声明对应 DSH 版本范围。缺少 Client 或路由能力时，该版本禁用新界面，现有工具继续可用。

## 3. Host 接口与授权

使用 `/powercontext/` 下的插件自有 HTTP 路由，不新增 DSH 中央 RPC 描述。以下是插件内部接口，不是 PowerContext 新业务 API：

| 方法与拟议路由 | 用途与副作用 |
| --- | --- |
| `GET /powercontext/status` | 读取连接和当前 Scope 观测 |
| `GET /powercontext/targets` | 读取能力，不安装或启动 |
| `POST /powercontext/preflights` | 检测选中应用、集成与配置，返回限定范围的预检 ID |
| `POST /powercontext/preflights/{id}/install` | 用户明确同意后，在选定目标安装 PowerContext 集成 |
| `GET /powercontext/preflights/{id}` | 读取检测/安装/验证状态与待用户操作 |
| `POST /powercontext/preflights/{id}/cancel` | 取消预检/安装后的继续动作，保留已完成配置修改 |
| `POST /powercontext/deliveries` | 要求预检就绪并重新验证，调用 PowerContext 准备 Handoff |
| `GET /powercontext/deliveries/{id}` | 读取授权范围内的交付状态和预览 |
| `GET /powercontext/deliveries/{id}/export` | 下载已存在交接包，不 prepare 或 commit |
| `POST /powercontext/deliveries/{id}/launch` | 显式尝试已选定的受限目标启动 |
| `POST /powercontext/deliveries/{id}/cancel` | 取消待处理工作，不能撤销已完成安装或启动 |
| `GET /powercontext/deliveries/{id}/receipt` | 读取并验证本次分配的 PowerContext 回执 |

每个接口请求经过 DSH 原有连接/来源检查和具体操作校验。预检/交付 ID 是选择器，不是凭据；绑定本机用户、源会话、目标和已配置连接。拒绝浏览器传入的任意程序、shell 命令、替代凭据或任意 Server URL。安装和启动只通过显式 POST 发起，GET 或模型生成的链接不产生这些副作用。

仅支持本人使用的本机 Desktop 或明确配置为本机的 Web Host。远程/共享 DSH 不在本 RFC 范围内，不将多用户权限映射作为前置工程。本机使用也不意味着任意网页可以调用接口：即使 PC Server 无 token，仍保留 DSH 自身认证/来源检查。不能为方便内嵌而修改 Server 认证模式。

Dashboard 是拥有原有权限的现成网页，不获得本地启动器或安装器能力。在宿主支持时优先使用不同 origin 的 iframe 或已有隔离浏览器容器。净化 Source/模型内容，保留浏览器隔离。如果必须增加代理，则限定已配置的 PC origin 和所需 Dashboard 路由，凭据留在后台，并实际验证 frame 与特权接口的边界。不提供任意 URL 代理，不关闭 Web 安全设置。

## 4. 连接状态与 Dashboard 内嵌

复用连接/readiness 和运行观测，不轮询 `/pc doctor`，不发送诊断 prompt，不为状态调用生成模型。记录时间、连接代次、Scope 和稳定错误；连接改变后旧观测失效。

现有 Dashboard 是默认关闭的可选功能。[安装和运行](../docs/get-started/install-and-run.md)已说明本机访问模式：

| Server 配置 | 现有 Dashboard 行为 |
| --- | --- |
| Dashboard 关闭 | 无可用页面，显示开启/配置指引 |
| Dashboard 开启，`POWERCONTEXT_SERVER_ACCESS_MODE=disabled`，绑定 loopback | 直接查看，无需密码或 token |
| Dashboard 开启，`POWERCONTEXT_SERVER_ACCESS_MODE=enforced` | 使用现有静态 Server token 登录 |
| 注入团队认证/授权 Provider | 当前 Dashboard 不支持该部署，亦不在本人本机范围内 |

打开网页和启动 Server 是两件事。插件不静默重启 Server、开启 Dashboard 或降低认证。PC API 正常而 Dashboard 未开启时，不能把 PC 连接显示为失败。

优先在 DSH 右侧浏览器容器直接加载现有 `/dashboard/...` URL。Web 使用合适的 iframe；Desktop 先验证已有隔离 Browser webview 或该发行版支持的插件 frame。沿用现有 Scope 查询/导航行为。能直接嵌入时，不需要重写 Dashboard，也不强制增加反向代理。

启用 token 的配置先尝试在嵌入容器完成现有登录。Host 持有 Bearer 不会自动让 iframe 登录；Cookie 隔离、SameSite、origin、协议、CSP，以及 HTTPS 页面嵌入 HTTP 地址都可能需要适配。`dsh-app://app` 转发会剥离 `Set-Cookie`，但这不代表独立加载网页的浏览器容器也不能登录。必须测试实际容器，不能预先认定直接嵌入不可行。

只有直接加载或登录不能工作时，才增加必要的兼容/认证适配。固定上游的凭据桥和明确嵌入 base prefix 是备用方案，凭据不进入 URL、页面脚本或日志。不关闭认证，不替换任意 HTML，也不放宽全局 CSP/Web 安全设置。保留外部浏览器入口，但外部打开不算通过右栏内嵌验收。

首期必须能查看现有运行、用量、Memory、Source 和 Handoff 页面。保留原网页权限及写操作确认，不新增写权限。严格禁用全部已有写控件属于另一项产品/访问要求，不是套上 iframe 就自动实现。分别验证免密码及 token 模式下的加载、资源、导航、Scope 切换、语言/主题和窄栏布局。Dashboard 配置只通过明确的配置动作改变。

## 5. 使用现有 PowerContext Handoff 流程

Handoff 内容、证据校验、准备、接续和接收确认均由 PowerContext 负责。DSH 插件提供当前任务输入并协调打开目标，不另创 Handoff 格式、独立摘要产品或平行交接领域模型。

已有操作分别适用于不同输入：

| 可用输入 | 现有 PowerContext 路径 |
| --- | --- |
| 通过源 Agent 已有交接指引或经检查的可编辑快照提供当前工作字段 | `handoff_current_work` 捕获 boundary 并返回完整 Prepared Handoff |
| 有界当前任务证据，且已经配置 PC Handoff generation pipeline | 用现有 Source API 捕获缺失材料，再 `prepare_handoff` → 检查/校验 draft → `finalize_handoff` |
| 已明确选中同一源边界下的有效 Prepared Handoff | 复用精确原值，不重复生成 |
| 用户明确要求保留里程碑 | `commit_handoff`，保存返回的精确 Revision |
| 目标收到交接 | `continue_handoff`、实时核对，再 `acknowledge_handoff` |

`prepare_handoff` 已经具有生成流程。`handoff_current_work` 接受当前工作字段并确定性地准备载体，本身不会从任意 DSH 历史自动推断这些字段。Server 也不能自动看到尚未捕获的 DSH 回复、工具结果和当前工作区。补充这些输入属于宿主集成，不是重做 Handoff。

预检时选择并记录现有可用路径。需要宿主辅助形成当前工作输入时复用源 Agent 的已有 Handoff 指引，不强制另建独立摘要服务/会话。PC generation 已配置时可以由其直接起草，同一次交接不运行两套模型整理。如果两条自动路径都不可用，提供可编辑快照并调用 `handoff_current_work`，或明确说明缺少生成配置，不静默配置新的模型提供商。

检测及经同意的安装完成前，不调用模型、不向 Source 写入任务内容。点击时记录所选源会话/回复。如果安装期间源会话已变化，询问继续原选定边界还是选择新回复，不静默换任务。接受后固定 `sessionId`、`messageId`、`throughSeq`、当前有效目录和 `workspaceObservedAt`。

读取有界材料，保留当前目标、用户约束、所选完成回复和相关观测：最多 512 事件 / 256 KiB，其中工作区观测不超过 32 KiB，同时受更低的模型/token 预算约束。明确记录 omissions，不能把未捕获的完整历史当作现成证据。

源 Agent 辅助属于按已有指引执行、可查阅记录的准备动作，只允许必要 Handoff 调用和固定只读观测，禁止无关的项目修改工具。DSH 全局工具限制不能单独覆盖 scoped 注册和所有 executor，必须验证真正执行边界。无法安全自动辅助时使用可编辑路径。PC generation 不因此获得 DSH 文件工具，不引入一次宽泛的自主任务执行。

通过 PC 原有模型/服务验证结果。没有证据的陈述使用 `basis: declared`，`verified` 要求精确证据；原样保留返回的 Prepared 值。每次显式请求最多自动起草一次，生成超时为 90 秒；安装、登录和用户操作采用独立状态与预算。失败不静默切换另一模型路径重试，准备后修正产生新交付版本，默认不 commit 里程碑。

## 6. 交付记录与载体

交付记录是插件传输记账，不是 PowerContext 新领域实体。在 Host 自有用户数据目录中保存带版本的 envelope，拟议版本为 `powercontext.dsh-delivery.v1`。

| 字段组 | 必须表达的含义 |
| --- | --- |
| 身份 | 交付 ID、所属 DSH 身份/会话、创建时间、schema 版本 |
| 源边界 | 消息 ID、包含该事件的截止点、工作区观测时间、初始目录与当前有效目录 |
| 连接 | 不含秘密的 Server 身份/配置引用、连接代次、Scope、通过验证的目标预检/安装结果 |
| 载体 | 完整 Prepared Handoff 和实际存在的精确 Source receipts，或完整精确 Artifact Revision 引用；不能是 `latest` |
| 目标 | 目标能力目录 ID、已验证适配器/版本、部署事实、选定目录、可选的已验证接收身份 |
| 尝试 | 准备结果、启动尝试 ID/状态、取消及结果未知标记 |
| 回执 | 每次接收尝试预分配的 ACK source ID；读到的精确 SourceRef 与验证后的身份 |
| 文件 | 交接包位置、文件完整性摘要、过期/保留状态；不包含凭据 |

`handoff_current_work` 路径保留完整 `PreparedWorkHandoff.handoff` 和返回的 boundary receipt；`finalize_handoff` 路径保留其返回的 Prepared 值与实际输入 Source 引用，不虚构 boundary receipt。保留必要 null、`base` 和 `generation`。`base` 是提交并发前提，不是本次交接的 Revision。boundary Source 不能替代完整 Prepared 值。遵循规范序列化；文件校验和与 Prepared 内容摘要用途不同。

模型或 Server 请求前，先保存交付身份和快照元数据。prepare 成功后，在允许启动前原子保存完整验证结果。使用每份交付的锁和请求去重。如果 Server 已完成 prepare，但完整载体还未保存到本地就崩溃，则准备结果未知；读回输入/boundary Source 或基于今天的 head 重新 finalize 都无法恢复原值。没有精确恢复路径时，只能明确提供生成新版本的操作。

默认交接不提交里程碑。可选的显式“保存里程碑并交接”使用现有 commit/CAS 行为，并保存返回的精确引用。它不能成为本机恢复或交付的隐藏前提。

创建仅当前用户可访问的目录/文件，原子写入，拒绝目录越界和不安全的符号链接/覆盖行为；默认不向项目仓库写入交接包。Host 可读不证明目标 sandbox 可读。使用已验证的共享位置、明确导出位置，或目标可访问的精确已提交引用。路径未验证时提供下载/导入，不宣称已实现自动交付。

尚未确认接收和固定保留的载体不自动删除，恢复元数据随载体一起保留。已确认、未固定保留的载体七天后可清理；已结束交付的元数据保存 30 天。显示保留策略，允许固定/导出/显式删除；清理不删除 Server 记录。初始可配置上限为 100 条载体记录或 50 MiB，达到上限时提示清理或明确调整限制，不静默淘汰待接收/固定保留工作。

不支持的未来 envelope 版本直接拒绝，不重写原文件。卸载停止活动，并保留已有交付，直至明确删除或按已告知的清理策略处理，不静默清除待接收工作。

## 7. 目录、Scope 与本机启动

目录、应用项目条目、应用会话和 PowerContext Scope 是不同对象。解析 DSH 当前有效目录，而不只读取初始 session header。启动前再次验证规范路径身份、存在性、访问权限、worktree/子目录行为和当前状态。

同一个目录在 DSH、Codex、Claude 中可能解析到不同 Scope，因为 integration key 和会话/工作区绑定优先级不同。预检必须在目标启动 hook 注入上下文之前确定目标 Server 和有效 Scope 路由。提示词无法修复已经加载错误 Scope 的 hook。冲突进入明确的绑定/配置流程，不能静默修改全局 binding 或载体 Scope。如果无法确定未来会话路由，降级为辅助交接，不宣称自动保持 Scope 一致。

| 部署 | 首期启动策略 |
| --- | --- |
| 本人本机部署的 DSH Desktop Host | 允许三个经过验证的首期适配器 |
| 明确配置为本机的 DSH Web Host | 允许经过验证的本机适配器，并显示启动所在机器 |
| 远程、SSH、共享、容器或位置未知的 Host | 不支持此部署，不在其中执行目标检测/安装/启动 |
| 尚未验证的平台或应用版本 | 保留准备和手工交付，禁用一键预填 |

Loopback URL、Client 自报及没有 `SSH_*` 都不能证明本机部署。在可信部署事实或明确的本机所有者配置建立受支持模式前，默认关闭原生启动。仅支持本人本机部署，不新增共享用户或远程 DSH 启动模式，也不新增 companion 常驻进程。

Host 仅接受已保存的交付 ID 和目标选择，重新校验后自行生成官方 URI。在 macOS 上可以使用固定 `/usr/bin/open` 和结构化 argv 打开白名单中的 Codex/Claude URI。禁止接受 shell 片段、任意程序、用户传入的 URI scheme 或携带凭据的链接。清理子进程环境，并对早期错误观察设置上限。不导入 DSH 私有 resolver，也不修改其固定应用目录。

使用引用精确交接包/Revision 和 ACK source ID 的简短提示。对字段做 URL 编码，实施适配器自己的长度限制，不静默截断 URI，不把完整对话、carrier 或长期 token 填进链接。默认不复用旧会话或未发送草稿。

### 目标检测与经用户同意的安装

Codex Desktop、Claude Desktop 和 Claude Code CLI 是三个独立目标。选中目标后按顺序执行：

1. 使用受支持的应用/包信息和有界版本探测，检查该具体应用或 runtime。桌面应用不能仅因 `PATH` 中没有命令就判为未安装；区分不存在、不支持和检测未知。
2. 未安装时立即停止，弹出 Agent 未安装提示并提供安装指引，不自动安装 Agent，不捕获任务内容、不调用模型、不创建 Handoff。
3. 已安装时，检测目标实际环境中的 PowerContext：安装、启用、必需的 Handoff/Continue/ACK 工具、Server 访问模式及连接、Scope 路由。磁盘存在几个文件不等于可用。
4. 缺少 PowerContext 时弹出“是否先在 [目标] 安装 PowerContext 并继续？”提供是/否。否则取消本次交接；是则只授权这次集成安装/配置，展示进度，完成后重新验证，验证通过才准备 Handoff。
5. 已安装但禁用、损坏或配置有误时，明确提示具体启用/修复/配置动作，修改前取得同意，不盲目重装或覆盖设置。
6. 只有选定目标就绪才继续。需要重启/登录/手动步骤时显示指引，完成并验证后恢复；安装失败、取消或结果未知不能当作就绪。

复用 PowerContext 已有 setup/installer 和 doctor 能力。`src/powercontext/cli/system.py` 中现有 Codex installer 与 `ClaudeInstaller` 是实现起点，不证明 Desktop/CLI 完全相同。当前 `ClaudeInstaller` 依赖 `claude` 可执行程序；需要验证只装 Claude Desktop 时受支持的安装路径，不能假定 CLI 已存在或静默加装 CLI。Codex 也要识别应用随附 runtime，不能把应用存在等同于 `PATH` 上有 `codex`。

复用已有受支持连接配置和凭据存储，秘密不进入 URL 参数或日志。安装源和包身份可信且固定，不接受浏览器传入任意命令。同一实际目标配置的安装串行去重，保留无关设置，部分完成但结果未知时不能直接丢弃。取消会结束交接和后续动作，不自动卸载已经装好的集成或撤销无关配置；重启目标不能丢失未发送工作。

两种 Claude 环境可能共用部分文件，但必须在各自实际 runtime 中核对。CLI 安装成功不证明 Desktop Code 会话加载了插件。最新版本如果没有受支持的自动路径，应提供引导并保持该项验收未完成；只提供复制不能算完成三个首期目标。

## 8. 目标能力目录

目录分别记录 `kind`、PC 工具支持、启动方式、预填方式、支持的平台/版本、安装状态、必需配置及最近验收证据。运行时探测不能执行模型 prompt。复用批准分发的本地图标或中性备用图标，不在运行时抓取 README 图片。

基线 README 包含 11 个宿主、Bub 和三个框架：

| 目标 | 首期处理 |
| --- | --- |
| Codex | 首期必需目标：Codex Desktop；检测真实 runtime/集成并验证目录、新会话、预填 |
| Claude Code | 两个首期必需目标：Claude Desktop Code 会话与 Claude Code CLI；分别检测、安装/验证 PC，并验证未发送预填 |
| DeepSeek Harness | 菜单展示手工指引；宿主内新会话接续可后续独立实现 |
| MiniMax Code | 配置/复制/导出；CLI 直接执行 prompt 不算预填 |
| ZCode | 配置/复制/导出，直至受支持外部入口完成验证 |
| Kimi Code | 配置/复制/导出；按版本区分的 CLI 执行另行处理 |
| Hermes Agent | 配置/复制/导出；不能把首轮 query 执行当成草稿 |
| Pi Coding Agent | 复制/导出；基于扩展的编辑器预填留待后续 |
| OpenClaw | 复制/导出；后续启动遵守自身 agent/workspace/session 模型 |
| OpenCode | 复制/导出；后续先绑定专属 TUI/session，再追加提示 |
| WorkBuddy | 复制/导出；平台审批、签名、目录行为和草稿保留单独推进 |
| Bub | 评测入口，不宣称生产交接已通过验证 |
| Pydantic AI | 开发接入，需要明确的 runner |
| LangChain | 开发接入，需要明确的 runner |
| LangGraph | 开发接入，需要明确的 runner |

README 仍为 15 个集成系列；Claude 展开为两个交接目标，不是新增一个集成系列。目标 ID 分别为 `codex-desktop`、`claude-desktop`、`claude-code-cli`。目标名单跟随 README，但资格验证按具体版本记录。实际安装的工具要独立核对，不能仅依赖 manifest 或生成矩阵。不支持自动启动的目标仍显示真实可用的动作，不能只给一个没有解释的禁用菜单项。

## 9. 状态、重试与接收确认

对用户来说，接收确认就是新 Agent 通过 PowerContext 回报“我已经读到这份交接，可以继续了”。检测到应用和安装好 PowerContext 都只是前置条件，不证明它读到了任务。

分别保存预检/安装、准备、启动尝试和接收状态；单一 success 标记无法表达关键事实。

| 维度 | 状态与转换 |
| --- | --- |
| 预检/安装 | `checking_agent -> checking_integration -> ready`，或 `agent_missing`、`needs_install_consent`、`installing`、`verifying`、`needs_user_action`、`failed`、`cancelled`、`unknown` |
| 准备 | `preparing -> prepared`；或 `needs_input`、`failed`、`cancelled`、`unknown` |
| 每次启动 | `requested -> opened`、`failed` 或 `unknown` |
| 接收 | `awaiting -> accepted`、`needs_clarification` 或 `declined`；另有 `read_unavailable` 诊断 |
| 保留 | 可用、固定保留、过期或显式删除 |

`opened` 只表示 OS 接受打开请求，不表示草稿可见或已发送；`awaiting` 不证明用户是否已经发送。即使启动结果未知，后续收到的回执仍可验证。取消会停止准备及后续动作，但不能召回已启动应用或已完成的 Server 写入。

并发重复请求复用同一交付和活动尝试。如果 OS 已接受但结果尚未保存时崩溃，就无法保证恰好打开一次：保留 `unknown`，由用户显式重试。新启动尝试不重新生成或提交载体。接收确认内容发生实质变化时分配新的接收尝试 source ID，保留历史。

接收指引明确预期 ACK source ID。目标读取完整载体、核对 Server/Scope、调用 `continue_handoff`，检查证据、实时状态、能力及当前授权，再调用 `acknowledge_handoff`。需要认证时，`receiver` 是真实认证 Principal，不是目标目录中的 Agent 名称。

通过已有 `get_source` 按精确 Scope/source type/source ID 读取，验证类型化 receipt、`prepared_digest` 或 exact Revision、接收检查和 enforced access 下已提交的 receipt identity。Source type 必须遵守现有捕获/读取契约；`metadata.kind="handoff-receipt"` 不自动等于 source type。匹配本次分配的接收尝试，不能使用同一 Scope 下任意 ACK。摘要遵守已有 Python 模型序列化契约，包括 null 和字段顺序，并通过跨语言 fixture 验证；任意 JSON key 排序并不等价。

已知目标 Principal 时必须匹配该身份，否则展示实际验证的身份，不能暗示回执证明了具体应用窗口收到内容。形似 receipt 的普通 Source 不足以作为确认。没有认证归属的部署将结果标为接收方自行报告的确认。目标使用其他 source ID 时保持未确认，直到用户提供精确 SourceRef 并通过完整校验。

区分回执不存在、不可读、权限不足及服务暂不可用。仅在活动视图进行有界退避轮询，保留手动刷新。不用当前全 journal WorkContinuity 投影驱动 UI 轮询。用大量无关历史验证点查成本；只有确有必要时才补针对性存储索引。

### 等待动画与恢复体验

在源回复处显示进度面板，并提供可恢复的交付详情入口。检测、安装、准备和打开阶段使用旋转/脉动动画与阶段文字；等待用户/接收方时使用轻量等待动画并给出明确指引。尊重减少动态效果设置，提供等价静态状态。不显示虚构百分比，不阻塞 DSH 普通对话，不在实际需要用户决定时暗示后台一直工作。

等待属于交接状态；左下连接标识仍反映真实连接，不能仅因尚未收到确认就变红。

同一活动尝试禁用重复动作，提供取消、查看交接及适当的重试/刷新。用户操作阶段显示“请在 [目标] 发送已预填提示”或所需重启/登录步骤。五分钟没有 ACK 时停止持续轮询/动画，改为“尚未确认”和手动刷新，不删除载体，也不判定任务失败。关闭面板不取消交接，重新打开恢复真实阶段。取消不能撤销已完成的 OS 启动或抹除已写 ACK；后来收到精确确认时仍如实显示。

临时 Prepared Handoff 可以 ACK，但当前 Outcome 回执关联要求 accepted 的精确 committed Handoff。本 RFC 不扩展该契约，也不为获得这种关联而自动 commit。

## 10. 建议预算与诊断

| 操作 | 拟议默认值 |
| --- | --- |
| 可见时状态刷新 | 每 10 秒；有界重试、隐藏时暂停、明确过期状态 |
| Readiness | 复用已有传输语义，包括冷启动等待余量 |
| 控制请求体 | 64 KiB；可编辑输入过大时明确拒绝 |
| 模型快照 | 最多 512 事件 / 总计 256 KiB，工作区观测不超过 32 KiB |
| Handoff 起草/模型步骤 | 一次尝试、90 秒超时；只使用一条现有 PC/源 Agent 路径 |
| 安装/外部用户操作 | 独立可见阶段和目标专属超时，不套用模型的 90 秒期限 |
| 导出交接包 | 最大 2 MiB；超限拒绝，不静默丢弃字段 |
| 回执轮询 | 从 2 秒退避至 30 秒；五分钟后或隐藏时暂停 |
| 可选认证嵌入桥 | 必要时采用 15 分钟 view 并重新授权；直接加载 URL 不需要该 view |
| 交付保留/存储 | 已确认未固定载体七天；已结束元数据 30 天；待接收/固定载体保留；100 条 / 50 MiB 可配置上限 |

这些是初始可配置宿主预算，不是 Core schema 限制变更，也不是实测性能保证。业务服务限制更严时取更严值。容量不足保留已有记录。模型用量未知时必须显示未知，不能当作零；只有测量准备耗时和模型用量后才能声称成本改善。

运行诊断遵守[可观测性开发指南](../development/observability.md)：使用稳定错误码、安全的计数与耗时，不在日志里记录 prompt、回复、完整 URI、凭据或敏感路径。经用户授权的本地预览可以展示必要内容，但与运行遥测分开。日志失败不能改变业务结果或吞掉取消信号。

## 11. 实施与验收

| PR | 范围 | 依赖 | 必需证据 |
| --- | --- | --- | --- |
| 1 | Client 导出/资源、Host 接口、setup 检查 | RFC | 实际 tarball 在当前最新官方 DSH Web/Desktop 发行版安装，固定版本身份；验证本人本机模式、接口检查及升级/卸载/无 UI 行为 |
| 2 | 侧栏状态与诊断 | 1 | 未配置、连接中、成功、401/403、离线、过期、恢复和 Scope 切换；折叠栏及深浅色无障碍交互 |
| 3 | 直接嵌入现有 Dashboard，仅补必要兼容/认证适配 | 2 | 最新 DSH Web/Desktop 免密码与 token 登录、未开启指引、资源/导航/Scope/主题；保留原认证及确认，不暴露 token |
| 4 | 现有 PC Handoff 流程、当前工作输入、返回载体持久化/导出 | 1 | 由已有操作生成/校验/接续；无强制独立摘要器；有界输入、源 Agent 不执行无关写工具、无隐式 commit、重启和未知结果处理 |
| 5 | 三个目标适配、检测、经同意的 PC 安装、目录/Scope 预检 | 4 | Codex Desktop、Claude Desktop、Claude Code CLI 分别通过真实安装/就绪/打开/预填；缺应用停止、否取消、是安装后验证、失败/重启处理，不静默安装 Agent |
| 6 | 回复菜单、预检/安装弹窗及等待动画 | 2、4、5 | 真实 UI、最新边界、键盘/减少动态效果、阶段文字、无假进度、否/取消、去重、关闭恢复、五分钟等待超时 |
| 7 | 精确回执读取、恢复、资格验证和双语指南 | 4、5、6 | 真实接收模型调用 Continue/ACK；错误载体/身份被拒；重启恢复；不全量轮询历史 |

状态和 Dashboard 可以先于跨 Agent 交接发布。完整交付要求 PR 3、PR 7 及三个首期目标全部通过验收；Claude Code CLI 不是可有可无的附加项。源码构建或 test double 不等于发行版资格验证。每个场景记录 DSH、插件、PC Server、OS、目标应用的精确版本、包身份、访问模式和实际结果。不能从 macOS 推断 Windows/Linux；远程/共享 DSH 不在范围内。

每个 PR 自带针对性测试。按改动使用现有插件 `test`、`build`、`pack:release`、`test:e2e`、`test:e2e:runtime`，以及由 PC 仓库维护、针对未修改 DSH 的 UI 验收。setup 改动运行已有 DSH CLI/profile/transport 测试；Dashboard 和回执改动运行相关行为及授权测试。遵守仓库检查要求，双语文档运行 `make docs-test`。评审中若确需公开 API 改动，必须一起修改 `openapi/powercontext.yaml`、生成客户端及契约测试，不手改生成源码。

# Drawbacks

插件将承担更多打包、认证界面、本地存储和平台适配职责。Deep link 与 DSH 扩展行为需要持续验证版本。现有 Handoff 起草可能消耗模型时间并遗漏关键状态，因此可编辑降级和明确 omissions 不可省略。

本地文件传递受目标 sandbox 限制。精确回执关联依赖接收方配合和读取权限，Server Principal 不一定能识别应用或会话。本功能不支持远程/共享 DSH 部署。这些限制必须出现在产品和支持矩阵中。

# Rationale and alternatives

| 方案 | 结论 |
| --- | --- |
| 新增 DSH Electron IPC 或扩展其应用目录 | 不在交付范围内；使用现有 Node Host 插件机制 |
| 在模型 Markdown 中加入任意 deep link | 不属于可信产品动作，不能建立目录、载体和能力预检 |
| 只通过浏览器 deep link | 可作为后续便利入口，但不能解决 Host 文件位置、本机识别和远程载体传递 |
| 新建本地 companion daemon | 暂缓；在利用已有本机 Host 前先增加配对、安装、凭据和另一套生命周期不划算 |
| 所有目标都只复制 | 保留为降级，无法满足已验证本机目标的一键预填体验 |
| 通过 CLI/SDK 自动发送 | 属于不同的执行产品，需要另行处理权限、取消与成本 |
| 每次都 commit Handoff | 不采用；传输持久化不代表用户要保存里程碑 |
| 读取 latest Handoff 或最新 ACK | 本流程不采用；并行工作要求精确选择载体和回执 |
| 修改目标私有数据库或模拟键鼠 | 不采用；脆弱且可能影响用户会话和草稿 |
| 新增 Core Transfer 实体和通用 continuity 查询 API | 首期不需要；复用 Handoff 契约、插件记录与精确 Source 读取 |
| 强制新建 Dashboard/代理或独立摘要器 | 不需要；优先嵌入现有网页并使用已有 PC Handoff 操作 |

# Prior art

PowerContext 的 [Handoff RFC](0048_handoff_artifact.md)、[工作连续性 RFC](1223_human_agent_work_continuity.md) 和 [rollover RFC](1783-handoff-session-rollover.md) 定义业务边界。[DSH 集成](../docs/integrations/dsh.md)、[Dashboard 设计](../development/dashboard.md) 和 [Codex 交接指南](../docs/workflows/handoff-with-codex.md) 提供已有集成契约。

已检查的 DSH 源码具有外部 Client 发现、认证插件路由及 Node 打开应用能力。这些先例支持可行性，但不证明本插件已经具备发布级兼容性。

目标入口参考：[Codex 应用链接](https://learn.chatgpt.com/docs/reference/commands#deep-links)、[Claude Desktop 链接](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link) 和 [Claude CLI 链接](https://code.claude.com/docs/en/deep-links)。后续候选包括 [OpenCode Server/TUI API](https://opencode.ai/docs/server/) 和 [Pi 扩展](https://pi.dev/docs/latest/extensions)。资格验证必须重新核对所选版本的参数、协议注册、长度限制及草稿行为；存在官方入口不等于已完成端到端接收。

# Unresolved questions

以下产品决策已确定。剩余问题是具体实现和实际验收，不是重新改变范围的理由。

| ID | 已确定决策 | 尚需验证 | 门槛 |
| --- | --- | --- | --- |
| Q1 | 使用当前最新官方 DSH 发布版，已记录基准 `v0.2.2-rc.1` | 实施时再次核对/固定 latest，验证真实 Web/Desktop 包；历史 checkout 不代替验收 | PR 1 |
| Q2 | 只支持本人本机部署 | 验证用户/本机信号或明确本机配置，拒绝远程/共享/未知位置执行 | PR 1/5 |
| Q3 | 现有 Dashboard 可本机免登录，优先直接嵌入并保留已配置认证 | 验证免密码与 token 两种右栏路径，仅补必要认证/容器适配 | PR 3 |
| Q4 | 唯一交接机制为现有 PC Handoff | 验证当前工作输入、已有 generation/高阶操作和完整返回载体；不重复造摘要器、不执行无关项目动作 | PR 4 |
| Q5 | 目标启动前核对目录、Server 和 Scope | 验证各目标真正的启动绑定优先级，显式解决冲突 | PR 5 |
| Q6 | 用户数据目录保存，必要时导出至可读位置，不隐式提交里程碑 | 验证三个真实目标环境均能读取交接包 | PR 5 |
| Q7 | 等待有动画、有界；保留待接收/固定载体 | 验证阶段、减少动态效果、超时、保留和容量规则，测量初始预算 | PR 4/6/7 |
| Q8 | 以 PC 内容及规范摘要为准 | 验证跨语言保留和精确回执匹配，覆盖 null/数字/Unicode | PR 4/7 |
| Q9 | 接收确认表示目标通过 PC 回报已读取本次 Handoff；先检测 Agent 和 PC 安装 | 验证缺 Agent 停止、安装是/否、各目标安装/就绪、精确 ACK；没有证据就不识别具体会话 | PR 5/7 |
| Q10 | 首期同时完成 Codex Desktop、Claude Desktop、Claude Code CLI | 三个都验收；核对仅 Desktop 时的安装路径、CLI 协议注册和不自动发送 | PR 5/7 |

尚需实测的是最新 DSH 的内嵌行为、仅安装 Claude Desktop 时受支持的 PC 安装路径、各目标有效启动配置与文件访问，以及确有必要的窄范围认证适配。实验失败时对应验收项保持未完成，不能因此修改 DSH、静默加装另一个 Agent、降低认证或从首期移除 Claude CLI。复制/导出和外部浏览器是如实提供的恢复方式，不代替承诺的自动交接/内嵌验收。

# Future possibilities

逐项增加通过验证的 Pi/OpenCode 等 Agent 适配、DSH 内部接续、Windows/Linux、远程目录映射和认证载体传递，以及可选的接收会话身份。自动发送、受管多 Agent 执行，以及临时交接关联 Outcome 的扩展需要独立设计，均不是接受本提案本机预填与精确 ACK 边界的前提。

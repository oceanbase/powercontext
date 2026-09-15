---
title: "RFC 1455：桌面控制中心"
description: "采用 Tauri 构建桌面客户端，明确 API、身份、安装和投递边界。"
---

- 提案名称：`desktop_control_center`
- 开始日期：2026-09-04
- RFC PR：[oceanbase/powercontext#1455](https://github.com/oceanbase/powercontext/pull/1455)
- Tracking Issue：[oceanbase/powercontext#1428](https://github.com/oceanbase/powercontext/issues/1428)
- 状态：提案

# 概要

采用 **Tauri 2、由 Vite 构建并打包的可信 React + TypeScript 客户端 UI、现有独立 Python Server**
构建 **PowerContext Desktop**。
桌面通过公开契约管理连接、本地安装与服务健康、Scope、资产、Review 和受支持的 Handoff 流程。
Rust 负责受限原生能力和携带凭据的传输；Server 负责业务语义、授权、持久化和持久处理。

复用个人 Dashboard 的品牌资源、展示规则、翻译和适合共享的展示组件。它的 Jinja/HTMX 页面不是可直接移植的
桌面应用。桌面有独立的客户端管理入口，不以共享整套 Web 管理应用为前提。

建议首个完成正式验收的平台为 **Windows 11 x64 + SQLite**。可以先交付“连接已有 Server”的预览，再完成受管
安装；授权资源浏览可以先于持久 Handoff 投递。完成 #1428 需要一个平台上的完整安装包流程，接受 RFC 或发布
预览本身不关闭该 issue。

# 动机与用户流程

用户应能在一个应用里确认 PowerContext 是否安装、Agent 是否使用正确 Scope、哪些内容需要审核、Handoff 发到
哪里，以及升级失败后如何恢复。原生凭据、文件选择、服务检查、托盘和通知构成桌面的价值。关闭桌面后，独立
Server 仍须继续服务 Agent。

产品面向新个人用户、连接已有安装的用户，以及认证团队 Server 的用户。它不是聊天客户端、IDE、Agent Runtime、
编排器或数据库副本，也不是 CLI/SDK/MCP 的前提。它不执行下载的 Skill，也不自动启动 Agent 任务。

1. 选择“这台电脑”或“远程 Server”。仅连接远程不需要本地 Python。受管本地安装先展示安装器计划，包括不可变
   发行版本、组件、所选宿主、位置和恢复方式，由用户确认。
2. 通过受支持接口连接或安装。受管安装尚不可用时，明确说明“仅连接”的范围并提供安装指引，不放置无效安装按钮。
3. 分别检查身份、授权模式、就绪和能力。不配置模型也可在同一精确 Scope 显式保存一条小型 Memory，并全文召回。
4. 单独检查所选 Agent：“已安装”“观察到宿主加载”“实际 capture/recall 通过”是不同事实。未观察的检查保留为
   未验证；依赖模型的功能说明前置条件。
5. 浏览资产、审核受支持 Candidate。投递能力具备后，从收件箱打开精确 Handoff。接收 Source 不代表提取已完成。
6. 找到脱敏诊断、本地数据位置、恢复，以及分别命名的桌面/服务移除操作。

| 页面 | 首版行为 |
| --- | --- |
| 总览 | 当前连接/身份、就绪、本地服务事实、可用能力、覆盖范围内的待办和恢复 |
| 项目与工作流 | Scope 目录与组织关系；观察选择与精确写入/绑定目标分开 |
| Memory 与资产 | 支持的目录、搜索、精确详情、来源/历史；显式 Memory 保存、修订、退役 |
| Review | Experience、Skill、Profile 的类型化详情与批准/拒绝/修订，保留版本检查 |
| Handoff | 已提交精确详情、授权共享、只读报告，以及单独设门槛的投递收件箱 |
| Sources 与集成 | 确认后的文本导入、Source 发现、Agent 声明能力与实际诊断 |
| 设置与诊断 | 连接、原生凭据引用、语言、通知范围、版本、升级和脱敏导出 |

通过受支持契约读取 Topic Memory 和 Profile。首版不包含 Prompt 编辑、Dream 管理、连接器市场、Handoff 编辑器
或任意 Skill 执行。未知类型不能获得通用编辑/批准能力。已有进程可以只连接而不接管；本地控制必须核验归属。

有托盘时，关闭最后一个窗口隐藏应用；“退出”结束应用。没有托盘时，关闭最后一个窗口退出，并明确说明该行为。
两者都不停止独立 Server。桌面登录启动与 Server 登录启动分别设置。通知需要桌面运行；Server 持久工作和已实现
的收件箱在退出后仍保留。远程离线时不排队写入。

# 技术设计

## 1. 当前基线与负责方依赖

源码基线为 2026-09-13 核验的上游 `master`：
[`62e4c821709c18b832c77363fdd428765bee6a96`](https://github.com/oceanbase/powercontext/commit/62e4c821709c18b832c77363fdd428765bee6a96)。
PowerContext 1.0.0 已发布，但源码基线中的能力不自动等于每个发行版本都具备。每次桌面发行都要固定并验收所支持
的 Server 与 Agent 发行物。

| 已有部分 | 可复用实现 | 剩余边界 |
| --- | --- | --- |
| 公开 API | Scope/Source 发现、通用 Artifact/revision 读取、Memory、Review、精确 Handoff 和 Access API | 尚无兼容握手或持久投递收件箱；Memory entry/history 缺分页 |
| 个人 Dashboard | 默认关闭的静态 token 查看器；Jinja2/HTMX/Tabler/Surreal 和 Python ASGI API 传输 | 不是共享管理 SPA；注入团队 Provider 的部署保持关闭 |
| 授权 | RFC 1396 与 [#1398](https://github.com/oceanbase/powercontext/pull/1398) 已实现；身份、检查、资源、绑定和审计 | 静态 Bearer 是一个共享服务身份；桌面认证传输需要验收 |
| 个人服务 | 安装、状态 JSON、卸载、Windows 登录启动选择 | 安装/卸载缺结构化结果选项；无公开 start/stop/restart |
| 配置与 Agent | 引导配置、受保护环境、按 URL 绑定的宿主凭据、结构化诊断 | 交互向导不是桌面机器接口 |
| 资产与处理 | Profile、Topic Memory、Prompt、标签、Dream 和处理监督器 | 各 family 写入规则与维护迁移仍是权威 |
| 分发 | 版本化 Python 发行和维护中的集成清单 | 统一安装器仍是依赖工作；当前 Windows 支持为 experimental |

以下是向原负责方提出的要求，**不是已经实现的接口**。每项负责方要在消费者交付前确定 schema、负责人和一致性
测试；缺失的依赖只阻塞表中对应能力。

| ID | 负责方与相关工作 | 所需契约 | 门槛 |
| --- | --- | --- | --- |
| D1 | Server/API | `server-info`、部署身份生命周期、明确兼容配置 | P1/P2 中依赖兼容性的控制 |
| D2 | Server/Memory | 授权且有界的 entry 列表；历史 UI 交付前的有界 change/history 查询 | P2 完整 Memory 浏览 |
| D3 | 服务/配置，[RFC 1299](1299_local_server_availability_and_service_installation.md) | 非交互结构化修改、受保护输入、归属和恢复 | P3 受管服务/配置修改 |
| D4 | 安装器 [#1406](https://github.com/oceanbase/powercontext/issues/1406)、RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | 核验 bootstrap、计划、锁、持久操作/状态与恢复 | P3 受管安装/升级 |
| D5 | 分发 [#1405](https://github.com/oceanbase/powercontext/issues/1405)、RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | 不可变宿主发行物、兼容性和宿主负责的安装适配器 | P3 所选宿主安装 |
| D6 | 投递 [#1419](https://github.com/oceanbase/powercontext/issues/1419) | 接收方关联、envelope、持久收件箱、精确引用、去重和恢复 | P4 投递消费 |
| D7 | 桌面/发行维护者 | 具名负责人、Windows/宿主验收、支持版本和实测预算 | P0 退出与 P5 发行 |
| D8 | Server/连接器负责方 | 若提供管理功能，需公开的连接器发现、健康与管理操作 | 仅对应的连接器控制 |

本基线下 D4/D5 RFC 和 D6 Tracking Issue 仍开放。已有授权无需等待 D6，应在各消费阶段验收。
Scope 集成绑定与 Access 角色绑定是分别命名的概念。

## 2. 组件与 UI 共享

```text
打包的桌面 UI -> 类型化 Rust IPC -> 公开 Server HTTP API
                       |
                       +-> 系统凭据、托盘、通知、文件句柄
                       +-> 安装器/服务/配置机器接口
操作系统服务管理器 -> 独立 Python Server -> 持久化与持久处理
安装器             -> 已核验 runtime/Agent 发行物和安装日志
个人 Dashboard     -> 自身的服务端渲染页面 -> 公开 API 授权
```

新增 `desktop/`，原生宿主位于 `desktop/src-tauri/`，`desktop/ui/` 使用 **React + TypeScript + Vite**。
React 组织页面、可复用组件，以及配置表单、审核、安装进度和连接切换的交互状态。TypeScript 检查前端和
API/IPC 类型；公开操作 schema 仍从 OpenAPI 派生。Vite 提供开发服务器，并构建随 Tauri 应用打包的静态
HTML/CSS/JavaScript。安装后的前端不需要 Node.js 服务、SSR 或 Next.js runtime；Python 未安装时，安装和
恢复界面仍可使用。

桌面构建独立于文档网站。采用 React 不会自动迁移 Jinja/HTMX 页面，也不要求重写 Dashboard。状态和请求处理
仍须保证连接/身份隔离，React 不替代原生校验或 Server 授权。业务规则保留在 Server，安装和服务逻辑保留在
原负责层。前端依赖和渲染成本纳入 P0 预算验收。

初期共享品牌资源、设计规则、翻译和适合复用的展示组件。共享代码必须有唯一源、确定的构建/复制和漂移检查；提取
PR 说明文件与许可证。服务端模板/静态资源仍位于 `src/powercontext/server/dashboard/`，随 Python wheel
分发。用户安装 Python 包或运行 Dashboard 不需要 Node、Rust 或本地桌面构建。

不复制运行期 Jinja、Python `DashboardAPI`、HTMX `/dashboard/*` 导航、cookie 登录或内联脚本到桌面。
改由客户端渲染和类型化 API 操作完成。安装/恢复页在没有 Python 和 Server 时可用。未来若共享完整 Web 管理
客户端，需要与 [#1341](https://github.com/oceanbase/powercontext/issues/1341) 协调；本 RFC 不扩大个人
Dashboard 的阅读定位，也不在团队 Provider 部署中启用它。

## 3. 公开操作矩阵与有界浏览

桌面不导入 Runtime 对象、不打开数据库、不抓取 HTML、不消费 Dashboard 私有路由。
从 `openapi/powercontext.yaml` 生成或校验 operation ID、schema、路径编码与响应类型，避免分别手工维护
Rust/JavaScript 路由目录。契约变更交付前运行 `make api-generate` 和 `make contract-test`。
下表权限列指出相关检查，不替代完整 Server 策略；组合证据、目标、发布和当前状态检查仍须执行。

| UI 行为 | 已有 operation ID | 授权/一致性 | 阶段或缺口 |
| --- | --- | --- | --- |
| 连接 | `get_liveness`、`get_readiness`、`get_capabilities`、`get_access_principal` | 健康不等于身份；受保护调用遵守当前策略 | P1；D1 握手 |
| Scope 目录 | `list_scopes`、`get_scope`、`get_default_scope`、`resolve_scope_selection` | 授权发现、不透明 cursor 和实际选择语义 | P2 |
| Scope/绑定修改 | `create_scope`、`update_scope`、`set_scope_binding`、`clear_scope_binding`、`resolve_scope_binding` | 创建/admin 检查、已定义的预期版本、精确目标 | P2；不承诺全局绑定列表 |
| Memory | `remember_memory`、`search_memory`、`list_memory_entries`、`get_memory_entry`、`revise_memory_entry`、`retire_memory_entry`、`list_memory_changes` | 相应 Scope/资源检查、精确 citation | P1 保存/搜索；D2 完整浏览/历史 |
| Source | `list_sources`、`get_source`、`capture_content_source` | 授权 Scope/Source 访问、分页、不可变 capture 身份 | P2 |
| Artifact 浏览 | `list_artifacts`、`get_artifact`、`get_artifact_revision`、`list_artifact_revisions` | Family 策略、精确 revision、ETag、不透明分页 | P2 |
| Topic Memory 详情/搜索 | `get_topic_memory`、`search_topic_memory` | 专用选择/搜索限制和当前 family 策略 | P2 只读；不手工写入/flush |
| 标签 | `get_artifact_tags`、`replace_artifact_tags`、`get_memory_entry_tags`、`replace_memory_entry_tags`、`query_artifact_tags` | 受支持可打标签 family、精确逻辑目标、必需的标签状态 `If-Match` | P2；标签不改变内容 revision |
| Review | `list_artifact_candidates`、`get_artifact_candidate`、`approve_artifact_candidate`、`reject_artifact_candidate`、`revise_artifact_candidate` | 读取/审核和 proposal/证据检查；`expected_version` | P2；列表要求精确 Scope |
| Skill 生命周期/包 | `list_managed_skills`、`update_skill_lifecycle`、`get_skill_package_manifest`、`download_skill_package` | 资源检查、`expected_generation`、精确已审包 | P2；不执行下载代码 |
| 发布 | `publish_artifact`、`publish_remote_skill` | 共享/目标管理与发布策略；精确引用/generation | P2，仅已验收目标 |
| 共享 | `get_access_principal`、`check_access`、`list_access_resources` | 当前 Principal、安全过滤、精确共享单位 | P2；不含角色管理 UI |
| Handoff | `get_handoff_report`、`continue_handoff`、`acknowledge_handoff`、`record_task_outcome` | 区分报告与精确证据/receipt 权限；接收方观察 | P2 读取；P4 受支持接收动作 |
| 统计 | `get_stats` | 授权投影和支持的选择；缺失不是零 | P2 |
| 本地管理 | 负责层机器接口；`service status --json`、`doctor integrations --json` | 核验本地归属、受保护配置 | P1 读取；P3 修改 |

当前 Memory entry 列表返回完整集合，没有 cursor/limit；changes 也缺分页。客户端分页或限制响应大小不能解决
这个问题。D2 明确服务端限制/过滤、稳定排序、cursor 过期/快照、并发 revision 和授权。Artifact 分页不能为
Memory Artifact 内部的 entry 分页。D2 前提供有界搜索和精确详情；若提供小数据目录，必须说明上限并报告限制，
不能截断或伪造总数。历史页等待有界接口。

不通过 Candidate 历史反推资产，不把所有通用列表解释为时间排序。Scope 绑定页解析已知宿主绑定，不虚构全局
注册表。搜索结果上限和支持模式应明确呈现。

## 4. 握手与兼容性

D1 提议增加受认证保护的 `GET /v1/server-info`，包含 `schema_version`、`product`、持久不透明 `server_id`、
`package_version`、`api_contract_version`、`feature_contracts`。协议版本使用明确的 major/minor：major
改变必需语义，minor 增加兼容的可选字段/能力。桌面只接受支持的 major 和所需最低 minor，忽略未知可选字段，
只开启已测试的操作组。能力名称与精确 OpenAPI 类型由 D1 确定；本 RFC 本身不增加端点。

`server-info` 说明部署/协议身份；`access/me` 提供 Principal、模式、Provider/family 访问能力；
`capabilities` 提供运行期功能。每项操作同时要求桌面支持、契约兼容、运行能力可用和当前授权。解释失败条件，
不连带禁用独立功能。

| 结果 | 行为 |
| --- | --- |
| 握手受支持 | 校验 product/schema 与操作组兼容性 |
| 握手 404 | 仅使用用户明确选择、随桌面发布且测试过的旧版兼容配置，否则只提供诊断 |
| 401 | 停止受保护重试，请求有效凭据；不能推断过期 |
| 403 | 解释拒绝；不降级匿名访问或换端点 |
| 503 / 认证服务不可用 | 显示故障、有界重试；保留凭据/身份选择 |
| 未知必需 major/product/feature | 阻止相关操作，解释兼容版本要求 |
| 可选能力缺失 | 独立受支持功能仍可用 |
| Server 身份变化 | 使待执行上下文/选择失效，要求明确重新连接 |

旧版兼容配置记录测试过的 tag/commit、schema 发行物和操作。1.0.0 是初始验收候选，不代表兼容当前主线所有
能力。用户选择版本和成功探测都不能证明远程二进制身份。不猜测支持、不执行未测试修改、不自动升级；旧版连接
重连时不复用持久通知游标。

提议的 `server_id` 识别逻辑部署：重启、受支持升级和恢复同一部署时保留；克隆为另一部署时，在服务客户端前
生成新 ID。同一部署的副本共享该身份。D1 定义持久化、备份/恢复和克隆初始化。它不是 Access 中可配置的
`deployment_id`，也不是信任证明。TLS、凭据和核验后的本地归属仍是信任依据。握手不暴露路径、秘密和未授权清单。

## 5. 传输、连接隔离与 IPC

连接配置持久化不透明 ID、名称、规范化端点/base path、模式、凭据引用、TLS 设置和已观察的兼容信息。
一个窗口只有一个活动连接；每个系统用户/通道一个实例，通过当前用户原生 IPC 激活，不增加 HTTP 管理监听。

更改连接、端点、TLS 信任、凭据或观察到的 Principal 时，推进原生侧拥有的 generation。取消读取、清除私有
视图/游标，并在必要的丢弃确认后清除易失草稿；拒绝迟到结果。已提交写入仍绑定原端点、身份、Scope、引用和
generation。选择变化不改变目标。修改端点解除旧凭据引用，并要求为新目标明确配置凭据。

首版远程仅支持 HTTPS。Loopback HTTP 遵守 `tests/fixtures/transport_loopback_vectors.json`。
当前 CLI/SDK 的非 loopback 明文 HTTP 同意机制不在桌面首版范围内；导入此类配置时说明限制。拒绝 URL 中的
userinfo/query/fragment、路径前缀逃逸和认证请求重定向。验证 TLS 主机名/证书；自定义 CA 必须显式绑定连接，
不能关闭验证。保留受支持 API base path，通过生成规则编码每个路径段。

首版 API 直连，不继承 shell 代理变量或系统代理凭据。需要代理的部署等待显式、绑定连接的适配器验收；连接设置
说明此限制。

| 桥接能力 | 允许数据 | 原生侧检查 |
| --- | --- | --- |
| 连接/凭据 | 连接选择、只写替换、安全事实 | 可信主窗口/设置窗口、原生 generation、无秘密读回 |
| API 读取 | 允许的 operation、类型化参数/结果 | 兼容契约、精确连接、取消和响应限制 |
| API 修改 | 允许的 operation、类型化载荷、预期版本和显式动作上下文 | 原连接/Scope/引用；不接受任意 URL/header 权限 |
| 导入/包导出 | 系统选择的文件或一次性保存句柄、有界进度 | 不接受渲染层路径；字节/摘要验证；不执行 |
| 本地管理 | 负责方确认的计划或支持的命令参数 | 核验归属、机器协议、确认绑定该精确计划 |
| 通知/诊断 | 批准的元数据、不透明导航句柄、脱敏模型 | 无原始错误、秘密、shell、数据库或无限制文件系统 |

同时限制应用自定义命令和插件权限。Tauri 对 `invoke_handler` 命令的默认行为不是全部拒绝，重叠 capability
会合并权限。显式列出窗口/命令，并测试未授权窗口调用。不暴露通用 fetch、shell、进程终止、SQL 或原始文件接口。

只有可信打包文档获得 capability。不允许特权远程导航/脚本。严格 CSP 和安全文本/Markdown 渲染拒绝可执行 HTML
和远程图片。外部 HTTP(S) 链接仅在用户操作后由系统浏览器打开；其他 scheme 要单独验收允许列表。不能为了复制
Dashboard 内联脚本而放宽 CSP。Server 文本、导入和更新说明均是不可信数据。

初始传输预算为连接 10 秒、普通读取 30 秒、单个解码 JSON 响应 8 MiB，Server 更低限制优先。逐操作例外在实现前
定义。受支持二进制包在原生侧流式处理，单独声明导出上限并验证摘要，不经无限制 JSON/base64。长修改遵守各自
契约，不全局重放。错误只返回安全类别/代码/request ID，不直接返回任意响应正文或 CLI stdout/stderr。

## 6. 服务、安装与配置契约

复用唯一用户级服务：Windows Task Scheduler、macOS LaunchAgent、受支持 Linux 上的 systemd user service。
不创建 root/SYSTEM 服务、竞争的桌面监管器，不接管归属未知的存活进程。本地服务配置保持 loopback。

分别保留 `support`、`registration`、`definition`、`manager_ownership`、`manager`、`server_liveness`、
`endpoint`、`log_location`、`recovery_action`。非零状态命令退出可以包含合法的不健康 JSON。未知/外部归属、
过期环境身份、端口占用分别提供受支持恢复方式。

当前安装/卸载面向人类输出，Windows install 未提供登录启动选择时可能询问。它们尚不是 D3 机器协议。
start/stop/restart 等待服务负责方支持；卸载不是停止。D3/D4 要求：

- 版本化请求/结果、稳定错误、受支持入口和非交互执行。配置校验/应用复用现有规则，并通过服务校准处理受保护
  env-file 身份。
- 秘密通过受保护文件或继承的私有输入传递，不进入命令行参数和普通输出。不解析交互向导，也不在 Rust 复制环境
  与宿主配置合并规则。
- resolve/preflight 不修改安装，计划含不可变组件身份、路径、归属、兼容、服务变更与恢复。应用前重新核验；
  确认绑定精确计划，不能授权后来替换的计划。
- 负责方维护 operation ID、进度、取消边界、锁、持久 journal 和状态查询。逐组件报告
  unsupported/skipped/current/installed/stale/failed/uncertain；重试前核验不确定状态。
- 客户端退出后可恢复，不承诺无关宿主之间的全局原子回滚。

CLI 和桌面共用负责方的锁。安装完成不代表宿主加载或健康。只有负责方支持持久执行时，退出后才继续安装；否则
保留操作窗口，只在安全边界取消。强制终止通过 journal 恢复。稳态 Server 始终独立运行。

## 7. 凭据与认证模式

采用 Windows Credential Manager；后续平台分别验收 Keychain/Secret Service。偏好只保存不透明引用。
凭据库不可用/锁定时提供解锁或仅本次会话使用，不回退明文。可另行支持加密 vault，但 Stronghold 本身不是系统
凭据库适配器。凭据可短暂存在可信输入/只写 IPC，提交或取消后清空；不持久化到渲染层存储、URL、参数、日志、
导出、崩溃报告或通知。

| 模式 | 行为 |
| --- | --- |
| 已有未认证 loopback | 明确显示本地访问未受保护，不静默重配 |
| enforced 静态 Bearer | 显示共享服务身份，不表现成多个团队成员 |
| enforced 注入 Provider | 支持运维方发放且该 Provider 接受的 Bearer；身份和检查来自 Server |
| 不支持的登录传输 | 说明不支持；不嵌入远程登录、不抓 token、不匿名降级 |

首版不含浏览器 SSO/OAuth、cookie 会话和交互企业登录。实际验收 Provider 身份解析，不能仅凭 `multi_principal`
判断登录支持。每项操作仍由 Server 授权。通用 `401 unauthorized` 表示需要有效凭据，不一定过期；只有支持的
契约给出过期原因时才显示过期。区分 `403` 拒绝和 `503 authentication_unavailable`；认证拒绝后停止受保护循环。

新托管本地安装默认启用强制认证；无认证连接只作为明确选择的现有安装模式。配置负责方在受保护 Server 环境中
生成凭据。用户确认安装后，安全机器接口为桌面凭据库和所选宿主
配置凭据，复用现有按 URL 绑定的 authorization 适配器，不通过页面数据返回秘密。Server/Agent 不依赖桌面运行
或桌面 vault 解锁。

轮换由负责方计划协调：核验归属/消费者、暂存受保护配置、校准服务、替换桌面与所选宿主凭据、逐个验证。
静态 Bearer 不假设新旧 token 同时有效，需说明中断和部分失败。按负责方状态恢复，不能用一个客户端重连成功推断
全部成功。删除连接只移除自己的引用/不再使用的凭据条目，不删除 Server/Agent 环境文件。

## 8. 授权、Scope 与资产

使用 `access/me` 和受支持检查解释 UI，再对真实请求重新授权。Agent 名称、`receiver`、标签和渲染层输入不建立
身份。分页/计数前过滤；不支持安全查询时失败，不无限制回退。缓存/游标键包含端点、Principal/凭据 generation
和查询条件；预检不是持久授权。

Scope ID 不透明，不能用路径/分支/会话 ID 替代。父子组织关系不继承权限、不共享 Context、不发布 Artifact。
只显示获准的祖先信息。`all/subtree/exact` 是观察选择，不是所有列表都支持的模式。页面使用 API 支持的选择，
写入/绑定采用明确展示的精确 Scope。

| 类型 | 首版处理 | 写入边界 |
| --- | --- | --- |
| Memory | 搜索/详情/citation；D2 有界目录/历史 | 专用 remember/revise/retire、原 citation 和冲突检查 |
| Experience/Skill | 通用目录、类型化精确详情、来源/生命周期 | 已审核 Candidate 流程和验收过的发布 |
| Profile | 授权读取、类型化 Profile Candidate Review | 保留 proposal/policy/证据要求，不通用绕过 Review |
| Topic Memory | 已发布通用目录/revision、支持的专用详情/搜索 | 不提供手工通用 create/replace/delete |
| Handoff | 已提交精确 revision 和单独授权的证据 | Continue、receipt、outcome 语义分开 |
| Prompt/Dream/未知 | 不提供专门管理；仅安全且受支持的元数据/详情 | 不为未支持类型通用编辑/批准/执行 |

标签遵守当前 taggable-family 契约。通用可读不代表可写。未知 Candidate 保留类型和身份，但在具备类型化展示/
校验器前禁用审核；部分表单不能丢弃未知字段。Source 待处理、Candidate、已提交 Artifact、已发布包和退役项
分别呈现。

Review 发送 `expected_version`，拒绝必须填写契约要求的非空理由，修订保留证据及省略字段的语义。
冲突后重读而不静默批准新版本。Skill 生命周期使用 `expected_generation`，
包使用精确已审引用。精确 Handoff 授权不允许 latest/相邻版本、宽泛报告、无关搜索或未授权证据。
即使没有所在 Scope 的目录权限，也应能打开合法的精确共享项。

## 9. Handoff 发现与接收身份

| 视图 | 权威来源 | 含义 |
| --- | --- | --- |
| 报告 | 已有报告 API | 授权的只读选择投影 |
| 与我共享 | Access 资源发现 | 精确可访问身份，不是未读/投递状态 |
| 投递收件箱 | D6 | 持久投递记录和受支持接收恢复 |

Prepared Handoff 不是可枚举的持久收件箱。Access 分页、Review 和远程 Skill receiver API 不能替代投递。
D6 前只提供报告/共享资源视图。

桌面是**当前 Principal 有权管理的接收目标的观察/控制界面**，不会自动成为 Agent 接收端。本地安装 Agent 不等于
有权注册或冒充它。D6 必须定义可信 Principal 与 target 关联、注册/发现、envelope 版本、不可变引用、去重 ID、
列表/恢复游标、过期/取消、可重试/终态和安全诊断。D6 还需定义已读属于 Principal 还是 target；设备本地通知去重
不能修改 Server 已读。桌面不建立第二套注册表、envelope 协议或重试调度器。

打开 envelope 时，重新检查原精确引用的权限与投递状态。缺失/过期/取消/撤销不显示缓存正文，不回退宽泛报告。
查看、支持时的已读、投递、授权、accepted receipt 和 Task Outcome 分别命名。

`accepted`、`needs_clarification`、`declined` 保留现有语义。接受需要真实接收端 live-state、能力、授权和
证据观察；浏览页面不能证明另一 Agent 的环境。只有提供这些检查的验收流程才允许确认。使用声明过的精确项宿主
启动机制，否则提供受支持复制/打开，URL 不携带 token/正文。D6/D7 验收具名 sender/receiver 及其版本。

## 10. 通知与后台限制

通知是尽力提示，不是队列或 exactly-once 保证。Server Candidate 状态和已实现收件箱是权威。
初期仅监控活动连接的**当前精确 Scope** 的 Review；all/subtree 浏览不订阅全部 Scope。托盘隐藏期间保留该 Scope，
并明确展示覆盖范围。

Review 初始限制：一次一个请求，每页最多 100 项，每分钟最多五个分页请求，轮询间隔 60 秒、最多 20% 抖动。
继续有界分页，不把列表 cursor 当事件 cursor。五分钟内无法完成的遍历丢弃为过期，刷新并报告部分覆盖。
完整全局计数/历史需要另一个 Server 契约；后台限制不阻止用户手动分页审核。

首次启用或范围改变只展示摘要，不为每个历史 Candidate 发通知。后续完整遍历按连接/Principal/Scope/Candidate
ID/version 去重；待审版本变化可以产生一次合并提示。部分扫描使用“已发现待办”，不能声称完整总数或把未读页
算作零。401 停止；临时错误退避最多 15 分钟，Server 要求更长等待时遵从。手动重试不能产生无限后台循环。

D6 投递使用单独的有界消费者，P4 交付前确定请求预算。只持久化不透明游标和去重/导航元数据，初始每连接最多
1,000 条、保留七天。过期句柄拒绝；身份改变清除私有元数据，尽可能移除已发系统通知，残留通用提示不提供权限。

使用“PowerContext 有事项需要处理”这样的通用提示和本地不透明导航句柄，不显示正文、敏感标题、路径、token
或原始错误，包括锁屏。点击后明确恢复对应连接并重新授权精确项；伪造句柄不能静默换凭据或修改状态。
解释并请求通知许可，拒绝后提供应用内回退，抑制重复离线错误。退出停止通知，重开刷新 Server 状态。
验收真实安装通知/冷启动激活，包括应用退出后点击已有提示。

## 11. Source 导入、连接器与 Agent 诊断

首版通过 `capture_content_source`（`POST /v1/sources/content`）导入输入文本或单个所选 UTF-8 文件。
不与分配新身份的通用 `create_source` 混用。读取系统选定句柄，不接受渲染层路径；防止替换/链接竞态、路径穿越
和未请求的目录扫描。

所有平台使用同一导入规则：

1. 超过 1 MiB 的文件在超过读取预算前拒绝。严格 UTF-8 解码，移除一个开头 BOM，保留其余 Unicode 码点、换行和
   空白。拒绝非法编码和空白内容。
2. 正文最多 200,000 个 Unicode 码点，并遵守更低的已声明传输/Server 上限，不截断。
3. 对提交文本的 UTF-8 字节做 SHA-256。`source_id` 为 `desktop-text-v1:<小写十六进制摘要>`，metadata 固定为
   `{"importer":"powercontext-desktop-text-v1"}`。载荷不包含文件名/路径/时间/设备/用户元数据。确认页可显示
   本地文件名，但不上传它。
4. 确认端点、精确 Scope、规范化后大小和去重规则。同一 Scope 中相同提交文本使用同一 capture，内容变化生成
   另一 Source。改名不重复导入相同内容，不同 Scope 相互独立。这是快照导入，不是文件同步。
5. 重试同一确认导入时复用相同身份/内容/metadata。已有载荷冲突是真实错误，不覆盖，也不静默换随机 ID。
   原生侧恢复读取用于核验提交文本。

不建立持久本地正文队列。重启后重新选择同一文件可复现身份，并在当前授权下检查/重试。远程导入传输批准的
字节，不把本地路径当作远程路径。Capture 成功不代表提取完成。

Source 定义/观察/checkpoint 不提供连接器管理。D8 前只展示已支持事实，不提供猜测的启动/重试按钮。已接收
任务、凭据和 checkpoint 属于 Server/连接器 worker；不完整抓取不代表删除，桌面退出不能取消已接收持久工作。

分别展示版本化 `integrations/capabilities.toml`、安装记录和 `doctor integrations --json`：声明能力、
已安装归属/版本、观察到的加载/连接/Scope/capture/recall，仅在真实提供时显示注册。元数据不是实时目标注册表。
宿主安装由用户选择，适配器负责合并/修复。Rust 不重写所有检测到的配置，也不通过文件/工具数量推断健康。

## 12. 并发写入、取消与未知结果

没有持久业务缓存或离线写队列。远程离线隐藏私有内容，未提交表单可在内存中保留为明确未保存输入，直到策略或
确认后的身份变化将其丢弃。重连刷新兼容、身份、授权和资源。本地离线可用不代表能离线生成。

| 修改 | 保护条件 | 响应丢失后的恢复 |
| --- | --- | --- |
| Candidate 批准/拒绝/修订 | ID 与 `expected_version` | 读取 Candidate/result；状态改变不证明由本客户端完成，不自动批准新版本 |
| Memory 保存/修订/退役 | 对应操作支持的预期 revision/精确 citation | 读取精确/当前 entry；可用时读取有界历史，归因不确定不等于允许重放 |
| 文本导入 | 稳定 ID 与完全相同载荷 | 读取/比对精确 Source，或在当前授权下明确重复相同幂等 capture |
| 标签替换 | 精确逻辑目标、必需的标签状态 `If-Match` | 读取当前标签集；冲突需要用户重新决定 |
| Skill 生命周期/发布 | 精确引用与所需 generation | 读取生命周期/目标状态；无法归因时保留不确定性 |
| Handoff receipt/outcome | 精确 revision、接收方观察、已接受 receipt 身份 | 负责方支持的读取/幂等路径，否则显示未知并转受支持接收恢复 |
| 安装/配置/升级 | 已确认计划与负责方 operation ID | 读取持久状态、核验不确定组件、从受支持边界恢复 |

不虚构 operation-status 端点或幂等键。待处理期间禁用重复提交。取消读取可丢弃响应；取消等待已提交修改不代表
取消修改。重试权限不转移到另一连接/Scope/revision。冲突时保留易失意图，但不自动应用。

## 13. 分发、升级与迁移

采用签名用户级 Windows 包。受管本地安装不需要预装 Python/Node/Rust/Git/编译器；D4 提供核验的版本化 Python，
D5 提供所选宿主。当前服务在 Windows 需要真实 Python 与相邻 `pythonw.exe`。冻结二进制需另行验收；Tauri
sidecar 不拥有 Server 生命周期。

不可变发行计划记录桌面/解释器/runtime/API/features/Agent 版本、OS/架构和数据兼容。可信发布者/来源固定在
渲染层控制之外，区分系统签名、Tauri updater 签名与 runtime/Agent manifest 信任。不可信包旁的 checksum
不是身份认证。发行前定义密钥轮换/撤销和 CI 归属；离线包说明剩余网络需求。

Stable 为默认通道。预览通道可以作为连接客户端共存，但一个本地安装只能由一个记录在案的通道管理。转移管理权
需要显式、兼容的负责方计划。两者可连接同一用户级服务，不创建竞争注册/SQLite 监管器。偏好和凭据引用按通道
归属，卸载一个通道保留另一通道或 Agent 仍引用的组件。

升级采用明确安装器计划：检查空间/归属/兼容，暂存并核验包，说明中断/数据恢复，通过支持的服务操作切换，再
检查就绪和所选宿主。不自动升级远程，不承诺全局原子回滚；仅在数据仍兼容时恢复二进制/配置。

受影响数据库已要求 `server processing-migrate --action plan/apply/verify`：停止旧 worker 及自动重启，
暂停写入/触发，使用相同 migration ID 恢复，验证 `ready: true` 后才恢复流量。采用负责方安全维护流程，或明确
说明手动维护；反复重启不是迁移。采用后端支持的备份，不复制运行中的 SQLite 文件。不可逆变更需要受支持恢复或
清晰的向前修复方案并经确认，不让不兼容旧 runtime 打开已迁移数据。

桌面自身升级与 runtime 升级分别处理。Windows Tauri updater 安装前退出应用，应先持久化负责方操作/检查点与
恢复入口，不能由 UI 内存持有未完成协调工作。不支持跨退出恢复时，先完成或安全推迟 runtime 操作，再更新桌面。
下载/签名失败保留原可用状态，逐组件报告部分成功和不确定性。

## 14. 数据归属与移除

| 组件 | 负责方 | 默认移除行为 |
| --- | --- | --- |
| 桌面二进制/UI | 桌面包/updater | 移除所选应用/通道 |
| 连接/偏好/有界通知元数据 | 桌面用户目录 | 明确重置/移除选项 |
| 桌面凭据 | 系统凭据库 | 仅所选、己方且不再被引用的条目 |
| Python/Agent 发行物/安装 journal | 安装器/分发 | 保留引用，通过归属感知计划处理 |
| 服务注册/受保护环境 | 服务/配置 | 除非单独移除服务，否则保留 |
| Memory/Source/Artifact/处理状态/数据库 | Server 持久化 | 桌面/服务卸载时保留 |
| 宿主配置 | 宿主适配器/用户 | 仅撤销记录过的己方变更，保留无关/用户编辑 |

现有 `POWERCONTEXT_HOME` 和平台规则仍是权威，版本化应用目录不是业务数据目录。本地诊断可显示/打开已知
本地位置，远程连接不能浏览 Server 文件系统。“移除桌面”“移除本地服务”“删除数据”分别命名。首版自动卸载器
不删除业务数据，保留归属未知内容，不递归删除任意所选位置。

## 15. 诊断与隐私

展示核验后的组件/平台/状态、契约版本、安全 request ID、有界耗时与规范化错误码。普通日志/导出不含正文、
prompt、prepared context、模型输出、凭据、Authorization、原始环境、敏感标题、URL 查询和私有路径。
向本地用户显示路径不代表加入导出。规范化 CLI/Server 错误，不直接附 stdout/stderr。

导出在本地显式执行且可预览。崩溃上报默认关闭，无正文诊断不允许内存转储或原始请求。限制日志大小/保留期。
威胁包括恶意内容/文件、伪造激活/IPC、错误端点和篡改发行物；不承诺抵御被控制的 OS、任意同用户恶意软件或
特权管理员。将秘密标记植入凭据、路径、provider 设置、错误和内容，检查所有可观察输出。

## 16. 平台、无障碍与预算

| 平台 | 建议状态 | 验收 |
| --- | --- | --- |
| Windows 11 x64 + SQLite | 首个平台；当前项目 Windows 支持仍为 experimental | 签名标准用户安装、WebView2 有/无、Credential Manager、Task Scheduler/登录、通知/激活、非 ASCII 路径 |
| macOS | 后续 | 具名架构、Keychain、LaunchAgent、签名/notarization、WebView/通知行为 |
| Linux | 按发行版/桌面环境后续验收 | WebKitGTK/系统库、Secret Service、systemd 会话、托盘、包/激活 |

不包含 Windows ARM 和 Windows 嵌入式 seekdb。框架编译通过不代表平台验收。P0 确定 Desktop/Install/Server/
Release 负责人和一个维护中 Agent Host/版本，观察 Windows 加载及显式 capture/recall。“任意维护中集成”不能
通过该门槛；P4 另行明确真实 sender/receiver。

当前源码基线在 Windows 原生类型检查中暴露了处理 worker 的 `Connection`/`PipeConnection` 类型不匹配，以及
测试引用 POSIX 专用 `os.WNOHANG` 的问题。Windows 验收前需要修复或正确限定这些检查的平台范围；按 Linux
目标检查通过不能证明 Windows 已受支持。

P0 Go/No-Go 证据包括：无 Python/Server 时 UI 可用、认证 API 读写、凭据、签名标准用户包、WebView2 bootstrap、
独立服务/登录、安装后的通知冷启动激活。D4/D5 完整 bootstrap 可以留到 P3，但 P0 要记录负责方承诺，并将预览
限制为仅连接。原生阻塞必须解决或重新讨论平台/范围；Electron 备选针对实际外壳/WebView/维护阻塞，不解决安装器缺口。

中英文 UI/文档同步，支持键盘、可见焦点、屏幕阅读器标签、IME 安全输入、高对比度、不只靠颜色的状态，以及
800 × 600 和 200% 缩放下可用的确认/恢复。语言/主题变化保留身份。

测量冷启动、空闲 CPU/唤醒、桌面+WebView+Server 内存、完整安装/下载大小、列表/搜索延迟。P0 记录硬件、OS/
WebView、数据规模、重复次数和 p50/p95，在 P2 扩展前固定数字发行预算。覆盖空/多页、无模型/已配置模型场景。
D7 负责公开预算，当前不宣称性能结果。传输/通知运行上限不能替代测量。

## 17. 交付阶段

| 阶段 | 交付物 | 退出条件 |
| --- | --- | --- |
| P0：架构 | 打包客户端、窄传输、凭据、Windows 安装原型、UI 复用与测量 | D7 负责人/宿主、安全证据、D1/D2 分工、D3–D6 限制 |
| P1：仅连接预览 | 已有本地/远程连接、已测兼容、服务状态、显式 Memory 保存/召回、Agent 诊断 | 验收操作/身份，不承诺未实现安装 |
| P2：管理/授权 | Scope/资产/Source、类型化 Review、精确共享/报告、导入、有范围的 Review 通知、诊断 | D2 完整 Memory 浏览、授权/并发/family 契约 |
| P3：受管安装 | 干净机器安装、所选宿主、服务/配置修改、迁移/升级/恢复/移除 | D3/D4/D5、签名不可变包和归属验收 |
| P4：投递 | 持久收件箱、目标关联、恢复、精确导航、支持的接收动作 | D6、具名 sender/receiver、有界消费者、安装激活 |
| P5：首个正式发行 | Windows 11 x64 完整 #1428 流程 | 全部适用 AC、兼容/支持矩阵、公开预算 |
| P6：更多平台 | 验收后的 macOS/Linux 包 | 每个声明环境重复安装验收 |

依赖具备后 P3/P4 可独立推进；P2 授权不等待投递。复用已有 Tracking Issue，负责方契约与消费者拆成聚焦 PR。
不能把被阻塞的必需 AC 标为不适用来关闭 #1428：完整交付需要一个平台上的受管本地安装、授权远程访问、持久
Handoff 投递、Review/Handoff 通知、恢复、无障碍和保留数据的移除。

## 18. 验收与验证

负责角色：Desktop 负责打包 UI/原生行为，Server 负责公开语义，Install 负责安装器/服务/配置/分发，Delivery
负责 D6，Release 负责签名平台验收。这些是职责，不是已具名人员，D7 在 P0 退出前落实维护者。每项记录包含版本、
环境、fixture、结果和负责人；一次冒烟不能代表某行所有场景通过。

| ID | 阶段/负责方 | 必须观察到的行为 | 验证入口 |
| --- | --- | --- | --- |
| AC-01 | P3/P5 · Install + Desktop | 干净机器核验 runtime/宿主，无模型 Memory 保存/fts 召回成功 | 安装后的首次使用 |
| AC-02 | P1/P3 · Install | 区分过期/外部/占用状态，保留未知归属 | 服务 JSON/原生生命周期 |
| AC-03 | P3/P5 · Desktop + Install | 关闭/退出/重启/登录保留独立服务与工作，遵守所选启动方式 | 安装生命周期/恢复 |
| AC-04 | P3/P5 · Install + Release | 中断升级/签名/就绪失败有持久组件状态和兼容恢复 | 安装器故障/重启 |
| AC-05 | P1/P2 · Server + Desktop | 缺失/旧/未知握手、缺能力、身份变化不猜测支持或改投 | D1 连接 fixture |
| AC-06 | P1 · Desktop | loopback/base path/明文/TLS/重定向/代理限制准确，不转发凭据 | 共享向量/原生传输 |
| AC-07 | P1/P2 · Desktop | 连接/端点/token/Principal 变化隔离响应、游标、草稿和修改目标 | 打包后的并发交互 |
| AC-08 | P2 · Server + Desktop | 无 Scope 列表权限仍可访问精确 Handoff；拒绝 latest/相邻/宽泛/证据泄露 | Access 与桌面流程 |
| AC-09 | P2 · Server + Desktop | 分页/计数前过滤、不安全回退禁止、Review/发布仍授权 | Access/修改契约 |
| AC-10 | P2 · Server + Desktop | 过期版本/citation、重复提交、响应丢失不静默批准/重放 | 修改恢复场景 |
| AC-11 | P4 · Delivery + Desktop | 离线到达、游标过期、撤权/取消恢复精确授权收件箱 | D6 和接收端组合 |
| AC-12 | P0/P4 · Desktop + Release | 安装提示、拒绝许可、突发、退出、过期激活安全导航/回退 | 原生/冷启动激活 |
| AC-13 | P2 · Server + Desktop | 相同/改名/变化文本、BOM/换行、非法/超限、未知导入遵守身份/限制 | 导入/句柄 fixture |
| AC-14 | P0/P2 · Desktop | 恶意内容、伪造 generation/窗口/路径/链接不能执行、读秘密或意外修改 | 打包 capability/CSP |
| AC-15 | P0/P5 · Desktop + Release | 日志、URL、通知、导出、渲染层存储、遥测无秘密标记 | 输出检查 |
| AC-16 | P3/P5 · Install | 移除保留数据、用户编辑和仍引用的独立消费者 | 安装后的移除 |
| AC-17 | P2/P5 · Desktop | 双语、键盘/IME/阅读器/对比度/小窗口/200% 缩放可完成支持动作 | 无障碍流程 |
| AC-18 | P0/P5 · Release | 精确签名包在参考机器测量，P5 达到公开预算 | 基准/支持记录 |
| AC-19 | P2 · Server + Desktop | 大型/变化 Memory 在 D2 下完整有界遍历，D2 前如实限制 | 大 Scope fixture |
| AC-20 | P2 · Server + Desktop | Profile Review、Topic Memory 和未知 family 保持类型化/只读边界 | Family/Review fixture |
| AC-21 | P1/P3 · Server + Install + Desktop | 静态/注入 Provider、通用 401/503、部分轮换保持真实身份/错误/恢复 | 认证/配置流程 |
| AC-22 | P2 · Desktop | 积压、多页、变化/部分覆盖遵守预算，不伪造计数/历史 | 轮询行为 |
| AC-23 | P3 · Install + Release | updater 退出和 stable/preview 共享保留负责方恢复与唯一管理归属 | 打包升级/通道 |
| AC-24 | P0/P2 · Desktop | 无 Python/Server 可安装引导，共享资源不漂移，管理只用公开 API | 桌面构建/Dashboard 回归 |
| AC-25 | P3 · Server + Install | 维护迁移同 ID 恢复，流量前验证，无不兼容回滚 | 迁移/安装恢复 |
| AC-26 | P4 · Delivery + Desktop | 多设备/目标、未注册 Agent 不冒充，不把提示转为接受 | 目标关联 |

实现 PR 运行 `make check` 和相关行为测试；契约变更额外运行 `make api-generate`、`make contract-test`。
复用 Server/Access/传输/迁移/原生服务测试。共享 UI 需要 Dashboard 回归和桌面行为；打包需要真实安装测试。
文档运行 `make docs-test`（Fumadocs），核验标题/导航/链接及中英文阶段/依赖/AC ID 一致。Mock 或文档构建不能
证明原生行为合格。

# 缺点与替代方案

项目增加 Rust/原生维护、客户端 UI/构建、签名/升级和平台测试。共享展示减少部分重复，但不能省去客户端管理流程。
Python/WebView 可能占据大部分资源。独立 runtime 升级需要兼容和数据恢复；投递仍依赖另一负责方。

| 考虑项 | Tauri 2 | Electron | 判断 |
| --- | --- | --- | --- |
| Web 展示 | 系统 WebView | 自带 Chromium | 都支持客户端 UI，都不能直接移植 Jinja |
| 原生边界 | Rust 与应用/插件权限 | 主进程和受限 preload/IPC | 倾向小型 Rust 宿主，实际验证权限 |
| 体积/渲染 | 系统依赖与引擎差异 | 更大引擎、较一致渲染 | 测量完整安装产品 |
| Python/服务/数据 | 外部 runtime 与迁移 | 同样需要 | 都不替代安装器/服务契约 |
| 维护 | Rust/平台经验 | Electron/JavaScript 经验 | D7 确定维护/发行负责人 |

选择 Tauri 2，并受 P0 门槛约束。Electron 用于实际 WebView/原生集成或持续维护阻塞时的备选，保留全部 API/归属
规则，不同时维护两套正式外壳。Web-only 仍有价值，但缺少所需原生能力。当前范围不支持特权远程页面、桌面持有
的 Server 子进程、Runtime 重写或完全原生的重复展示层。

# 相关设计与参考

项目契约：[服务](1299_local_server_availability_and_service_installation.md)、
[Scope](1345_scope_organization_and_agent_integration.md)、[授权](1396_handoff_access_control.md)、
[Source](1400_source_definition_and_observation_model.md)、[基础 REST](1437_source_artifact_rest_api.md)、
[Profile](1485_profile_artifact.md)、[处理](1515_artifact_processing_supervisor.md)、
[family 读取](1549_artifact_family_unification.md)、[Skill 生命周期](1351_standard_skill_package_lifecycle.md)、
[Dashboard](../development/dashboard.md)、[处理迁移](../docs/operate/artifact-processing-migration.md)。

在真实包中验收框架机制：[Tauri 架构](https://v2.tauri.app/concept/architecture/)、
[capability](https://v2.tauri.app/security/capabilities/)、[CSP](https://v2.tauri.app/security/csp/)、
[Windows 安装](https://v2.tauri.app/distribute/windows-installer/)、[通知](https://v2.tauri.app/plugin/notification/)、
[updater](https://v2.tauri.app/plugin/updater/)、[Stronghold](https://v2.tauri.app/plugin/stronghold/)、
[Electron 安全](https://www.electronjs.org/docs/latest/tutorial/security)。

# 需要维护者共同决定的事项

这些问题都有建议默认方案和明确决策时间。缺失依赖仍是交付前提，不能写成已实现能力，也不妨碍提交设计供评审。

| 通俗问题 | 建议默认方案 | 何时决定 |
| --- | --- | --- |
| 先把哪个系统做好，谁长期维护和发版？ | Windows 11 x64 + SQLite；确定 Desktop/Install/Server/Release 负责人和一个 Agent Host/版本 | 接受 RFC 时确定平台；P0 结束前落实人员/宿主 |
| 和现在的 Dashboard 共用多少界面？ | 资源/规则/翻译/组件，独立客户端管理入口，不强制重写 Dashboard | 接受 RFC 时 |
| 安装器和投递还没好，能不能先发布？ | 先仅连接预览，再管理；P3/P4 独立，完整前不关闭 #1428 | 接受 RFC 时 |
| 第一版能连接哪些远程环境？ | 直连 HTTPS、运维发放 Bearer；暂不做代理/SSO/明文同意 | 接受 RFC 时；扩展需验收适配器 |
| 支持哪些 Server 版本，恢复或克隆后如何识别？ | D1 明确版本/生命周期，1.0.0 作为旧版验收候选 | P0 契约讨论，早于相关控制交付 |
| 安装和投递接口由谁提供，什么时候可用？ | D3–D6 原负责方维护 schema/恢复，桌面不另造替代 | 承诺 P3/P4 前 |
| 整个产品要多快、多省资源？ | 在具名硬件实测，公布含 Python/WebView 的数字预算 | P0 结束、P2 扩展前 |

# 未来可能性

首个平台验收后，再增加 macOS/Linux、代理/浏览器身份适配器、用户明确选择的多 Scope/连接监控、更多导入格式和
有契约的连接器管理。持久离线内容/写入及更广 Agent 执行引入新一致性/安全责任，需要单独提案。

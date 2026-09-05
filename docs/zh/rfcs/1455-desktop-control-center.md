- 提案名称：`desktop_control_center`
- 开始日期：2026-09-04
- RFC PR：[oceanbase/powercontext#1455](https://github.com/oceanbase/powercontext/pull/1455)
- Tracking Issue：[oceanbase/powercontext#1428](https://github.com/oceanbase/powercontext/issues/1428)
- 状态：提案

# 概要

采用 **Tauri 2、共享 Web 管理界面、独立运行的现有 Python Server**，构建 PowerContext 桌面控制中心。
用户可以在一个应用里安装和诊断 PowerContext，连接本地或远程 Server，组织 Scope，查看和管理 Memory 及经过
Review 的资产，处理 Handoff 和 Review 待办。Rust 只负责范围明确的桌面系统能力；业务行为、持久化、授权和持久任务
仍由 Server 负责。安装和原生服务管理继续使用各自已有的负责层。

建议首个完成正式验收的平台为 **Windows 11 x64，本地使用 SQLite 后端**。macOS 和 Linux 沿用同一架构，但需要分别
完成安装、安全、升级和可用性验收。个人预览版可以提前交付；完成 #1428 还需要本文规定的授权和持久 Handoff 投递依赖。
合并这份 RFC 不代表关闭该 Tracking Issue。

# 动机

PowerContext 已有 Python SDK、HTTP API、Agent 集成、Server 托管的 Web UI 和原生用户级服务管理。用户要回答一些
基本问题，仍然需要理解多套安装、配置、版本和诊断入口：Server 是否运行？Agent 是否使用了正确的 Scope？哪些内容
需要 Review？Handoff 发到了哪里？升级会不会丢数据？

桌面端应让用户在一个应用中回答这些问题。它的价值在于原生安装与服务状态管理、安全保存凭据、系统通知和一致的管理
界面，同时保留 PowerContext 在没有打开任何应用窗口时，仍能独立服务多个 Agent 的能力。

本提案面向三类用户：

- 新个人用户：不准备 Python 环境、不编译源码，就能用上本地 Server 和自己选择的 Agent 集成。
- 已有 CLI 或 Web 用户：方便地管理现有部署，不被自动迁移数据或替换配置。
- 团队 Server 用户：通过认证和授权，访问共享工作以及精确版本的 Handoff。

本提案不增加聊天客户端、IDE、Agent Runtime、自主任务编排器、新的 Memory 引擎或桌面数据库副本，也不让桌面端
成为 SDK、CLI、Server 或集成的必需依赖。

# 面向使用者的说明

## 用户安装什么，运行在哪里

用户安装一个名为 **PowerContext Desktop** 的原生应用，它有独立窗口、应用图标，以及平台支持时的托盘入口。源码
放在 PowerContext 同一个仓库内。桌面发行物包含可信的本地 UI 和原生宿主；本地模式还通过统一安装器安装独立版本化
的 Python 运行环境。仅连接远程 Server 时，不需要安装本地 Python 环境或本地 PowerContext Server。

应用提供两种连接选择：

| 选择 | 用户体验 | 由谁管理 |
| --- | --- | --- |
| 这台电脑 | 安装或连接当前系统用户的本地 Server，查看服务和集成状态 | 已有服务层和操作系统服务管理器管理 Server |
| 远程 Server | 输入 HTTPS 地址和凭据，查看所连接的 Server 及自己有权访问的资源 | 远程运维方管理运行环境、数据和服务生命周期 |

连接配置保存地址设置和受保护凭据的引用。窗口明确显示当前连接及其 Scope 选择。只有经过核验、由本机管理的安装才
提供本地服务控制。远程连接的配置不会被转换成本机服务定义。

## 第一次成功使用

1. 应用解释本地和远程模式。本地安装时，先展示发行版本、组件、数据位置、所选 Agent 宿主，以及安装器计划修改的内容。
2. 用户确认这个具体计划。安装器校验不可变发行物，安装运行环境和所选集成，注册用户级服务，并报告各组件结果。
   失败时保留明确的恢复入口。
3. 用户可以不填写模型，先用最小配置启动。显式保存 Memory 和可用的全文检索构成首次成功路径。生成、提取、向量检索
   等依赖模型的功能，按实际能力显示要求。
4. 用户选择或创建 Scope，显式保存一条小型 Memory，并在相同 Scope 中召回它。也可以导入 Source，但接收 Source
   不等于已提取出 Memory。
5. 单独检查所选 Agent 集成。界面区分“已安装”“宿主已成功加载”“实际 capture/recall 检查通过”，不把未检查的集成
   标成健康。
6. 用户可以找到待 Review 内容，以及支持时的 Handoff 收件箱。点击通知后，应用先刷新状态，再打开对应的精确授权项。

对于已有安装，先发现和检查，再提出修改计划。可访问但归属未知的进程可以作为连接目标；桌面不能擅自终止它、替换其
运行环境或接管其端口。

## 主要界面

| 界面 | 必需行为 | 边界 |
| --- | --- | --- |
| 总览 | 当前连接、Server 就绪状态、本地服务状态、支持的功能、待处理事项、恢复操作 | 就绪、安装、认证、授权是不同状态 |
| 项目与工作流 | 展示 Scope 及组织关系，提供 `all` / `subtree` / `exact` 观察视图，管理支持的绑定 | 项目和工作流是 Scope 的展示名称，不增加第二套身份体系 |
| Memory 与资产 | 列表、搜索、读取 Memory；通过已有 API 显式记忆、修订、退役；查看 Experience、Skill、来源与生命周期 | 保留精确引用，以及既有 Review 和发布规则 |
| Review | 筛选和查看 Candidate，批准、拒绝、修订，并显示冲突 | 针对当前展示的 Candidate 版本，由 Server 授权执行 |
| Handoff | 精确授权的 Handoff 详情、只读报告，以及能力就绪后的投递收件箱 | 授权发现、投递、查看、确认接收和任务结果保持区分 |
| Sources 与连接器 | 导入所选内容，查看支持的 Source 状态，以及可用的连接器健康和恢复信息 | 后台摄取由 Server/连接器 worker 承担；缺少管理 API 时明确显示不可用 |
| Agent 与集成 | 选择维护中的发行物，查看声明能力、安装版本、诊断结果，以及授权允许的绑定修改 | 复用分发和安装契约，不重写宿主适配器 |
| 设置与诊断 | 连接、凭据、语言、通知、数据位置、版本、升级和脱敏诊断 | 远程管理操作需要独立声明并授权的 API |

首版不承诺完整的连接器市场、Handoff 编辑器、任意 Skill 执行能力，或覆盖 SDK 的每一个操作。但已支持的页面必须
完成所声明的用户流程，不能把不可用功能伪装成能工作的占位按钮。

## 关闭窗口和离线使用

有托盘时，关闭最后一个窗口默认隐藏应用；明确选择“退出”才结束桌面进程。没有托盘时，窗口应说明关闭行为，并提供
清晰的退出入口。两种操作都不注销或终止独立管理的 Server。桌面开机启动和 Server 随用户登录启动是两个独立设置。

首版系统通知要求桌面进程仍在运行。桌面关闭期间，Server 保留持久任务和 Handoff 收件箱；重新打开应用时刷新权威
状态。远程连接离线时明确显示断连，不排队保存业务写操作。本地 Server 仍可提供不依赖失联模型或远程服务的能力。

# 技术设计

## 1. 当前基线与相关工作

本提案核验的实现基线为 2026-09-04 的上游 `master`：
[`f0f288abecaccb97e1fe97d991b87b808bbebfbd`](https://github.com/oceanbase/powercontext/commit/f0f288abecaccb97e1fe97d991b87b808bbebfbd)。
下表描述该基线已有的实现，不表示桌面产品已经发布：

| 已有部分 | 可以复用的能力 | 与本提案相关的缺口 |
| --- | --- | --- |
| 公开 HTTP 契约 | Scope 与绑定、Memory、Source 摄取、Candidate、Skill、精确 Handoff 操作、统计和报告 | 没有桌面兼容性握手或持久 Handoff 投递收件箱契约 |
| Web UI | 总览、Skill、Review、Handoff Report 的 Jinja 模板和 JavaScript 模块 | 部分辅助路由位于 `/dashboard`；桌面业务访问必须使用公开 API |
| 原生服务层 | `service install`、`service status --json`、`service uninstall`；独立的用户级服务注册 | 尚无公开的 `service start/stop/restart --json` 接口 |
| 配置 | 不配置推理也可校验最小 Server 配置；模型能力可选 | 尚无桌面引导和受保护的配置编辑流程 |
| 认证 | 可选的部署级静态 Bearer 认证 | 此基线尚未实现资源级 Principal/角色授权 |
| 集成清单和诊断 | 随版本维护的能力声明与结构化集成检查 | 二者都不是实时 Handoff 接收端注册表 |
| 已发布软件包 | 已发布的 `0.1.0` 与开发中的 `master` 明确区分 | `0.1.0` 不包含原生 `service` 命令 |

最新安装文档已经区分发布版本与未发布代码的安装路径。桌面必须选择在 manifest 中明确支持所需 runtime/service 契约
的发行版本，或清楚标注锁定版本的预发布包。不能静默安装持续变化的 `master`，不能拼接不相关的集成和 runtime 版本，
也不能声称 `0.1.0` 已具备 service 能力。

| 依赖 | 基线时的状态 | 需要协调的内容 |
| --- | --- | --- |
| [RFC 1299](1299_local_server_availability_and_service_installation.md) | 服务架构及实现已进入 `master` | 保留唯一服务管理层和结构化状态语义 |
| [RFC 1345](1345_scope_organization_and_agent_integration.md) | Scope 模型及集成契约已具备 | 复用 Scope 身份、组织、绑定和显式发布 |
| [RFC 1396](1396_handoff_access_control.md)、实现 [#1398](https://github.com/oceanbase/powercontext/pull/1398) | RFC 已合并，实现 PR 仍开放 | 团队和资源共享验收依赖 Server 强制授权与授权后发现 |
| [#1419](https://github.com/oceanbase/powercontext/issues/1419) | Handoff 投递 Tracking Issue 仍开放 | 负责接收端登记、envelope、持久收件箱、投递状态、重试、过期和恢复 |
| [#1406](https://github.com/oceanbase/powercontext/issues/1406)、RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | 安装 Tracking Issue 与 RFC PR 仍开放 | 负责 bootstrap、安装计划、组件安装、版本记录和恢复 |
| [#1405](https://github.com/oceanbase/powercontext/issues/1405)、RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | 分发 Tracking Issue 与 RFC PR 仍开放 | 负责标准 Agent 发行物、target profile 和宿主配置规则 |
| [RFC 1400](1400_source_definition_and_observation_model.md) | Source 身份和观察模型设计已在仓库中 | 保持 Source 语义；连接器管理需要独立的受支持接口 |

开放提案提供协调约束，不代表协议已经实现。最终由负责方确定的契约，优先于本文用于说明的名称。个人预览版可以先用
已有的单部署能力；依赖通过验收之前，不能宣称已支持资源隔离的团队共享或可靠投递。

## 2. 组件职责与仓库位置

```text
可信 Web UI（共享展示和页面行为）
    浏览器适配器 ---------------------> 公开 Python Server HTTP API
    桌面适配器 -> 受限 Rust 桥接层 ------> 公开 Python Server HTTP API
                      |
                      +-> 系统凭据库、托盘、通知、文件选择器
                      +-> 统一安装器和已有服务接口

操作系统服务管理器 -> 独立 Python Server -> 业务持久化和持久 worker
统一安装器         -> 已验证的 runtime/集成发行物与安装记录
```

| 组件 | 负责 | 不应负责 |
| --- | --- | --- |
| 共享 Web UI | 导航、本地化展示、表单、受支持的用户操作 | 授权裁决、业务持久化、后台摄取 |
| Rust 桌面宿主 | 受限系统集成、受保护凭据访问、认证传输、有限本地偏好 | Memory/Handoff 语义、数据库访问、第二套安装器或服务监管器 |
| Python Server | 公开 API、Runtime 能力、业务校验、授权、持久化、持久处理 | 依赖桌面窗口保持打开 |
| 安装与分发层 | 发行物身份、bootstrap、安装计划、宿主配置、归属记录、升级恢复 | 桌面专属业务规则 |
| 已有服务层与系统管理器 | 用户级注册、服务身份、状态和生命周期 | 另一套争用相同端点的桌面守护进程 |

在现有仓库中增加 `desktop/`，其中 `desktop/src-tauri/` 放 Tauri 宿主，另放桌面入口资源、打包配置和桌面验收工具。
首次实现从已有 Web UI 中提取可复用的展示与传输边界。Server 托管的模板和静态资源继续位于
`src/powercontext/server/`，继续随 Python wheel 分发。

桌面是独立构建的应用，可以引入自己的前端构建过程，但不能让 Python 包安装或现有 Server UI 运行依赖 Node、Rust
或桌面依赖。无需先迁移 React/Vue：在出现明确需求之前，现有 HTML、CSS 和 JavaScript 模块可以继续使用。生成的
桌面入口标记应来自同一份共享源，并在发布前构建；安装后的桌面无需执行 Jinja 或连接 Server，就能显示安装和恢复页面。

Tauri capability、插件、依赖和锁文件需要审查并锁定。桌面专属 CI 与日常 Python 开发分开；修改共享 UI/API 时，仍须
通过原有测试要求。

## 3. 公开 API 复用与兼容性

桌面是公开 API 的客户端，不能导入 Python Runtime 对象、打开业务数据库、抓取渲染后的 HTML，或依赖私有的
`/dashboard/*` 辅助端点。共享页面通过传输适配器调用接口：浏览器使用 Web 部署的认证流程，桌面使用原生桥接层。
页面代码不自行保存凭据或拼装路由。

已有 API 覆盖了初期管理的大部分操作：

| 领域 | 已有公开接口 | 桌面实现要求 |
| --- | --- | --- |
| 健康和能力 | `/health/live`、`/health/ready`、`/v1/capabilities` | 区分进程存活、runtime 就绪、功能可用 |
| Scope | `/v1/scopes/*`、`/v1/scope-bindings/*`、Artifact 发布 API | 复用精确身份，以及支持的选择和绑定操作 |
| Memory | `/v1/memory/*` | 遵守大小限制、citation、修订冲突和已声明的搜索模式 |
| Review | `/v1/artifact-candidates/*` | 传递预期 Candidate 版本，显示冲突而非覆盖 |
| Skill 与 Experience | `/v1/skill/*`、`/v1/experience/*` | 保留受管生命周期、精确包引用和 Review 要求 |
| Handoff 与工作 | `/v1/handoff/*`、`/v1/work/*`、`/v1/handoff-reports/get` | 复用精确继续、确认接收、结果和只读报告 |
| Source | `/v1/sources/content`、Source 定义、观察、连接器 checkpoint | 使用受支持的摄取契约；checkpoint API 不是连接器管理面 |
| 统计 | `/v1/stats` | 使用 Server 授权的投影，不在客户端聚合未限制的记录 |

缺失的公开投影必须先加入 `openapi/powercontext.yaml`，再运行 `make api-generate` 和 `make contract-test`，之后
才可交付对应桌面功能。其他客户端也能使用相同路由和授权规则。本 RFC 本身不增加已实现的端点。

本提案建议新增受认证保护的 **`GET /v1/server-info`** 握手，初始契约包含 `schema_version`、`product`、持久化的
不透明 `server_id`、`package_version`、`api_contract_version` 和版本化的 `feature_contracts`。这些字段描述部署
身份和协议兼容性；运行时 provider 是否可用仍通过 `/v1/capabilities` 获取。该接口不能暴露文件路径、凭据、用户清单
或未经授权的资源元数据。它遵守 Server 的认证策略，只提供认证客户端建立连接所需的最小元数据。

精确的 OpenAPI schema 和兼容性标识由 Server 负责，属于前置工作。每个桌面版本声明自己理解的契约版本和可选能力，
不能仅比较软件包版本字符串来判断兼容性。未知的可选能力可以忽略；必需契约不兼容时，阻止相关操作并解释升级要求。
没有握手的旧 Server 应标记为“旧版/兼容性未知”，只提供明确测试过的支持，不能根据猜测版本开启功能。

`server_id` 是关联标识，不是归属或认证证明。凭据、经过验证的 TLS，以及核验过的本地安装/服务记录共同构成连接
信任依据。Server 身份意外变化时，使待执行操作和缓存选择失效，要求用户明确重新连接。握手不能触发自动 runtime
升级、凭据转移或远程部署迁移。

## 4. 连接配置、传输与原生桥接

连接配置持久化本地不透明 profile ID、显示名称、规范化地址及受支持的 base path、连接模式、凭据引用、TLS 信任配置
和已观察到的兼容信息，不保存业务记录。远程配置不能选择本地可执行文件或服务环境。

首版每个窗口只有一个活动连接，每个系统用户、每个发行通道只运行一个桌面实例。再次启动通过限定当前系统用户的原生
IPC 激活已有实例，不额外开放 HTTP 管理监听端口。切换配置时递增连接 generation，取消未完成的读取，清理私有视图，
丢弃旧 generation 的迟到响应。已提交的写入始终关联原端点、Principal、Scope 和精确项，不能因切换连接而改投另一处。

传输层必须遵守现有客户端的 loopback 规则，包括 `tests/fixtures/transport_loopback_vectors.json` 中的公共用例：

- 非 loopback 地址必须使用 HTTPS，并正常校验主机名与证书。允许现有客户端规则认可的 loopback HTTP；仅能访问
  loopback 并不能证明 Server 可信。
- 拒绝地址中的用户信息、查询参数和 fragment，凭据不得放进 URL。保留受支持的 API base path，同时避免操作路径
  逃逸该前缀。
- 首版拒绝认证 API 请求重定向，不向其他主机、协议或端口转发凭据。如支持自定义 CA，只能显式绑定某个连接配置，
  不提供持久化的“关闭证书校验”开关。
- 限制连接/读取超时、报文大小和分页，支持取消。分别呈现传输失败、证书失败、认证失败、无权访问、冲突、协议不兼容
  和服务不可用。
- 远程配置从提供凭据开始。公开健康检查成功不足以证明管理访问已通过认证；多用户使用还必须满足第 7 节资源授权契约。

Rust 在请求中注入所选凭据。WebView 只得到数据和安全错误，不提供读取凭据的 API。桥接命令限定为允许的公开操作 ID
及类型化参数、连接选择、只写凭据替换、有边界的文件选择/导入、诊断，以及支持的安装和服务操作。

不提供任意 `fetch(url)`、shell 执行、原始文件系统、终止进程或数据库桥接。渲染层不能自行决定可执行文件、命令行、
发行源、目标路径或凭据请求头。原生侧独立校验所选连接、操作、参数、限制和当前操作上下文，不能只依赖 UI 按钮约束。
文件操作使用系统选择的句柄或受限目标位置，不接受渲染层传入的任意路径。

只有随应用打包的本地 UI 文档拥有 Tauri capability。远程 Server 响应视为不可信数据，不在有原生权限的窗口中加载
远程 HTML。使用严格 CSP，禁止远程脚本和不受限内联执行。以不可执行的方式安全渲染文本和支持的 Markdown；导入内容
不能启动命令、加载远程图片、导航特权窗口，或通过嵌入标记调用 IPC。只有用户明确操作才在系统浏览器打开外部 HTTP(S)
链接；其他 URL scheme 需要单独审查并加入允许列表。

## 5. 本地服务生命周期与安装控制

本地 Server 保持 RFC 1299 的用户级身份：Linux 使用 systemd user service，macOS 使用 LaunchAgent，Windows
使用 Task Scheduler。桌面安装不请求 root、SYSTEM 或第二套机器级服务。服务配置仍只面向本地 loopback，来源于
经过验证的本机安装环境。

已有结构化状态字段保持独立：

| 字段 | 对桌面的含义 |
| --- | --- |
| `support` | 当前平台/环境是否支持原生服务注册 |
| `registration` | 注册是否存在、是否合法 |
| `definition` | 可执行文件和环境身份是否仍然有效 |
| `manager_ownership` | 系统管理器加载的条目是否属于 PowerContext |
| `manager` | active/inactive/failed/unknown 管理器状态 |
| `server_liveness` | 端点 live/unreachable/unknown |
| `endpoint`、`log_location`、`recovery_action` | 本地检查和恢复信息，按需脱敏展示 |

`service status --json` 即使返回非零退出码，也可能包含合法的“不健康”结构化结果。应先解析约定结果，再判断是否
执行失败。端点存活但归属 foreign 或 unknown，不代表受管安装健康。不能终止占用端口的进程、删除其他注册，或替换
归属不明的可执行文件。

通过已有服务层使用 `service install` 的校准能力和 `service uninstall` 语义。如果产品需要显式启动、停止或重启，
必须先在该服务层补充操作和机器可读结果，当前 CLI 尚未提供。能力就绪前隐藏这些控制，并提供受支持的恢复入口，
不能用“卸载服务”实现“停止”。

桌面消费 #1406 及其安装 RFC 所负责的安装计划、核验后的组件结果和恢复语义。桌面管理安装的前提是提供版本化、
非交互的机器接口。桌面不能另写安装引擎，也不能只凭退出码推断成功。特别是 #1408 提出的阶段和结构化输出，尚未
定义公开的 `plan/apply/status` 命令或 JSON schema。

该接口需要提供可审查的计划、不可变组件身份、受影响位置、归属和兼容性检查、可观察进度、取消边界、持久操作身份、
组件结果，以及客户端中断后的恢复能力。resolve/preflight 不修改安装；应用过期计划之前重新核验。并发操作锁和
持久 journal 由安装器维护，不同入口不能竞争修改同一个安装。

Runtime 和各宿主组件可以分别成功。`uncertain` 必须先核验再重试；`installed` 不证明宿主已经加载成功。桌面如实
展示负责方定义的 `unsupported`、`skipped`、`installed`、`current`、`stale`、`failed`、`uncertain` 状态，
不虚构跨多个独立宿主的全局原子回滚。

只有安装负责方支持持久执行和恢复时，关闭窗口才能让安装在后台继续。否则应用保留操作界面，只在安全边界提供取消。
强制退出后必须能根据安装记录恢复，不能承诺普通 Tauri 子进程会在“退出”后继续运行。稳态 Python Server 始终由
已有系统服务注册独立管理。

## 6. 凭据与本地配置

使用明确的系统凭据库适配器：首个 Windows 目标使用 Windows Credential Manager；macOS 和 Linux 验收时分别
接入 Keychain、Secret Service。桌面偏好只保存不透明引用。凭据库不可用或锁定时，要求解锁、仅本次会话使用，或
走另行支持的加密 vault 流程，不能静默回退明文存储。Tauri Stronghold 可以用于 vault，但它本身不是系统凭据库，
首个平台不以引入 Stronghold 为前提。

用户在可信配置表单输入或粘贴凭据时，凭据可以短暂存在于输入框和只写 IPC 参数中。提交后清空，不提供读回操作，
不保存到 WebView local/session storage、URL、命令行参数、日志、诊断、崩溃报告或通知。原生传输层在输出可观察
错误前，清除 Authorization 头和敏感请求/响应字段。用户复制的 token 也可能留在系统剪贴板中；应用不声称能够抵御
以相同用户身份运行的任意软件。

Server 的认证/provider secret 与桌面客户端凭据具有不同生命周期。独立 Server 必须能在桌面未运行、桌面 vault
未解锁时取得自己的配置。本地安装委托安装/服务配置负责方生成并校验配置、设置严格文件权限、处理环境身份，不能
把凭据放入服务命令行。涉及已注册环境的配置变更，必须通过服务层校准流程生效。

发现过程只读取已知安装/服务记录及用户明确选择的配置文件，不扫描无关用户目录、不导入全部环境变量、不把 Server
或 provider 凭据复制到 UI 偏好。应用敏感配置修改前，展示作用范围及所需重启/校准操作。删除桌面连接时移除其凭据
引用，并提供删除该凭据的选项；不能删除独立 Server 或 Agent 宿主仍在使用的凭据和环境文件。

## 7. 认证与资源授权

现有静态 Bearer 中间件建立的是部署级信任边界，并不提供团队角色或资源级共享。个人预览版可以明确以“共享信任”
模式连接这种部署。桌面管理的新本地安装默认应开启 Server 认证；连接已有未认证 loopback 部署时，展示其真实策略，
不静默修改它。

团队模式要求 Server 完成 RFC 1396 及相关实现规定的强制授权。可信 Principal 由 Server 解析；渲染层输入、Agent
名称、`receiver` 或接收方自报的授权检查都不能建立可信身份或授予权限。桌面不能通过隐藏按钮或先拉全量数据再过滤，
弥补后端授权缺失。

使用 Server 的当前 Principal 发现与受支持权限检查，解释哪些操作可用。它们仅辅助界面预检，每次读取正文、精确继续、
确认接收、Review 或其他修改仍须经过 Server 当前授权检查。具体包括：

- Scope 的组织关系不意味着权限继承或 Context 共享。
- Candidate 读取和 Review 修改分别遵守读取与审查权限。
- 获得某个已提交 Handoff revision 的授权，不等于能访问最新版本、相邻 revision、整个 Scope、报告或任意 Memory
  搜索。Evidence 遵守精确 citation manifest 及其授权规则。
- Skill 发布同时保留资源和发布权限要求。target 标识是操作参数，不是新的授权资源或归属证明。
- 集合、总数和搜索结果在 Repository 查询与分页之前完成授权过滤。如果无法安全过滤，明确失败，桌面不能回退到
  无限制列表后再本地过滤。

缓存项、不透明列表游标、选择和通知元数据，按连接端点、当前 Principal 或凭据 generation，以及查询条件隔离。
切换身份时清除旧私有状态，权限检查结果不能作为持久授权。凭据过期时停止受保护请求并提示重新认证；操作被拒绝时
保留独立说明。两者都不能触发跨连接自动复用凭据或无限后台重试。

## 8. Scope、资产与 Review 行为

项目和工作流视图使用已有的不透明 Scope ID 和组织关系。仓库路径、分支、会话 ID、Agent 名称或显示标签都不是
Scope 身份。Parent 组织关系不会产生传递性 Context reference、转移归属或发布 Artifact；跨 Scope 可见性和发布
使用各自明确的已有 API。

观察选择（`all`、`subtree`、`exact`）与写入或集成绑定的精确目标 Scope 分开。表单显示目标 Scope，提交时固定
该值；请求执行期间改变全局选择，不能重定向写操作。编辑绑定时显示受影响集成及其支持的选择语义，不假设所有宿主
行为相同。

Memory 搜索使用 Server 支持的模式和限制，缺少 embedding/generation 能力只禁用相关操作。UI 保留 Memory
citation 和精确 Artifact reference，区分待处理 Source、Candidate、已提交 Artifact 和已退役项。不能把已接收
的 Source 显示成已提取的 Memory，也不能把待 Review 的 Candidate 显示成已发布 Skill。

Review 复用 Candidate 的 expected-version 检查。冲突时重新加载权威 Candidate 并解释期间发生的变更，不静默
批准更新版本。受管 Skill 生命周期变更保留 generation 检查，包发布使用已 Review 的精确包。下载、查看或发布包
不授权桌面执行其中的脚本。

## 9. Handoff 发现、投递与操作

三个视图的用途不同：

| 视图 | 权威来源 | 含义 |
| --- | --- | --- |
| Handoff Report | 已有报告 API | 所选 Scope 及其最新精确 Handoff 的只读投影 |
| 与我共享 | RFC 1396 的授权资源发现 | 当前 Principal 有权访问的精确资源身份 |
| Handoff 收件箱 | #1419 投递契约 | 接收方的持久投递记录，以及契约支持的状态和恢复 |

授权列表的分页游标不是增量通知游标。授予访问权限不会投递 Handoff，也不代表未读。Candidate Review 和远程 Skill
receiver/reconciliation API 同样不能代替 Handoff 投递。桌面不另定义 envelope、接收端注册表、receipt 协议或
重试调度器。

#1419 负责方需要提供所有消费者共用的投递契约：版本化 envelope 和精确引用、可信接收方关联、持久列表与恢复、
去重身份、分页/事件游标语义、过期、取消，以及终态/可重试状态。桌面只消费这些不透明身份和受支持操作，并限定到
当前端点与 Principal。实现完成前可以提供报告和授权发现，但必须明确标注持久投递收件箱不可用。

打开条目时使用原始精确 `ArtifactReference`，重新检查当前权限和投递状态，不能替换为 `latest`。条目不存在、
过期、取消或权限撤销时，解释结果，不暴露缓存正文。只有某个精确 revision 的权限时，不能回退打开更广的 Scope 报告。

已有精确 Continue 和 Acknowledge 操作仍是权威语义。`accepted`、`needs_clarification`、`declined` 等 receipt
值保持原含义。接受需要接收方真实的 live-state、capability、authorization 观察；仅浏览桌面页面不能为另一个
Agent 的环境作保证。只有受支持流程能够提供这些检查时，桌面才提供确认接收，否则引导至能完成检查的集成。

维护中的 Agent 宿主如果支持打开精确条目，就使用它声明的集成机制，只传递其接受的有界精确选择。否则提供受支持的
复制/打开流程，不在 URL 中携带凭据或业务正文。桌面不虚构宿主 deep link，也不自行执行 Agent 任务。本地链接和
通知激活仅用于导航：校验连接与条目的关联，不自动执行修改。

查看、标记已读（若支持）、投递成功、授予访问权限、接收方接受 receipt、记录 Task Outcome 是不同动作。界面分别
命名；除非 Server 契约明确规定，不能把一种动作推进为另一种状态。

## 10. 通知与后台行为

Server 收件箱和 Candidate 状态是权威来源，系统通知只是尽力提示，不是持久队列，也不保证 exactly-once 投递。
初期只订阅或轮询活动连接。有受支持增量契约时使用该契约；否则 Review 状态可以采用有上限、带退避的轮询。
轮询 Candidate 列表只能得知当前待办，不能还原每个中间状态的完整历史。

通知消费要求如下：

- 从负责方的稳定条目/事件身份和适用的精确 revision 派生通知身份。只持久化有界去重元数据和不透明游标；桌面通知
  存储不保存 Memory、Source、Handoff、Prompt 或 Prepared Context 正文。
- 使用负责方定义的恢复、游标过期和缺口修复语义。如果只有当前状态列表，就刷新该状态并展示摘要，不虚构漏收事件，
  不改变分页游标含义。
- 按端点和 Principal 隔离元数据，凭据/身份变化时清理，并限制保留时间和容量。本地展示通知与 Server 标记已读、
  确认接收是分开的操作。
- 轮询支持抖动、退避、请求上限和取消；合并突发通知，抑制重复的离线/认证错误。凭据过期时停止受保护后台请求，
  提供一个有用的恢复提示。
- 默认使用“PowerContext 有事项需要处理”这样的通用提示，只携带批准的有界元数据和本地不透明导航句柄。包括锁屏
  场景在内，不显示正文、凭据、私有路径、敏感标题或未经处理的 Server 错误文本。
- 点击后激活应用，明确恢复对应连接，并重新授权精确项。失效或伪造的激活句柄不能静默切换凭据或执行操作。

首次使用通知时解释用途并请求系统许可。拒绝许可后，应用内数量和收件箱仍可使用。首版完整退出桌面后不会继续通知，
下次启动时恢复 Server 当前状态。没有托盘支持时，普通窗口导航和退出仍须可用。验证真实安装包的通知与冷启动激活，
不能只测试进程内 mock。

## 11. Source、连接器与集成

首先支持显式输入文本，以及通过系统文件选择器导入有大小限制的 UTF-8 文本文件。传输前显示目标连接、Scope、计划
使用的 Source 身份和大小。通过有边界的原生句柄读取所选文件，防止路径替换、目录穿越或链接变化后读取另一文件。
Server 的内容大小限制和校验仍生效，不能静默扫描目录或用户主目录。

远程连接通过公开内容摄取契约传送用户确认的字节，本地路径不是远程 Server 可以直接打开的位置。保留 Source 身份、
摘要和 provenance，避免不必要地泄露完整本地路径。重复导入遵守 Source 身份/冲突规则；不可变身份下内容发生变化，
不能被当成成功去重。

RFC 1400 的 Source 定义、观察和 checkpoint 不定义连接器发现、调度、provider 凭据或插件执行。首版连接器页面
仅使用 Server 已支持的元数据和操作。新增管理 API 由连接器/Server 负责，必须形成公开契约后才交付对应控制。
桌面关闭不能停止已接收的连接器任务；worker 凭据和 checkpoint 不能只存在桌面内。不完整抓取也不能被解释成未看到
的内容已删除。

Agent 方面使用 #1405/#1410 维护的分发模型和随发行版本提供的能力声明。已有 `integrations/capabilities.toml`
是仓库版本契约，不是实时公开 HTTP capability API 或接收端目录。界面分别展示：

1. 所选发行物声明自己在该宿主、平台和版本上支持什么。
2. 安装器记录了什么已安装内容，以及由谁管理。
3. 结构化诊断核验了哪些加载、连接、Scope 选择、capture 和 recall 行为。
4. 只有相应负责方提供事实时，才显示运行状态或接收端登记状态。

使用 `doctor integrations --json` 等真实结构化诊断接口，不解析人类文本、不通过文件或工具数量推断健康。未支持
或未观察的检查应明确标注。每个所选宿主由用户主动安装。配置合并、标准包身份、hook 行为和分发修复继续由原负责层
实现，Rust 宿主不能复制这些规则，也不能自动改写所有检测到的 Agent 配置。

## 12. 离线、重试与并发变更

首版没有持久本地业务缓存或离线写队列。远程离线视图隐藏私有内容，显示连接状态；可选择保留尚未提交的表单输入，
但仅在内存中存在，并清楚显示未保存。重连时先刷新兼容性、身份、授权和所选资源状态，再开放修改。本地 Server
可以继续提供自己的离线能力；模型需要网络时，桌面不承诺离线生成。

读取重试有上限并可取消。修改重试遵循各操作公开契约。如果 Server 支持幂等键，同一逻辑操作重用同一个键。
提交后超时意味着结果未知，不证明失败：重试前核验权威状态或提供检查入口。不能盲目重放 Review 批准、Handoff
receipt、导入、发布或安装。没有安全核验或幂等重试路径时，显示不确定状态，要求用户重新明确决定。

CLI、Agent、Web 和桌面并发修改都应正常工作。遵守已有 revision/version 检查，冲突时展示刷新后的条目，保留用户
意图，但不静默应用到新 revision。待完成 UI 操作携带原连接/身份 generation 和精确目标，迟到结果不得出现在其他
连接的页面上。

## 13. 分发、升级与恢复

首个 Windows 发行物使用签名的用户级安装包。本地 bootstrap 必须在未预装 Python、Rust、Node、Git 或编译器时
工作。安装负责方提供所选系统和架构对应、经过验证的解释器/runtime 环境，以及维护中的集成发行物。仅远程连接的
安装省略该 runtime。在线安装包和任何提供的离线包，都要声明包含哪些组件、仍需哪些网络访问。

当前服务实现会定位 Python 可执行文件，Windows 还要求相邻的 `pythonw.exe`，所以冻结后的 Python 可执行文件
不能直接替换现有 runtime。优先使用安装器管理的版本化 Python 环境；未来采用冻结 runtime 时，需要单独完成服务
兼容性和平台验收。Tauri sidecar 可以分发辅助程序，但不意味着 Server 生命周期由 Tauri 子进程接管。

发行计划分别记录桌面 UI/宿主版本、Python runtime 版本、API 契约版本、集成发行版本和持久数据兼容性。把便于人
理解的通道解析成不可变 manifest，记录精确发行物位置、摘要、OS/架构和兼容性。发行信任需要绑定可信发布者的签名
发行物或 manifest；仅从同一个不可信位置下载 checksum 不足以认证发行物。公钥和允许的更新源固定在任意渲染层或
远程 Server 响应之外。

Tauri 签名 updater 可以更新桌面组件，但不会协调 Python 环境、Agent 配置、服务注册和数据库迁移。这些组件的
共同计划属于统一安装器。桌面不能静默升级远程 Server，也不能自行覆盖被 Agent 共用的安装。

升级遵循以下规则：

1. 解析并展示兼容的不可变版本、受影响组件、中断时间、数据兼容性和恢复方式，检查空间、归属、核验所需凭据，以及
   是否有其他安装操作。
2. 先校验发行物，再暂存到现有版本旁边，保留上次验证过的安装记录。下载或签名失败不影响仍在运行的安装。
3. 切换 runtime 时，使用服务层支持的暂停处理、切换和校准路径。契约须明确进行中请求与持久任务如何处理，桌面不能
   等待一个猜测的超时后直接杀进程。
4. 完成就绪、兼容性和所选集成核验后，才将新安装记为健康。计划部分成功时分别报告组件结果。
5. 只有安装器声明可安全回滚时，才回退可执行文件/配置。数据迁移属于 Server/runtime 负责方；旧 runtime 不能打开
   不兼容的新数据。在不可逆迁移前，计划必须提供受支持备份/恢复或明确的向前修复路径，并由用户确认。
6. 中断后重新读取持久操作记录，核验不确定组件，通过原负责方恢复或修复，不能推断中断操作已经成功回滚。

不能临时复制正在使用的数据库文件充当备份。备份、暂停处理和恢复必须符合实际持久化后端。负责层尚未支持的 schema
迁移或备份 API，会阻塞相应自动升级路径，不能转由 Rust 实现。

默认 stable 通道；预发布需要主动选择和明确标识。切换通道不能绕过数据或 API 兼容检查。首版提供升级提醒和用户
明确执行的升级，不在活跃工作期间无人值守地升级 runtime。

## 14. 数据位置与卸载

| 数据或发行物 | 负责方 | 默认删除行为 |
| --- | --- | --- |
| 桌面可执行文件和打包 UI | 桌面包管理器/updater | 随应用卸载 |
| 连接、UI 偏好、有界通知元数据 | 桌面，位于独立用户级应用目录 | 可通过明确的重置选择删除 |
| 桌面凭据条目 | 系统凭据库 | 只删除所选连接/应用拥有的条目 |
| Python 环境、集成发行物、安装记录 | 统一安装器 | 仍有引用时保留，通过识别归属的计划删除 |
| 服务注册和受保护 Server 环境 | 已有服务/配置负责方 | 除非明确要求移除服务，否则保留 |
| Memory、Source、Artifact、调度状态、后端数据 | Server 持久化负责方 | 卸载应用或服务时默认保留 |
| Agent 宿主配置 | 分发/安装负责方与用户 | 只撤销自己拥有且记录过的修改，保留无关编辑 |

Server 的 `POWERCONTEXT_HOME` 或已有平台数据目录规则继续生效，版本化应用目录不能成为业务数据目录。本地
诊断页可以显示解析后的数据/日志位置，远程连接不能浏览 Server 文件系统。Rust 可以为用户打开已知本地位置，但
不能读取或修改业务数据库内容。

“移除桌面应用”“移除本地服务”“删除 PowerContext 数据”是分别命名的操作。首版自动卸载器不提供数据删除；未来
提供该 UI 时，需要负责方支持的明确流程，并确认精确的本地安装和数据路径。除非用户单独要求移除其他组件，否则
卸载桌面后，独立安装的 Server 和 Agent 仍须可用。归属未知或被用户修改的文件应保留并解释，不能递归删除。

## 15. 诊断、隐私与安全范围

诊断汇总桌面版本/平台、已核验的安装/组件状态、服务事实、连接失败类别、支持的契约版本、安全 request ID，以及
有界耗时/错误码。默认导出不包含 Memory、Source、Handoff、Prompt、Prepared Context、模型输出、凭据、
Authorization 头、原始环境变量、连接查询参数或私有绝对路径。不能直接附上任意 Server 错误正文或完整 CLI
stdout/stderr，必须经过脱敏诊断模型归一化。

导出是本地、显式、可预览的操作，由用户选择脱敏文件的保存位置，不要求自动上传或产品遥测。崩溃上报默认关闭；
未来的主动开启机制也不能以“无正文诊断”为名传送内存转储或原始请求正文。日志保留时间和大小均有上限。

威胁模型包含恶意 Server 内容、导入文件、伪造通知/deep-link 激活、本地网站尝试访问桥接层、向错误端点泄露凭据，
以及发行物被篡改。防护来自可信本地 UI、受限 IPC、按连接隔离的传输、Server 授权、系统凭据库、安全渲染和可信分发。
它不承诺抵御已被控制的操作系统、任意同用户恶意软件，或可访问用户进程和数据的管理员。

安全验收必须检查真实安装包的 capability/CSP、依赖权限、凭据库回退行为，以及发行物/升级验证。选择 Rust 本身
不能证明这些边界正确。

## 16. 平台、无障碍与本地化

| 平台 | 建议交付状态 | 需要验收的问题 |
| --- | --- | --- |
| Windows 11 x64 | 首个正式验收目标，SQLite 本地 runtime | 用户级签名安装、WebView2 可用性/bootstrap、Credential Manager、Task Scheduler 归属、安装后通知、非 ASCII 路径 |
| macOS | 后续验收 | 不同架构 runtime、Keychain、LaunchAgent、签名/notarization、WKWebView 行为、通知许可 |
| Linux | 后续按明确发行版/桌面环境矩阵验收 | WebKitGTK/系统库、Secret Service、systemd 用户会话、托盘差异、打包和通知激活 |

支持 Windows 不意味着支持 Windows ARM、Windows 上的 seekdb，或所有系统版本行为相同。Tauri 在三个目标上
编译成功不足以证明产品支持。每个对外声明的 OS/架构，都必须在所声明环境通过安装包验收。

英文和中文 UI/文档同步维护。提供键盘导航、可见焦点、无障碍名称、屏幕阅读器语义、不会破坏 IME 输入的表单、高
对比度，以及不只靠颜色表达的状态。通过滚动或响应式布局，管理流程在 800 × 600 窗口和 200% 缩放下仍可操作，
不能隐藏确认或恢复入口。保留用户选择的语言及系统主题偏好。

声明首个平台受支持前，在记录配置的参考机器上测量冷启动、空闲 CPU/唤醒次数、桌面与 runtime 的总内存、安装/下载
体积，以及列表/搜索响应。架构验证后、功能扩展前确定发行预算。比较应包含 WebView2/runtime 依赖和 Python
Server，不能把较小的 Rust 可执行文件宣传成整个产品的占用。

## 17. 交付顺序与依赖门槛

拆成范围明确的实现 PR，关联本 RFC 和 #1428。复用已有依赖任务，不再创建重复的桌面 Tracking Issue，也不把整个
产品塞进一个变更。

| 阶段 | 具体交付物 | 退出条件 |
| --- | --- | --- |
| P0：架构验证与契约 | Tauri 中打包可信共享页面；公开 API 传输；凭据适配器；Windows 安装后通知；连接/兼容设计；原型测量 | 确认 Windows 可行性和预算、安装/服务负责方的机器接口、公开 API 缺口、安全边界及发布负责人 |
| P1：个人预览 | 新旧本地安装、远程共享信任连接、服务状态/恢复、模型可选的首次 Memory 流程、所选 Agent 诊断、明确卸载行为 | 使用真实锁定发行物和当前支持的服务契约；不声称支持多用户资源共享或可靠 Handoff 投递 |
| P2：管理功能 | Scope/绑定视图、Memory/资产、Review、只读报告、显式 Source 导入、受支持连接器状态、双语无障碍、受保护诊断 | 保留公开 API 授权与版本冲突语义；准确展示受依赖限制的功能 |
| P3：授权协作 | 当前 Principal 资源发现、精确项权限、#1419 持久收件箱/恢复、受支持 Handoff 操作、有界通知 | RFC 1396 实现及 #1419 契约通过 Server 与桌面验收 |
| P4：首个平台正式发行 | 签名安装/升级发行物、恢复、完整首次使用流程、独立服务生命周期、保留数据的卸载 | 下列适用验收项在 Windows 11 x64 全部通过；发布兼容/支持矩阵并明确运维归属 |
| P5：增加平台 | 沿用相同边界的 macOS 和 Linux 包 | 每个声明支持的 OS/架构重复完成安装包验收 |

P0/P1 无需等所有协作功能就绪，但不能用桌面自造 bootstrap 冒充统一安装。如果 #1406 机器接口或 Windows
bootstrap 不可用，预览版必须明确为“仅连接”，不能声称通过安装验收。个人预览版本身不代表完成 #1428。

关闭 #1428 时，建议维护者至少要求：一个正式验收的 OS，issue 要求的完整本地安装和管理流程，授权远程访问，可靠
的 Server Handoff 收件箱消费，Review/Handoff 通知，以及文档规定的恢复和卸载保证。授权和投递负责方保留各自
测试与发布责任；桌面验收验证这些能力组合后的完整流程。

## 18. 验收与验证

以下是可观察的验收要求，不要求固定内部函数调用、模块布局或 UI 元素 ID。使用公开 API 契约测试和真实安装后的
桌面流程，只对宣称支持的平台要求相应平台测试。已有测试已保护的 Server 契约，应尽量复用。

| ID | 场景 | 必须观察到的结果 |
| --- | --- | --- |
| AC-01 | 无 Python/Node/Rust/Git 的干净环境首次安装，且不配置模型 | 核验本地 runtime/service 与所选维护中集成的安装；显式 Memory 保存和全文召回成功；依赖模型的操作说明条件 |
| AC-02 | 已有手工服务、过期受管定义、端口占用或其他服务注册 | 正确区分状态，保留归属未知的服务/数据，只提供受支持修复 |
| AC-03 | 关闭窗口、退出、重启桌面、重启系统用户会话 | 独立服务和已接收持久任务不因桌面退出而丢失；登录行为符合各自设置 |
| AC-04 | 下载/安装/升级中断，或签名/就绪检查失败 | 尽可能保留原可用状态；组件结果和不确定性明确；受支持恢复/回滚遵守数据兼容性 |
| AC-05 | 旧 runtime、不兼容 API、混合集成版本或 Server 身份变化 | 解释能力/兼容限制；不猜测支持、不静默改投目标、不自动升级远程 Server |
| AC-06 | loopback、非 loopback HTTP、错误 TLS、重定向、凭据过期和授权拒绝 | 保持现有传输策略，不跨端点转发凭据，各失败类别可区分 |
| AC-07 | 请求或通知未完成时切换连接/Principal | 旧身份的数据、游标、响应、凭据和修改目标不出现在新连接 |
| AC-08 | Principal 只能读某个精确 Handoff revision | 不泄露 latest/相邻 revision、未授权 evidence、更广报告或 Scope 内容；操作重新授权 |
| AC-09 | 受限制列表及 Review/发布权限 | Server 在分页/总数之前过滤；不安全过滤失败；隐藏按钮不能绕过授权；各权限对应操作正确 |
| AC-10 | Candidate/资产并发更新，或修改请求超时导致结果未知 | 显示版本冲突或未知结果；不静默批准新 revision，不盲目重复修改 |
| AC-11 | 断线期间收到 Handoff、游标过期、权限撤销或投递取消 | 按负责方语义恢复授权收件箱；保留原精确引用；导航不会自动确认接收 |
| AC-12 | 安装后通知、拒绝许可、突发事项、完整退出或过期激活句柄 | 有界无正文提示、去重合并、安全精确导航、应用内回退可用，并准确说明后台限制 |
| AC-13 | 文件导入、重复 Source 身份、字节变化、不完整抓取或摄取时关闭桌面 | 确认内容通过公开 API 到达所选 Scope；保留身份/冲突规则；不静默扩大导入、删除或由桌面运行 worker |
| AC-14 | 恶意 HTML/Markdown、任意 IPC 参数、伪造链接或错误连接端点 | 无任意执行/文件系统访问、凭据读回、特权远程导航或意外修改 |
| AC-15 | 在 token、路径、provider 配置、错误和业务正文中植入秘密测试标记 | 通知、普通日志、诊断导出、URL、渲染层持久存储和发行遥测中均无标记 |
| AC-16 | 移除桌面、移除服务或遇到用户修改的集成文件 | 默认保留数据；未单独移除的独立组件仍可用；保留未知/非己方文件 |
| AC-17 | 中英文、纯键盘、IME、屏幕阅读器、高对比度、小窗口和 200% 缩放 | 安装、连接、Review、通知导航、恢复和卸载选项仍易懂且可操作 |
| AC-18 | 在参考机器及声明的 OS/架构安装正式发行物 | 签名发行物和升级路径可用；完整占用和响应测量达到约定预算 |

修改公开契约的实现 PR 运行 `make api-generate` 和 `make contract-test`，保留正常 `make check`、相关行为
测试和严格文档检查。共享 UI 修改需要 Web 与桌面行为覆盖；桌面打包修改需要安装包冒烟测试，服务修改复用服务层
原生平台测试。仅有 mock 传输测试不能证明安装或系统通知合格。

# 缺点

这会给 Python 项目增加一个长期维护的原生应用、Rust 工具链、桌面 JavaScript 打包、签名发布流程和系统专项测试。
共享 UI 可以减少重复展示逻辑，但提取传输和构建边界仍需投入，也可能影响已有 Web UI。

Tauri 在不同系统使用不同 WebView，渲染、无障碍、认证集成和原生通知都需要逐平台验证。Python 及可选存储/模型
依赖可能占据大部分包体积和运行资源，减小桌面宿主后获得的实际收益未必很大。

独立 runtime 安装比单个可执行文件更复杂。它符合现有 Server 在桌面关闭后继续服务 Agent 的职责，但需要协调
兼容性和恢复。完整协作产品还依赖本 RFC 实现范围之外的授权和投递工作。

# 设计理由与替代方案

## Tauri 2 与 Electron

| 考虑项 | Tauri 2 | Electron | 对 PowerContext 的判断 |
| --- | --- | --- | --- |
| Web UI 复用 | 在系统 WebView 中运行 HTML/CSS/JavaScript | 在随包 Chromium 中运行 HTML/CSS/JavaScript | 两者都能复用管理 UI，都不要求重写业务代码 |
| 原生宿主 | Rust 宿主，显式授权 capability/插件 | Node.js 主进程，受限 preload/IPC | 范围较小的 Rust 宿主符合桌面职责和贡献者偏好 |
| 分发占用 | 复用系统 WebView，但有平台 bootstrap 依赖 | 自带 Chromium 和 Node.js | 倾向 Tauri，但测量必须包含 Python/WebView/runtime 的完整发行物 |
| 跨平台渲染 | 存在 WebView2、WKWebView、WebKitGTK 差异 | 自带 Chromium，相对一致 | 系统 WebView 无法满足必需无障碍/UI 行为时，Electron 更有优势 |
| Python 集成 | 外部 runtime 或辅助进程 | 外部 runtime 或辅助进程 | 两者都不解决 Python 安装、服务归属和业务 schema 迁移 |
| 凭据与升级 | 需要明确接入凭据库并设计组件升级 | 原生加密/升级能力仍需要策略和集成 | 两者都不替代系统凭据库验收、授权或安装契约 |
| 团队成本 | Rust/原生插件维护能力和平台验收 | JavaScript/TypeScript 生态与 Electron 经验 | P0 验证 Rust 维护和发布归属 |

选择 **Tauri 2**，因为产品是在既有 Python Server 和相对轻量的 Web 管理界面外增加原生控制能力，宿主可以保持
较小职责，不需要 Node.js 插件、自带浏览器引擎或桌面本地 AI 执行。Rust 用于系统集成和受限传输，不以性能为理由
重写 Python 业务逻辑。

如果 P0 发现系统 WebView 的无障碍/渲染、必需原生集成，或持续 Rust/平台维护存在实际阻塞，Electron 是备选。
切换时保持相同公开 API、安装、服务和授权边界。不并行维护两套正式桌面外壳，也不在比较完整安装原型之前声称某种
方案性能更好。

## 其他方案

- **只做 Web UI：** 继续支持，对远程管理成本最低，但无法完成原生安装/服务诊断、系统凭据保存、文件集成和安装后通知流程。
- **在有原生权限的外壳中直接加载 Server 页面：** 减少早期 UI 提取工作，但让安装依赖 Server 已可用，并把远程标记
  放到原生权限旁边，因此选择打包可信本地 UI。
- **让 Python 成为桌面子进程：** 可用于原型，但关闭/升级桌面不能中断 Agent 或持久工作，因此保留已有独立服务管理。
- **用 Rust 重写 Runtime/存储：** 重复成熟业务契约和迁移责任，当前桌面需求没有要求这样做，不属于本提案。
- **完全使用 Rust 原生控件：** 无法复用已有 Web 展示，又产生一套管理界面。只有明确证明共享 Web UI 无法满足需求时
  再考虑。

# 相关设计与参考

项目内基础包括 RFC
[1299](1299_local_server_availability_and_service_installation.md)、
[1345](1345_scope_organization_and_agent_integration.md)、
[1396](1396_handoff_access_control.md)、
[1400](1400_source_definition_and_observation_model.md)、
[1351](1351_standard_skill_package_lifecycle.md)，以及
[Server Web UI 开发指南](../development/server-web-ui.md)。前面的依赖表区分了已实现部分和开放中的安装、分发、
授权、投递工作。

以下官方资料用于判断框架和打包方案，其中的机制不能替代 PowerContext 的组件契约：

- [Tauri 架构](https://v2.tauri.app/concept/architecture/)与
  [WebView 版本](https://v2.tauri.app/reference/webview-versions/)说明宿主/UI 模型和平台引擎。
- [Tauri capability](https://v2.tauri.app/security/capabilities/)与
  [CSP](https://v2.tauri.app/security/csp/)用于设计受限原生桥接和可信打包 UI。
- [Tauri sidecar](https://v2.tauri.app/develop/sidecar/)、
  [updater](https://v2.tauri.app/plugin/updater/)和
  [通知](https://v2.tauri.app/plugin/notification/)提供组件机制，仍需验证生命周期和真实安装行为。
- [Tauri Windows 分发](https://v2.tauri.app/distribute/windows-installer/)、
  [macOS 签名](https://v2.tauri.app/distribute/sign/macos/)及
  [AppImage 分发](https://v2.tauri.app/distribute/appimage/)说明各系统不同的交付要求。
- [Tauri Stronghold](https://v2.tauri.app/plugin/stronghold/)描述 vault 能力；本提案单独选择系统凭据库适配器。
- [Electron 文档](https://www.electronjs.org/docs/latest/)、
  [安全指南](https://www.electronjs.org/docs/latest/tutorial/security)、
  [safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)与
  [autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater)用于评估替代方案。

# 待解决问题

在 RFC 审查或指定阶段，解决以下跨负责方决策，不把核心业务语义交给桌面：

1. **RFC 接受前：** 确认 Windows 11 x64 首个平台、Tauri 维护/发布负责人，以及个人预览和完成 #1428 的阶段区别。
2. **桌面管理安装前：** 确定安装/服务机器接口和 Windows bootstrap 时间。#1406 要求 shell 和 PowerShell 同为
   首批入口，而开放的 #1408 仍未确定引擎及 PowerShell 时序；本提案不选择安装引擎语言，也不虚构 CLI 参数。
3. **依赖兼容性的管理功能前：** 与 Server 负责方确定 `server-info` schema、契约版本策略、稳定 Server 身份的
   生命周期和支持窗口。
4. **协作版本发布前：** 与 #1419 确定投递/收件箱契约、集成启动及接收方检查，并完成 RFC 1396 实现的授权验收。
   不将授权列表分页视为事件重放。
5. **P0 结束时：** 公布实测性能预算、代码签名/更新密钥归属、发布 CI 环境及依赖/安全维护策略。这些是发布前提，
   不表示当前已经覆盖。

连接器管理面、更多认证方式、高级离线同步和广泛 Agent 执行属于其他设计，不能通过桌面私有协议掩盖这些能力缺失。

# 未来可能性

首个平台完成全部验收后，再增加 macOS/Linux 包、更多架构，以及用户明确开启的多连接后台通知。后续可以扩展
Source 导入格式、受支持的连接器配置，或在 Server 契约具备后接入系统浏览器认证。

离线写队列、本地业务缓存、更广的 Agent 操作或云同步都会引入新的一致性和安全责任，需要单独提案；采用本 RFC
不以它们为前提。

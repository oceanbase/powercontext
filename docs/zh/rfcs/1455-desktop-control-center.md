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

采用 **Tauri 2、由 Vite 构建并打包的 React + TypeScript UI** 构建 **PowerContext Desktop**，通过公开契约
管理连接、Scope、上下文资产、Review，以及受支持的本地安装/Handoff 流程。Rust 负责受限原生能力和携带凭据的
传输，独立 Python Server 负责业务语义、授权和持久化。复用个人 Dashboard 中适合共享的展示资源，同时保留独立
客户端管理入口。建议首个平台为 Windows 11 x64 + SQLite。

# 动机

用户应能在一个应用里确认 PowerContext 是否安装、Agent 是否使用正确 Scope、哪些内容需要审核、Handoff 发到
哪里，以及升级失败后如何恢复。原生凭据、文件选择、服务检查、托盘和通知构成桌面的价值。关闭桌面后，独立
Server 仍须继续服务 Agent。

产品面向新个人用户、连接已有安装的用户，以及认证团队 Server 的用户。它不是聊天客户端、IDE、Agent Runtime、
编排器或数据库副本，也不是 CLI/SDK/MCP 的前提。它不执行下载的 Skill，也不自动启动 Agent 任务。

目标是在一个验收过的平台上完成完整安装产品流程。可以先提供仅连接预览，再补齐受管安装和投递，但要明确展示
限制。接受本设计不等于完成 [#1428](https://github.com/oceanbase/powercontext/issues/1428)。实施阶段、任务
负责人、验收证据和调优工作由[交付计划](../development/desktop-delivery-plan.md)跟踪。

# 用户层说明（Guide-level explanation）

桌面是 Server 的控制中心。**连接**选择 Server 及其凭据，Server 解析实际采用权限的**身份（Principal）**。
**Scope** 标识正在查看或修改的上下文范围；切换浏览 Scope 不会改变 Agent 绑定。**Candidate** 是等待审核的提案，
Handoff **投递**则是面向获授权接收目标的持久记录。打开它们不等于批准提案或接受任务。

## 连接 Server

在尚未安装 PowerContext 的电脑上选择“这台电脑”。受管安装可用时，检查并确认安装器计划，包括发行版本、组件、
所选 Agent 宿主、位置和恢复方式。Python 未安装时，引导界面仍可使用。仅连接预览会解释限制，并提供受支持的安装指引。

已有 Server 的用户可以保存地址和凭据。例如，分别建立“个人”和“团队”连接，团队连接采用 HTTPS。只连接远程
不需要本地 Python。总览分别显示实际身份、授权模式、服务就绪状态和可用功能。Server 健康不代表所有功能已配置，
也不代表当前用户都有权使用。版本或登录方式不受支持时，界面解释原因和处理方法，不猜测兼容性或降级认证。

## 选择 Scope 并管理上下文

在个人连接中，选择显示名称为“文档”的精确 Scope，显式保存“指南采用英式拼写”，再在同一 Scope 通过全文搜索
召回。这条路径不需要模型配置，结果打开精确 citation。扩大浏览范围不会静默改变写入目标。

导入 UTF-8 笔记文件前，界面显示连接、精确 Scope 和去重规则。同一 Scope 再次导入相同文本，即使文件改名，也
复用 capture 身份；文本变化则产生另一 Source。“Source 已接收”不代表 Memory 提取完成。完整 Memory 浏览或
历史不可用时，页面说明限制并提供受支持的搜索/详情，不把截断的目录显示成完整结果。

所选 Agent 的绑定需要单独检查。浏览“文档”不会让 Agent 自动改为向该 Scope 保存。“已安装”“已观察到宿主加载”
和“已验证 capture/recall”是不同状态，未观察的检查保留为未验证。修改绑定是单独的授权操作，有明确的精确目标。

## 审核提案与查看 Handoff

打开待审 Profile Candidate，检查类型化提案和证据，再批准当前展示的版本。若另一客户端已经修订它，桌面报告
冲突并加载新状态，由用户重新决定，不自动批准替换版本。拒绝 Candidate 时必须填写理由。

即使无权列出所在 Scope，也可以打开获授权共享的精确 Handoff revision。共享和投递是不同概念：没有受支持的投递
契约时，桌面提供授权报告/共享项，不虚构收件箱。支持投递后，可以打开收件箱记录，查看接收目标和状态。浏览不会
代表 Agent 接受任务；接受必须经过受支持的接收流程，并提供真实接收端观察。

## 理解通知与不完整结果

Review 监控覆盖活动连接的当前精确 Scope。例如，只扫描了“文档”中的部分待审列表时，显示“已发现待办，扫描未
完成”，并说明覆盖范围和最近刷新时间。若显示数字，必须标为已观察数量，不能表示当前完整总数。未读页面不能算作
零，分页之间内容也可能变化；没有 Server 支持时，遍历完成本身不建立某一时刻的精确总数。

首次监控只展示摘要，不为每条历史记录发通知。后续提示合并发送，不包含私有标题或正文。点击提示会打开原上下文并
重新检查权限；已删除或撤权的项显示安全的不可用状态。拒绝系统通知后，仍可使用应用内视图。

## 切换连接、恢复与退出

从个人连接切换到团队连接，会清除个人连接的私有视图，必要时先确认丢弃未保存输入。迟到的个人搜索结果不会出现在
团队连接中。已提交的写入仍指向个人连接；响应丢失时先显示结果不确定，由受支持的恢复路径核实。断连不建立离线
写队列，重连后刷新身份和授权。

有托盘时，关闭最后一个窗口隐藏桌面，“退出”结束应用；没有托盘时，窗口明确说明关闭即退出。两者都不停止独立
Server 或已接收的持久工作。退出后停止通知，重新打开时刷新 Server 状态。桌面和 Server 的登录启动分别设置。

升级前显示受影响组件、中断和恢复计划。升级中断后，读取负责方的持久状态，不假设所有组件都成功，也不盲目重做。
“移除桌面”“移除本地服务”和“删除数据”分别命名；桌面/服务移除默认保留业务数据。诊断可预览后导出，不包含
私有正文或凭据。

## 页面与范围

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

# 技术层说明（Reference-level explanation）

前述连接和切换场景依赖兼容性、原生传输与身份隔离（第 4–7 节）；Scope、导入和审核行为由公开 API 及类型化
family 规则支持（第 3、8、11–12 节）；Handoff 与不完整通知覆盖分别具有权威状态和恢复边界（第 9–10 节）。
关闭、升级和移除桌面时，负责方归属与 Server 持久状态保持独立（第 6、13–15 节）。这些是架构契约，不依赖实施排期。

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

以下是向原负责方提出的要求，**不是已经实现的接口**。负责方先定义 schema 与语义，桌面功能才能依赖它们。
依赖不可用只禁用对应功能；人员安排、交付门槛和验收证据由交付计划维护。

| ID | 负责方与相关工作 | 所需契约 |
| --- | --- | --- |
| D1 | Server/API | `server-info`、部署身份生命周期、明确兼容配置 |
| D2 | Server/Memory | 授权且有界的 entry 列表；历史 UI 交付前的有界 change/history 查询 |
| D3 | 服务/配置，[RFC 1299](1299_local_server_availability_and_service_installation.md) | 非交互结构化修改、受保护输入、归属和恢复 |
| D4 | 安装器 [#1406](https://github.com/oceanbase/powercontext/issues/1406)、RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | 核验 bootstrap、计划、锁、持久操作/状态与恢复 |
| D5 | 分发 [#1405](https://github.com/oceanbase/powercontext/issues/1405)、RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | 不可变宿主发行物、兼容性和宿主负责的安装适配器 |
| D6 | 投递 [#1419](https://github.com/oceanbase/powercontext/issues/1419) | 接收方关联、envelope、持久收件箱、精确引用、去重和恢复 |
| D8 | Server/连接器负责方 | 若提供管理功能，需公开的连接器发现、健康与管理操作 |

本基线下 D4/D5 RFC 和 D6 Tracking Issue 仍开放。已有授权无需等待 D6，各消费操作独立授权。
Scope 集成绑定与 Access 角色绑定是分别命名的概念。

## 2. 组件与 UI 共享

```text
打包的桌面 UI -> 类型化 IPC -> Rust 宿主 -> 公开 HTTP API -> 独立 Python Server
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
原负责层。前端依赖和渲染成本在交付计划中评估。

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
Rust/JavaScript 路由目录。
下表权限列指出相关检查，不替代完整 Server 策略；组合证据、目标、发布和当前状态检查仍须执行。

| UI 行为 | 已有 operation ID | 授权/一致性 | 可用性/边界 |
| --- | --- | --- | --- |
| 连接 | `get_liveness`、`get_readiness`、`get_capabilities`、`get_access_principal` | 健康不等于身份；受保护调用遵守当前策略 | D1 握手 |
| Scope 目录 | `list_scopes`、`get_scope`、`get_default_scope`、`resolve_scope_selection` | 授权发现、不透明 cursor 和实际选择语义 | 已有契约 |
| Scope/绑定修改 | `create_scope`、`update_scope`、`set_scope_binding`、`clear_scope_binding`、`resolve_scope_binding` | 创建/admin 检查、已定义的预期版本、精确目标 | 不承诺全局绑定列表 |
| Memory | `remember_memory`、`search_memory`、`list_memory_entries`、`get_memory_entry`、`revise_memory_entry`、`retire_memory_entry`、`list_memory_changes` | 相应 Scope/资源检查、精确 citation | D2 完整浏览/历史 |
| Source | `list_sources`、`get_source`、`capture_content_source` | 授权 Scope/Source 访问、分页、不可变 capture 身份 | 已有契约 |
| Artifact 浏览 | `list_artifacts`、`get_artifact`、`get_artifact_revision`、`list_artifact_revisions` | Family 策略、精确 revision、ETag、不透明分页 | 已有契约 |
| Topic Memory 详情/搜索 | `get_topic_memory`、`search_topic_memory` | 专用选择/搜索限制和当前 family 策略 | 只读；不手工写入/flush |
| 标签 | `get_artifact_tags`、`replace_artifact_tags`、`get_memory_entry_tags`、`replace_memory_entry_tags`、`query_artifact_tags` | 受支持可打标签 family、精确逻辑目标、必需的标签状态 `If-Match` | 标签不改变内容 revision |
| Review | `list_artifact_candidates`、`get_artifact_candidate`、`approve_artifact_candidate`、`reject_artifact_candidate`、`revise_artifact_candidate` | 读取/审核和 proposal/证据检查；`expected_version` | 列表要求精确 Scope |
| Skill 生命周期/包 | `list_managed_skills`、`update_skill_lifecycle`、`get_skill_package_manifest`、`download_skill_package` | 资源检查、`expected_generation`、精确已审包 | 不执行下载代码 |
| 发布 | `publish_artifact`、`publish_remote_skill` | 共享/目标管理与发布策略；精确引用/generation | 仅验收过的目标 |
| 共享 | `get_access_principal`、`check_access`、`list_access_resources` | 当前 Principal、安全过滤、精确共享单位 | 不含角色管理 UI |
| Handoff | `get_handoff_report`、`continue_handoff`、`acknowledge_handoff`、`record_task_outcome` | 区分报告与精确证据/receipt 权限；接收方观察 | 已有读取；投递/接收流程依赖 D6 |
| 统计 | `get_stats` | 授权投影和支持的选择；缺失不是零 | 已有契约 |
| 本地管理 | 负责层机器接口；`service status --json`、`doctor integrations --json` | 核验本地归属、受保护配置 | 已有状态；修改依赖 D3/D4/D5 |

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
在个人切换到团队连接的例子中，generation 检查丢弃迟到的个人结果，同时保留已向个人连接提交的写入目标。

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

传输策略限制连接/读取等待和解码响应大小，Server 更严格限制优先。逐操作限制必须解释不支持或超限的结果，不能
静默截断。二进制包在原生侧按声明的导出上限流式处理并验证摘要，不经无限制 JSON/base64。超时和字节预算通过
实现及测量确定。长修改保留自己的恢复契约，超时不等于允许重放。错误只返回安全类别/代码/request ID，不返回
任意响应正文或 CLI stdout/stderr。

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
Profile 示例提交用户实际看到的版本，冲突后重读而不静默批准新版本。Skill 生命周期使用 `expected_generation`，
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
启动机制，否则提供受支持复制/打开，URL 不携带 token/正文。只有验收过的 sender/receiver 组合及版本才开放这些操作。

## 10. 通知与后台限制

前述不完整扫描场景以 Server Candidate 状态为权威；已实现的投递收件箱有自己的权威状态。通知是尽力提示，
不是队列或 exactly-once 保证。初期仅监控活动连接的**当前精确 Scope** 的 Review。all/subtree 浏览不订阅全部
Scope。托盘隐藏期间保留监控 Scope，并明确展示覆盖范围。

轮询必须限制并发、请求速率、响应大小和后台工作量。遵守 Server 分页契约：当前 Candidate 列表的 `limit` 范围
为 1–100，默认 50。这是服务端限制，不是选定的桌面页大小。响应只有 `candidates` 和 `next_cursor`，没有总数。
列表 cursor 不是事件 cursor，完整全局计数或事件历史需要单独的 Server 契约。

界面显示覆盖范围、刷新时间，以及遍历未完成、已完成或已过期。部分扫描可以报告本轮已观察到的项，不能冒充完整
总数，也不能将未读页面算作零。即使遍历完成，期间仍可能发生并发变化；没有快照/计数契约时，数量应标为已观察
数量，不是当前精确总数。遍历过期或 cursor 失效时，解释限制并按受支持语义刷新。调度策略要同时考虑积压推进与
新鲜度；反复从第一页开始不能静默暗示后续页面已被覆盖。后台限制不阻止用户显式分页审核。

首次启用或范围改变只展示摘要，不为每个历史 Candidate 发通知。后续观察在连接/Principal/Scope/Candidate
ID/version 上下文中去重，待审版本变化可以产生合并提示。认证拒绝后停止受保护轮询；临时失败退避并遵守 Server
要求的等待。手动重试不能形成无限后台循环。轮询间隔、页大小、抖动、遍历过期和退避数值由实现及测量确定。

D6 投递使用单独的有界消费者。只持久化不透明游标和去重/导航元数据，并限制容量与保留期。过期或淘汰的句柄安全
失败，元数据过期不把 Server 项标为已读，也不删除持久投递。容量和保留时长由实现及验证确定。身份变化清除私有
元数据，尽可能移除已发系统通知；残留通用提示不提供访问权限。

使用“PowerContext 有事项需要处理”这样的通用提示和本地不透明导航句柄，不显示正文、敏感标题、路径、token
或原始错误，包括锁屏。点击后明确恢复原连接并重新授权精确项；伪造句柄不能静默换凭据或修改状态。解释并请求
通知许可，拒绝后提供应用内回退，抑制重复离线错误。退出停止通知，重新打开刷新 Server 状态。应用退出后激活
已有提示，也遵守同样的授权规则。

## 11. Source 导入、连接器与 Agent 诊断

首版通过 `capture_content_source`（`POST /v1/sources/content`）导入输入文本或单个所选 UTF-8 文件。
不与分配新身份的通用 `create_source` 混用。读取系统选定句柄，不接受渲染层路径；防止替换/链接竞态、路径穿越
和未请求的目录扫描。

所有平台使用同一导入规则：

1. 读取前声明并执行有界文件读取预算。严格 UTF-8 解码，移除一个开头 BOM，保留其余 Unicode 码点、换行和
   空白。拒绝非法编码和空白内容。
2. 遵守已声明正文与传输/Server 限制，确认前解释限制。超限拒绝，不截断；客户端具体上限在实现与测量中确定。
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
特权管理员。隐私保证适用于全部可观察输出，包括失败信息和诊断导出。

## 16. 平台与无障碍

建议先验收 Windows 11 x64 + SQLite；当前源码基线的 Windows 支持仍为 experimental。不包含 Windows ARM 和
Windows 嵌入式 seekdb。macOS/Linux 只有完成安装后行为验收，才能声明支持。框架编译通过本身不能证明平台支持。

中英文 UI/文档同步，支持键盘、可见焦点、屏幕阅读器标签、IME 安全输入、高对比度和不只依赖颜色的状态。小窗口
及放大文字时，受支持操作、确认和恢复仍可使用。语言/主题变化保留身份及当前上下文。交付计划记录测试环境、
窗口/缩放场景，以及包括桌面、WebView 和 Server 在内的实测性能预算。

# 缺点

项目增加 Rust/原生维护、客户端 UI/构建、签名/升级和平台测试。共享展示减少部分重复，但不能省去客户端管理流程。
Python/WebView 可能占据大部分资源。独立 runtime 升级需要兼容和数据恢复；投递仍依赖另一负责方。

# 设计理由与替代方案

| 考虑项 | Tauri 2 | Electron | 判断 |
| --- | --- | --- | --- |
| Web 展示 | 系统 WebView | 自带 Chromium | 都支持客户端 UI，都不能直接移植 Jinja |
| 原生边界 | Rust 与应用/插件权限 | 主进程和受限 preload/IPC | 倾向小型 Rust 宿主，实际验证权限 |
| 体积/渲染 | 系统依赖与引擎差异 | 更大引擎、较一致渲染 | 测量完整安装产品 |
| Python/服务/数据 | 外部 runtime 与迁移 | 同样需要 | 都不替代安装器/服务契约 |
| 维护 | Rust/平台经验 | Electron/JavaScript 经验 | 需要可持续的维护/发行责任归属 |

选择 Tauri 2，并通过安装后平台验证确认可行性。Electron 用于实际 WebView/原生集成或持续维护阻塞时的备选，保留全部 API/归属
规则，不同时维护两套正式外壳。Web-only 仍有价值，但缺少所需原生能力。当前范围不支持特权远程页面、桌面持有
的 Server 子进程、Runtime 重写或完全原生的重复展示层。

不提供桌面时，现有 CLI 和 Dashboard 仍可使用，但用户需要借助不同工具协调原生凭据、服务安装、升级和通知。

# 相关先例（Prior art）

个人 Dashboard 提供服务端渲染、以读取为主的展示，不提供可直接移植的客户端管理流程。已接受的服务设计提供
独立 runtime 归属，Scope/授权/资产契约提供与 CLI 等客户端一致的业务语义。本提案将这些边界与受限原生宿主组合。
Tauri 和 Electron 提供外壳机制，不替代 Server 或安装器。

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

# 未决问题

接受 RFC 前，维护者需要确定首个平台（建议 Windows 11 x64 + SQLite）、Tauri/客户端边界与 Dashboard 展示层
共享方案、远程首版采用直连 HTTPS 和运维发放 Bearer，以及是否允许在受管安装和投递之前发布明确受限的仅连接预览。

负责方契约还需单独确定：D1 的兼容/版本支持与恢复/克隆部署身份，D3–D5 的机器管理、凭据配置和恢复，以及 D6 的
接收方关联、持久收件箱和已读状态。这些依赖阻塞对应功能，不阻塞无关的授权浏览；契约不可用时，桌面不能私自
建立替代接口。负责人、验收和测量由交付计划跟踪。

代理/浏览器登录、持久离线写入和更广 Agent 执行不在本提案范围内；支持它们需要单独的契约决策，不能静默扩大首版。

# 未来可能性

首个平台验收后，再增加 macOS/Linux、代理/浏览器身份适配器、用户明确选择的多 Scope/连接监控、更多导入格式和
有契约的连接器管理。持久离线内容/写入及更广 Agent 执行引入新一致性/安全责任，需要单独提案。

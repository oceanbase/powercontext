---
title: 易用性与 Agent 工作流
description: 安装维护、分步配置、场景 Skills 与本地 Client 的共同演进方向。
---

- Proposal Name: `usability_and_agent_workflows`
- Start Date: 2026-09-24
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1733](https://github.com/oceanbase/powercontext/pull/1733)

# Summary

PowerContext 的易用性建设围绕两条主线展开：降低安装与持续运行的成本，让 Agent 更容易理解和使用产品能力。安装从 Bash / PowerShell 引导脚本开始，独立安装/运维工具负责环境准备、首次配置与接入，以及后续维护。共享 Client 提供配置、连接和宿主公共操作，需要持续运行的能力逐步由本地 daemon 提供。安装维护 Skill 和功能 Skill 分别引导环境维护与产品使用，复用对应工具提供的操作。

本文定义组件职责和交互方式，作为各入口持续演进的共同技术方向。具体命令名称、平台适配、daemon 运行方式和分发格式由相关专项设计确定。

# Motivation

用户需要能够完成从安装、配置、接入 Agent 到首次使用的连续过程，并在后续使用中方便地诊断、维护和升级。这个过程应尽量减少对 Python 环境、部署方式、模型配置和产品内部概念的预先理解。

安装、配置和 Agent 接入之间存在需要用户手工衔接的步骤。服务地址、凭据、工作范围和能力状态需要在不同入口间保持一致；其中一个步骤完成，并不意味着用户已经能够使用产品。安装与配置入口应负责这些交接，并以完成连接和首次操作作为引导目标。

功能使用同样需要连续的引导。用户提出保存决定、接续工作、总结经验或创建 Skill 的意图后，Agent 应能够选择合适的能力，完成必要操作，并返回可以查看和继续使用的产物。

# Guide-level explanation

用户运行 Bash / PowerShell 安装脚本，或让 Agent 通过安装维护 Skill 发起安装。安装流程先确定使用本地服务还是连接已有服务，再准备环境、选择宿主和项目，完成连接。首次配置与宿主接入包含在安装流程中。

个人本地模式默认启用 Dashboard，无需 auth token，基础使用不要求配置服务端模型。安装器保存连接信息和项目绑定，用户无需手工传递 Token、导出全局环境变量或填写 Scope ID。已有服务的用户只配置本机 Client 和宿主集成。

使用引导按保存决定、查找上下文和接续工作等任务组织。安装完成后提供与已启用能力对应的场景示例，高级配置按需展开。

安装后，本地安装/运维入口负责状态检查、日志和进程管理；Client / daemon 提供后续连接配置、项目绑定和宿主接入。安装维护 Skill 调用这些操作完成用户请求。本地维护工具在 Server 或 daemon 未启动、不可用时仍能工作。

安装运维关系如下。Bash / PowerShell 脚本在首次安装时获取维护工具，用户和安装维护 Skill 后续直接调用本地入口。安装/运维工具分别管理分发资源、原生服务和首次配置；连接远端服务时不安装本地 Server。

```text
Bash / PowerShell (first install)
                 |
                 v
           Installer / Ops <---- User / powercontext-install Skill
                 |
                 +-- packages --------> Server / Client / Plugins
                 |
                 +-- service control -> Native service manager
                 |                          +-- Local Server
                 |                          `-- Client daemon
                 |
                 `-- initial setup
                      +-- Server CLI ------> Local Server settings
                      `-- Client operations -> Connections / Projects / Hosts
```

运行时调用关系如下。功能 Skill 按需指导宿主工作流，Client CLI 和宿主插件复用公共 Client 操作；需要持续运行的工作由 daemon 执行。原生 MCP 由宿主直接连接 Server。各分支中的 Server 均指配置的本机或远端服务，负责领域状态和产物。

```text
powercontext-project-context Skill
                  |
                guides
                  v
            Host / Plugins
                  |
                  +-- Native MCP ---------------------------> Server (MCP)
                  |
                  `-- Shared Client <---- Client CLI
                            |
                            +-- direct calls ---------------> Server (HTTP API)
                            |
                            `-- persistent work --> Client daemon
                                                        `--> Server (HTTP API)
```

# Reference-level explanation

## 组件职责

| 组件 | 职责 | 交互方式 |
| --- | --- | --- |
| Bash / PowerShell 引导脚本 | 检查平台，获取安装资源，启动安装流程 | 获取并调用独立安装/运维工具 |
| 安装/运维工具 | 准备依赖，组织首次配置与接入；安装、升级、修复、卸载、进程管理和诊断 | 调用包管理工具、原生服务适配层和 Client 公共操作 |
| 配置向导 | 收集使用方式、宿主和项目等必要输入 | 安装器和后续配置入口共用向导及配置操作 |
| Skills | 识别意图，选择并指导工作流 | 调用本地工具或宿主实际提供的产品接口 |
| 宿主适配层 / Plugins | 处理加载方式、事件、工具名称、连接、Scope binding 和权限交互 | 将公共工作流接入各宿主 |
| Client 工具（CLI / daemon） | 提供连接配置、项目绑定、宿主接入和客户端领域操作 | CLI、安装器和插件复用公共 Client 操作，常驻能力由 daemon 提供 |
| Server 工具（CLI / 服务） | 管理服务端配置，运行服务并执行领域操作，保存状态和产物 | CLI 提供配置与前台启动入口，运行中的服务通过 HTTP API 与 MCP 提供能力 |

Skills 描述流程，工具实现具体操作。安装/运维工具管理 Server 和 Client 进程，Client 提供配置与客户端执行能力，Server 负责领域状态。插件下载和版本选择由分发层负责。

## 命令与操作入口

命令按 Installer / Ops、Client 和 Server 三个工具组织，各工具拥有自己的命令入口。下图中的工具名和子命令用于表达归属与层级，可执行文件名称和具体参数由专项设计确定。

```text
Installer / Ops CLI
+-- install | upgrade | repair | uninstall
+-- status | doctor
+-- server
|   `-- start | stop | restart | logs
`-- client
    `-- start | stop | restart | logs

Client CLI
+-- config
|   `-- show | validate
+-- connection
|   `-- configure
+-- project
|   `-- bind
+-- host
|   `-- connect
+-- dashboard
|   `-- open
+-- memory | handoff | experience | skill
`-- daemon
    `-- run

Server CLI
+-- config
|   `-- show | validate
`-- run
```

Installer / Ops CLI 在安装后保留于本机，独立于 Server 和 daemon 运行。它负责软件生命周期、整体状态与诊断，并通过 `server` 和 `client` 子组选择受管理的本机进程。后续维护直接调用该工具，无需重新下载引导脚本。

Client CLI 负责客户端连接配置、项目绑定、宿主接入、打开 Dashboard 和领域操作。其 `config` 只处理客户端设置；`connection`、`project` 和 `host` 操作与安装器及 daemon 复用同一实现。`daemon run` 是 Client 常驻进程的前台运行接口。

Server CLI 负责服务端配置和前台运行，其 `config` 处理存储、模型、监听和访问设置。安装/运维工具通过原生服务管理器调用 Server CLI 的 `run` 或 Client CLI 的 `daemon run`，统一执行启停、重启和日志操作。

原 `powercontext setup`、`powercontext service` 和 `powercontext doctor` 的职责迁入上述工具，不保留旧命令或转发别名。

## 安装与维护

Bash / PowerShell 脚本负责检查平台、获取安装资源和启动安装流程。安装器自动准备缺失的运行依赖，使用隔离环境，并协调 Server、Client、宿主集成和配套 Skills 的兼容版本。下载来源和安装目标允许显式配置，保留用户已有选择。

首次配置由安装器组织：本地服务配置调用 Server 工具，连接、项目绑定和宿主接入调用 Client 公共操作。Client 操作供安装器和 daemon 复用。个人本地路径完成配置和服务启动；用户选择持续运行时，由安装/运维工具完成个人服务注册。后续增加宿主或切换服务通过 Client 配置入口完成，环境变更仍交由安装/运维工具执行。

程序、配置和用户数据分别管理。升级复用配置，卸载默认保留用户数据。程序版本回退与数据格式恢复分别处理。

公共流程负责环境检查、操作顺序和状态汇总，各平台适配层负责依赖准备和原生服务管理。Server 与 client daemon 分别由操作系统管理生命周期；远程连接只配置本机 Client 和插件，不隐式修改远程部署。

操作支持重复执行和中断恢复，并返回已完成项、失败原因和下一步动作。恢复时核对实际状态，保留用户配置和已有结果。安装维护 Skill 根据工具返回的状态选择后续步骤。环境检查、配置修复和原生服务管理不依赖运行中的 Server 或 daemon。

[EverMe 安装 Skill](https://everme.evermind.ai/SKILL.md) 通过 CLI 完成安装、宿主配置和诊断；[Lody CLI](https://lody.ai/docs/cli/) 保留不依赖运行中 daemon 的本地修复命令。这些做法支持维护操作独立可用，具体分发形式由安装架构确定。

## 分步配置向导

向导提供本地快速使用、连接已有服务和完整配置三条路径。用户先选择使用方式、宿主和项目，再根据实际需要展开模型、存储和高级配置。向导复用有效配置，并跳过已满足的条件。

新建个人本地配置采用以下默认值：

| 项目 | 默认行为 |
| --- | --- |
| Server | 仅监听本机 loopback 地址 |
| Dashboard | 默认启用，提供直接打开的入口 |
| 鉴权 | 不要求 auth token，Dashboard、HTTP API 和 MCP 使用一致的本地设置 |
| 存储与模型 | 使用 SQLite 和默认用户数据目录；基础保存与检索无需配置服务端模型，生成和向量能力按需启用 |
| 项目绑定 | 根据用户选择的项目，通过 Server 解析或创建 Scope，保存并复用绑定 |
| 宿主接入 | 自动保存连接配置，无需手工传递地址、Token 或 Scope ID |

本地 Dashboard 免 Token 的方向参考 [#1707](https://github.com/oceanbase/powercontext/pull/1707)。已有配置保留用户选择；远程连接沿用服务端鉴权要求。将本地服务改为局域网或远程访问时，需显式调整访问方式并配置鉴权。凭据通过受控输入或已有凭据引用提供，不写入操作进度或诊断结果。

```text
Choose local / remote -> Apply settings -> Select host / project -> Connect
```

交互式向导、安装维护 Skill 和自动化脚本共享配置与执行操作。操作支持显式参数和结构化结果，Agent 无需模拟长流程终端问答；机器可读输出与是否允许交互分别控制。

恢复时重新检查实际状态，并从未完成步骤继续。配置生成、环境变更、服务启动和宿主连接各自报告状态，避免将配置文件生成成功等同于可以使用。

## Skills 与场景工作流

首期提供两个可发现的 Skill 入口：

| Skill | 职责 | 分发方式 |
| --- | --- | --- |
| `powercontext-install` | 安装、配置、宿主接入、升级、诊断和修复 | 独立分发，可以在 Runtime 未安装或服务不可用时加载 |
| `powercontext-project-context` | 保存与检索、交接与接续、经验沉淀、Skill 创建与使用 | 随宿主集成提供，按需加载功能工作流 |

安装维护 Skill 统一调用引导脚本、本地运维工具和 Client 配置操作，按任务加载安装或维护流程。功能 Skill 沿用分层组织方式，按 Memory、Handoff、Experience 和 Skill 工作流加载参考内容。两个入口都使用简短描述，减少首次加载的上下文。

公共 Skills 描述任务目标、操作顺序和结果含义，宿主适配层决定这些流程如何被发现和触发。配置与使用说明应区分由 Hook 自动执行、由 Agent 根据指引选择执行，以及由用户主动发起的流程。各宿主根据实际支持的方式提供引导，加载了 Skill 并不代表具备自动执行能力。

[Nowledge 集成定义](https://github.com/nowledge-co/community)区分上述使用方式；[接入 Lody 时](https://mem.nowledge.co/integrations/lody)也优先连接其实际运行的 Agent。PowerContext 接入这类启动器时复用已有宿主集成，并在启动器提供额外能力时增加相应适配。

每个场景说明触发意图、前置能力、必要输入、操作步骤和结果。公共引导覆盖以下使用过程：

| 场景 | 工作流 | 返回结果 |
| --- | --- | --- |
| 保存与查找上下文 | 保存决定或约束，按需搜索和读取 | Memory citation 或检索结果 |
| 交接与接续 | 检查工作并准备交接；明确持久里程碑意图后 commit，接收方核对并接续 | 临时交接载体或精确 committed revision，以及接收状态 |
| 经验沉淀 | 选择材料，生成候选，按授权审查 | Experience 候选或已批准 revision |
| Skill 创建与使用 | 生成候选，按授权审查、导出或安装 | 候选、版本、导出位置或安装结果 |

```text
Intent -> Load workflow as needed -> Execute -> Verify result
```

工作流结合 Server 能力与当前宿主工具清单选择可用操作。需要其他 CLI/API 路径时，沿用已支持且已授权的调用方式；能力不足则说明未完成的步骤。场景描述不额外授予权限。

完成状态来自实际操作结果。准备、持久化、审查和安装分别报告；失败或结果未知时保留已有结果，先核对状态再重试。

## 分发与兼容

沿用 [#1691](https://github.com/oceanbase/powercontext/pull/1691) 的分工：公共 Skills 和工作流由统一基准生成，宿主适配保留加载、事件和权限差异，共享执行由已安装的 Client 提供。该提案中的 Python 调用和 JSON Lines worker 提供公共执行基础，独立常驻 daemon 作为后续工作设计。

共享 Client 提供配置、宿主接入和插件共同使用的客户端操作，需要持续运行的能力逐步由本地 daemon 提供。安装器、桌面、Client CLI 和插件复用这些能力，各入口保留自己的交互方式。安装/运维工具负责 Server 和 daemon 的环境、生命周期与诊断，Server 继续负责 Scope、领域状态和产物，宿主适配层负责事件、工作范围和权限交互。

[Lody](https://lody.ai/docs/quickstart/) 将多端界面与本机执行分开，桌面端既可以管理本机运行环境，也可以只连接其他机器。PowerContext 参考这一分工，在共享 Client 基础上设计常驻能力。

Server、Client 和插件按接口与能力要求选择兼容组合。复用已有 Client 能力的宿主规则和 Skills 可以独立更新，需要新增客户端能力的变更则声明相应版本要求。安装维护信息可在离线或 Server 不可用时读取，产品能力由 Server 与宿主的实际状态确定。

领域操作通过 Client CLI、HTTP API、MCP 和直接 SDK 调用提供，安装与运维使用 Installer / Ops CLI。个人模式调整默认鉴权配置，领域授权规则维持原有约束。本方案不改变领域对象和持久化格式；安装迁移、daemon 协议及具体场景的语义调整通过对应专项设计定义。

# Drawbacks

公共工作流、分发资源和宿主适配需要协调维护，流程恢复需要持续核对记录与实际环境。Client daemon 增加本地资源占用，以及安装、诊断和升级成本，其职责应由实际的持续运行需求决定。复用 Client 操作后，仍需维护不依赖 daemon 的本地诊断和修复能力。公共部分应聚焦稳定的操作和结果，保留宿主在发现、权限和交互上的差异。

# Rationale and alternatives

脚本入口让用户在 Runtime 未安装时开始安装。独立安装/运维工具统一管理本机进程和环境，在 Server 或 daemon 故障时仍能工作。安装器与 daemon 复用 Client 配置操作，使首次接入和后续配置保持一致。安装维护 Skill 覆盖环境生命周期，功能 Skill 按任务组织产品操作。

仅依靠文档会要求用户自行拼接步骤；把操作逻辑写进 Skills 会增加执行差异；在 Runtime 中集中安装职责会耦合环境维护与服务发布。组件分工使这些入口能够独立演进并复用已有能力。

# Prior art

- [安装架构提案](https://github.com/oceanbase/powercontext/pull/1408)与[脚本安装器](https://github.com/oceanbase/powercontext/pull/1529) 提供服务外安装维护的基础方向；[RFC 1299](1299_local_server_availability_and_service_installation.md) 提供个人 Server 原生服务管理基础，本方案统一其公开运维入口。
- [Agent Plugin 分发提案](https://github.com/oceanbase/powercontext/pull/1410)与[共享执行及分发工作](https://github.com/oceanbase/powercontext/pull/1691) 提供公共内容、Client 执行和宿主适配的分工，本地 daemon 沿这些边界继续设计。
- [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)展示独立安装入口与显式版本选择。

# Unresolved questions

各工具的可执行文件名称、具体参数、安装记录、平台支持和分发格式由安装与分发架构确定。Client 配置入口、daemon 的运行方式及其与插件的兼容约束，作为 #1691 后续工作继续设计。这些专项设计需要保持一致的操作和结果接口，以及独立可用的本地维护入口。

# Future possibilities

桌面或 Web 管理入口可以复用相同的状态与操作接口；新的功能场景可以复用公共 Skills 结构和宿主适配机制。需要常驻执行的客户端能力可以逐步接入 daemon。

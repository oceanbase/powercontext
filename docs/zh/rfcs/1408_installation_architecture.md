- Proposal Name: `installation_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#1408](https://github.com/oceanbase/powercontext/pull/1408)
- Related RFCs: [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

PowerContext 将由独立且归发行层所有的安装器负责个人发行安装。安装器根据一份不可变的 Release
Manifest，安装相互兼容的 PowerContext Runtime 和用户显式选择的 Agent 宿主集成。Runtime CLI 继续负责运行、
配置和诊断 PowerContext，但不再下载源码仓库、构建集成产物，也不再安装或更新宿主插件。

一次安装由三个输入解析而成：

```text
Release Manifest + Runtime Profile + selected Hosts = Installation Plan
```

安装器会预检完整计划，通过各集成专用的 adapter 安装组件，验证宿主可观察状态，并记录每个组件的精确结果。
重复执行相同计划必须幂等。某个宿主集成失败不会删除已经成功安装的 Runtime 或其他宿主，用户可以重复同一计划进行恢复。

现有 `powercontext setup` 命令组将先弃用、再移除。`powercontext config`、`powercontext doctor`、内容命令和
`powercontext server` 仍属于 Runtime CLI。本 RFC 不会隐式注册个人 Server 服务，也不会取代 RFC 1299 定义的
显式服务生命周期。

# Motivation

PowerContext 当前把仓库和实现布局暴露为正常安装流程的一部分。用户先从 Git ref 安装 Python 应用，再执行一个或多个
宿主专用 setup 命令：

```text
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
```

用户必须自行协调 Runtime requirement、Git source、Git ref、宿主选择、宿主前置条件和更新命令。更新 Runtime
不会自动更新集成。重复执行 setup 是目前记录的刷新方式，但它与已安装 Runtime 版本的关系只是一项用户约定，而不是
发行契约。

实现也存在同样的耦合。Runtime CLI 同时充当：

- 交互式宿主目录；
- Git source 解析器和 checkout 缓存；
- JavaScript 插件构建器；
- 宿主原生包管理器客户端；
- 文件系统安装器和配置合并器；
- 回滚协调器；
- 安装诊断界面。

这些责任散落并重复在多个宿主模块中。新增一个一等宿主需要同时修改目录、单宿主命令、多宿主分发、安装后校验、错误、
输出、测试和文档。有些宿主已经提供原生 marketplace 或包管理器，另一些仍通过复制仓库文件或在用户机器上构建源码
安装。把所有路径都视为 Runtime CLI 行为，会迫使公共 CLI 与发行拓扑同步演进。

安装、运行配置和诊断也有不同生命周期：

- 安装放置版本化组件并向宿主注册；
- 配置控制 Server URL、scope 和 capture policy 等可变值；
- 诊断只观察最终环境，不修改状态。

将三者合并会迫使用户为了修改配置而重跑安装器，也容易让诊断退化成隐式修复。PowerContext 需要先确立一个持久边界，
再继续增加宿主或安装模式。

目标结果是：

- 用一个入口获得可直接运行的个人安装；
- 用一个不可变兼容坐标约束 Runtime 与集成；
- 对交互和非交互场景都提供显式宿主选择；
- 正式插件不再从移动的仓库 checkout 现场构建；
- 提供带组件级结果的幂等重试和升级；
- 缩小 Runtime CLI，同时保留与安装无关的命令；
- 建立可扩展到其他平台入口、但不复制安装策略的发行契约。

# Guide-level explanation

## Installation Plan

安装器将三个用户选择转换成 Installation Plan。

**Release Manifest** 标识一个不可变 PowerContext release 以及与之兼容的集成产物。**Runtime Profile**
选择 PowerContext 应用角色和可选本地后端。初始 profile 为：

- `local`：包含 CLI、可直接运行的 Server 和默认 SQLite Runtime；
- `seekdb`：在通过认证的平台上为 `local` 增加嵌入式 seekDB 后端。

**Host Selection** 是需要安装 PowerContext 集成的 Agent 宿主显式集合，与 Runtime Profile 相互独立。
选择 `seekdb` 不隐含 Codex，选择 Codex 也不改变数据库后端。

解析后的计划包含精确版本与产物。`latest` 可以用于发现 release，但任何已安装状态发生变化之前，必须先解析为精确
release identity。

## 交互式安装

在受支持的 macOS 或 Linux 系统上，新用户从规范安装入口开始：

```text
curl -fsSL https://oceanbase.github.io/powercontext/install.sh | sh
```

bootstrap 在必要时安装 `uv`，然后启动独立 Installer Engine。安装器展示 Runtime Profile 和受支持宿主目录。
宿主检测可以显示 CLI 是否存在等可观察前置条件，但不能据此静默选择或忽略宿主。

修改状态前，安装器打印解析后的 release、Runtime Profile、选定宿主、目标目录和不受支持的选择。用户只确认一次
Installation Plan。安装器随后安装 Runtime、逐个安装所选集成并验证每个组件。

成功报告类似：

```text
Release       0.1.0
Runtime       installed  local
Codex         installed  powercontext 0.2.0
Claude Code   skipped
OpenCode      installed  powercontext-opencode 0.0.1
```

报告最后给出 `powercontext config init`、`powercontext server run` 和 `powercontext doctor` 等运行期下一步，
不会自动注册或启动持久个人服务。

## 非交互式安装

自动化场景显式选择每个变量：

```text
curl -fsSL https://oceanbase.github.io/powercontext/install.sh | sh -s -- \
  --version 0.1.0 \
  --profile local \
  --host codex \
  --host claude-code
```

全新安装且标准输入不是终端时，省略 Runtime Profile，或者既没有 `--host` 也没有显式 `--no-hosts` 都是错误。
对于现有安装，省略宿主参数会复用已记录的 Host Selection。安装器不根据 `PATH` 推断安装集合。结构化输出与人类可读
输出包含相同组件事实。

## 重复安装与升级

安装器在发行层拥有的安装根目录下，记录精确的成功 Installation Plan 和组件结果。如果所有组件均为 current，
重复相同计划成功且不产生语义变化。

选择更新 release 会生成新计划。其 Host Selection 从所有已成功记录的宿主开始，并加入任何新选择的宿主。安装器先预检
完整集合，再替换 Runtime，并把每个宿主收敛到同一 release 声明的产物。用户数据、Server 数据库和运行配置不属于
安装根目录，不会被替换。

后续命令未包含之前安装的宿主，不表示卸载该宿主。安装器绝不根据缺席推断删除。downgrade 必须显式指定版本；如果目标
manifest 没有声明兼容迁移路径，必须在修改状态前失败。

## 从部分失败中恢复

Runtime 与每个宿主集成都是独立安装事务。如果 Runtime 和 Codex 成功后 OpenClaw 失败，安装器报告：

```text
Runtime       current
Codex         current
OpenClaw      failed    host version is below the declared minimum
```

Runtime 和 Codex 保持可用。用户更新 OpenClaw 后重复相同命令；current 组件只需验证，OpenClaw adapter 重试自己的事务。

当多个宿主已经提交可观察状态时，安装器不能声称完整计划已回滚。如果任何所选组件为 unsupported 或 failed，进程以
非零状态退出，并保留恢复所需的组件级结果。

## 现有安装

独立安装器最初与 `powercontext setup` 共存。现有 Runtime 与宿主插件继续工作。在兼容期内，每个 setup 命令打印包含
等价 installer selection 的弃用信息，同时保留当前行为。

兼容期结束后，从 Runtime CLI 移除 `setup` command provider 及其安装实现。已有数据库、配置、marketplace、宿主包、
hooks 和 Skills 不会被删除。用户通过运行包含已有宿主的 Installation Plan 转移到 installer ownership；每个 adapter
必须先识别并验证兼容的 PowerContext-owned 状态，再进行收敛。

# Reference-level explanation

## Goals and non-goals

本 RFC 的目标是：

- 定义一个个人发行安装契约；
- 将安装所有权移出 Runtime CLI；
- 从同一个兼容 release 安装 Runtime 与宿主集成；
- 让安装可幂等、可恢复、可观察；
- 在宿主原生安装机制满足契约时优先复用；
- 为每个声明支持的平台和宿主组合定义 conformance 边界。

以下内容不是目标：

- 安装 Agent 宿主应用；
- 管理生产或特权部署；
- 修改集成运行协议或 PowerContext 数据契约；
- 自动安装、发布、批准或执行 managed Skill；
- 跨多个无关宿主包管理器提供原子事务；
- 替代 Python 应用对 PowerContext SDK role 的依赖管理；
- 修改 RFC 1299 已接受的显式个人服务生命周期。

## 责任边界

安装架构分为以下层次：

```text
Release Pipeline
    |
    v
Immutable Release Manifest
    |
    v
Bootstrap -> Installer Engine
                 |
                 +---- Runtime Environment
                 |
                 +---- Integration Adapters
                            |
                            v
                   Native Host Interfaces
```

| Concern | Owner | Required behavior |
| --- | --- | --- |
| 构建并发布版本化产物 | Release Pipeline | 产出内部兼容的 release 及其 digest |
| 获取 `uv` 并启动安装器 | Bootstrap | 保持精简，不包含宿主事务逻辑 |
| 解析、执行并记录计划 | Installer Engine | 强制执行 manifest、ownership、重试和结果契约 |
| 注册一个集成 | Integration Adapter | 使用宿主原生接口或受所有权保护的原子文件事务 |
| 运行并配置 PowerContext | Runtime CLI | 不修改自身发行状态或宿主 package 状态 |
| 观察已安装状态 | CLI diagnostics | 保持只读并独立报告事实 |
| 持久启动个人 Server | RFC 1299 service layer | 保持显式、opt-in，并与发行安装分离 |
| 运行托管部署 | Operator or orchestrator | 使用部署方拥有的打包、配置和生命周期 |

Installer Engine 是独立 release artifact。`powercontext server`、Client SDK、integration hook 和正常 CLI
启动均不导入它。Integration Adapter 是 installer extension，不是 Runtime CLI command provider。

## Release artifacts

通过个人安装认证的 release 包含：

- PowerContext wheel 和精确 Runtime requirement；
- 独立 Installer Engine；
- 一份 Release Manifest；
- manifest 声明支持的全部集成产物；
- 所有下载并执行或安装内容的加密 digest；
- 预检所需的兼容性和宿主最低版本元数据。

正式 release artifact 不得要求 checkout 仓库或在本地构建 JavaScript。原生 marketplace coordinate 只有在能够
解析到不可变版本时才是合格 artifact identity。`master` 或 `main` 等移动分支不是 release identity。

release pipeline 必须先发布全部产物，再发布可发现 release index。manifest 和必要产物通过 release verification
之前，安装器不能宣传该 release。

## Release Manifest contract

Release Manifest 发布后不可变，至少包含：

- `schema_version`；
- release name 与 package version；
- Installer Engine locator 与 digest；
- 各 profile 的 Runtime requirement；
- 支持的平台；
- integration identifier 与 display name；
- adapter kind 与 artifact locator；
- artifact version 和适用的 digest；
- host executable 与最低版本约束；
- 兼容或迁移约束。

Profile 与 integration identifier 使用稳定的小写 kebab-case。只能有一个 Runtime Profile 标记为交互式默认值；
Host Selection 没有隐式默认值。

不支持的 manifest schema、重复 identifier、不安全 package specification、缺失 digest 或不一致 release identity
必须在修改状态前令解析失败。只有 schema 明确标记为非规范性的未知元数据才可以忽略。

可变 channel index 可以将 `latest` 映射到 manifest URL 与 digest，但 Installation Plan 只记录精确 manifest identity，
绝不将 `latest` 记录为已安装状态。

## Bootstrap 与信任边界

shell bootstrap 只执行：

1. 验证参数与平台；
2. 获取或定位 `uv`；
3. 将 channel 或精确 release 解析为 manifest；
4. 下载并验证对应 Installer Engine；
5. 使用原始 installer 参数执行该 engine。

未来加入的 PowerShell 或 package-manager 入口必须启动同一个 Installer Engine，而不是重新实现策略。bootstrap
可以修改当前进程 `PATH`，但持久 shell 修改是 best-effort，必须与安装成功分别报告。

规范 bootstrap origin、channel index、Release Manifest 与 artifact host 共同构成安装信任边界。重定向到 manifest
allowlist 之外的 origin 必须失败。可执行内容运行或传给宿主包管理器之前必须验证 checksum。输出不得包含 credential、
完整环境或带认证信息的 artifact URL。

## 安装生命周期

Installer Engine 按以下阶段执行计划：

```text
resolve -> preflight -> install runtime -> install integrations -> verify -> record
```

`resolve` 只读并产生精确组件；`preflight` 只读并检查平台、宿主可用性、版本、路径、artifact 可达性、可提前发现的
ownership 冲突，以及能够可靠判断时的磁盘空间。

Runtime 安装先于集成提交，因为集成命令和诊断可能使用新 Runtime executable。此后每个集成作为独立有序事务执行。
验证读取与 `powercontext doctor` 相同的公共宿主状态，不能只相信 adapter 命令的退出码。

每个组件提交后原子写入状态记录。因此中断后可以区分 unattempted、uncertain 和 verified 组件。重试前先验证 uncertain
组件；不能仅因为上次进程未写入结果，就重复潜在破坏性操作。

## Runtime 环境与状态

发行层拥有一个每用户应用环境和一个 executable link。平台路径由 Installer Engine 统一解析，不在每个 bootstrap
中独立硬编码。Unix-like 系统上的预期布局为：

```text
<installation-root>/venv/
<installation-root>/installer/state.json
<user-executable-dir>/powercontext -> <installation-root>/venv/bin/powercontext
```

状态记录包含 manifest digest、release identity、Runtime Profile、成功组件 identity、后续 ownership 检查所需的
adapter result metadata 和最近 verification status，不包含 credential。

PowerContext 应用数据、Server 数据库、生成配置、日志和集成运行状态位于安装根目录之外。替换应用环境不得隐式删除或
迁移这些路径。

## Integration Adapter contract

每个声明支持的集成都提供以下逻辑操作：

```text
preflight -> install -> verify -> describe result
```

adapter 必须：

- 修改状态前验证宿主 executable 与支持版本；
- 只消费已解析 manifest 选择的产物；
- 优先使用原生 marketplace、package manager 或 plugin command；
- 替换文件或配置状态前验证 PowerContext ownership；
- 保证单宿主安装幂等；
- 事务提交前失败时，在宿主接口允许的范围内保留或恢复先前有效状态；
- 外部命令是否完成无法确认时返回 uncertain；
- 提交后通过可观察宿主状态验证；
- 提供不含敏感内容且可操作的失败信息。

宿主没有 package interface 时，adapter 可以把必要注册信息合并进配置。合并必须保留无关值、原子写入，并保留足够的
ownership metadata，以便区分后续 PowerContext 更新与外部条目。shell 文本替换不是合格的配置事务。

## 集成发行矩阵

初始目标架构为：

| Host | Distribution unit | Installation owner | Mutable configuration owner |
| --- | --- | --- | --- |
| Codex | Versioned marketplace plugin | Codex CLI | Plugin configuration |
| Claude Code | Versioned marketplace plugin | Claude Code CLI | Claude Code settings |
| DeepSeek Harness | Versioned package or release bundle | DSH CLI | Environment or DSH configuration |
| OpenClaw | Published package | OpenClaw CLI | OpenClaw configuration |
| OpenCode | Published package or release bundle | Native config or installer adapter | OpenCode configuration |
| Pi | Versioned Pi package | Pi CLI | Environment or Pi configuration |
| Hermes | Versioned package or release bundle | Hermes CLI or installer adapter | Hermes configuration |
| WorkBuddy | Versioned integration bundle | Installer adapter | WorkBuddy settings and MCP configuration |

该表是 release requirement，不表示每一行已经立即通过认证。某个集成只有在 distribution unit 与 adapter 通过
conformance 后才能出现在 Release Manifest 中。否则可以保留现有手工指南，但安装器不能将其声明为 supported。

## 配置边界

安装确保所选组件存在、属于 PowerContext 且能被宿主发现。运行配置决定组件如何连接和运行。Server URL、scope、capture
policy、模型 credential 和数据库配置不是 component identity。

安装器可以收集初始非敏感配置或调用宿主原生配置接口，但之后必须能在不重装组件的情况下修改同一组值。这些修改由
`powercontext config` 或宿主原生设置界面负责。安装器不得把调用者完整进程环境复制进宿主设置。

`powercontext doctor` 和宿主专用诊断保持只读。它们暴露 installer verification 所需事实，但不下载、修复、启用或
替换组件。

## Ownership、幂等与回滚

宿主原生 package-manager identity 是首选 ownership proof。直接写文件时，adapter 使用版本化 manifest，记录
PowerContext owner、integration identifier、release identity、artifact digest 和 owned file set。

adapter 可以替换 current 或 stale 的 PowerContext-owned 状态。除非未来用户显式选择 recovery operation，否则不得
覆盖外部 package、文件、目录、配置项或被本地修改的 owned artifact。名称相似不能证明 ownership。

Runtime 与每个集成都有独立 commit boundary。组件提交前失败时，在宿主接口允许的范围内恢复先前有效状态。外部 package
manager 提交后失败时保留该状态并报告 verification failure。安装器不能宣称无法观察或强制执行的跨宿主回滚。

重复安装相同 desired state 必须成功。当版本、digest、注册与验证均为 current 时可以避免写入，但幂等不表示跳过验证。

## 版本与升级

一个 Installation Plan 只包含一个 PowerContext release identity。即使各插件使用独立版本号，所有集成产物仍从该
release manifest 中选择。

升级会先解析并预检完整目标计划，再替换 Runtime。计划会继承所有已成功记录的宿主；宿主不会仅因用户省略 `--host`
参数就退出兼容集合。如果任何已记录或新选择的宿主与目标 release 不兼容，安装器必须在修改状态前停止。初始契约不提供
明知保留不兼容集成的部分 Runtime 升级。

downgrade 必须显式请求，且只有在目标 manifest 接受当前应用数据格式、每个所选集成都能安全收敛时才受支持。安装器
绝不降级或删除 Server 数据。数据格式迁移仍由 Runtime 所有，并需要独立兼容契约。

## 结果与退出契约

每个所选组件最终处于以下状态之一：

```text
unsupported | skipped | installed | current | stale | failed | uncertain
```

人类和结构化输出包含相同的 component identity、desired version、可用时的 observed version、state 和 recovery action。
`failed` 表示 adapter 已确认 desired state 未提交；`uncertain` 表示外部操作可能已经提交，但验证无法确认。下一次运行
必须先验证 uncertain 组件，再修改状态。

只有 Runtime 和所有所选组件均为 `installed` 或 `current` 时，安装器退出零。未被选择的 skipped 组件不影响退出状态；
所选组件为 unsupported、stale、failed 或 uncertain 时退出非零。

## 兼容与迁移

新安装器先发布，之后才移除 Runtime `setup`。在一个有明确期限的兼容窗口内：

- 现有 setup 命令保留行为；
- 每个 setup 命令输出弃用信息和等价 Host Selection；
- installer adapter 识别 setup 创建的兼容状态；
- 文档将独立安装器作为正常路径。

窗口结束后移除 `setup` CLI entry point、多宿主 setup dispatcher、Git checkout installer 和 setup-only result model。
只读诊断实现保留，并可迁移到不导入 installer code 的模块。

本 RFC 只在 RFC 1299 描述“setup 继续安装集成”这一当时假设的范围内取代它。本 RFC 保留 RFC 1299 对发行所有权和
Server 执行的分离，包括显式 `powercontext service install`、`status` 和 `uninstall` 生命周期。发行安装器可以推荐
service installation，但绝不隐式执行。

贡献者从 checkout 安装仍通过 `make install` 等仓库命令完成。Python 应用继续把 Client、builtin 或框架集成包加入
自己的项目环境。这两种流程都不经过个人发行安装器。

离线 customer bundle 可以提供自己的 bootstrap origin 和 manifest，但必须保持相同的 plan、ownership、verification
和 result contract。验证 bundle 完整性后无需网络访问。

## Conformance

只有在以下组合通过测试后，才能声明支持：

```text
Platform x Runtime Profile x Integration Adapter x Lifecycle Scenario
```

必须覆盖：

- clean interactive 和 non-interactive installation；
- 系统没有 Python 或 `uv` 时的安装；
- 精确 plan 重复执行；
- 从前一个受支持 release 升级；
- 声明支持时的显式 downgrade；
- 宿主 executable 缺失或过旧；
- artifact digest 或 manifest 验证失败；
- 每个 component commit 前后的中断；
- uncertain external command 恢复；
- foreign 或本地修改 artifact 冲突；
- Runtime 成功后的单集成失败；
- structured 与 human result 等价；
- 应用数据和无关宿主配置保持不变。

shell syntax test 和 PowerShell parser test 是必要条件，但不能单独认证平台。某个平台入口只有在对应操作系统上通过端到端
clean-install 与 upgrade job 后才能发布。

# Drawbacks

该方案新增 bootstrap、Installer Engine、manifest、integration artifact 和跨平台 conformance job 等发行面。
release publication 成为有序事务，不能只发布 Python wheel 就宣布版本完成。

将安装移出 Runtime CLI 不会消除所有宿主专用 adapter。WorkBuddy 和缺少充分原生 package interface 的宿主仍需要
带 ownership 的文件或配置事务。这些代码得到隔离，但维护工作仍然存在。

组件级 commit boundary 能让失败真实且可恢复，却无法提供单一全局事务的简单性。用户可能看到可用 Runtime 与失败集成
并存，并需要执行报告中的 recovery action。

不可变兼容 manifest 减少漂移，但也禁止随意混合来自不同 commit 的 Runtime 与插件。贡献者仍可使用源码工作流，正式
release 用户不再能把 `master` 当成受支持安装版本。

# Rationale and alternatives

## 保留 `powercontext setup`

该方案保留现有代码，并让已安装 CLI 复用 Python 完成复杂宿主事务；但发行、源码 checkout、宿主修改、配置和诊断仍混在
同一公共运行界面。增加入口而不改变 ownership，无法解决本 RFC 的根本问题。

## 用 shell installer 包装 `powercontext setup select`

这能快速提供一行安装体验，但 bootstrap 只是先安装 Runtime，再调用同一 setup graph。Runtime 与集成兼容性仍然只是
用户约定，现有 setup code 也仍是公共 API。它可以作为迁移步骤，但不是目标架构。

## 用 shell 和 PowerShell 实现全部安装逻辑

shell 适合 bootstrap，不适合 manifest validation、多文件配置事务、ownership proof、结构化状态和恢复。独立 shell
与 PowerShell 实现会复制最敏感的策略，并在平台间产生漂移。

## 将 Installer Engine 放入 Runtime CLI package

同一 wheel 中的独立模块可以改善内部结构，但 Runtime 仍负责分发自身 installer，并保留安装依赖和公共耦合。独立
release artifact 可以让安装拥有单独生命周期，同时用一份实现服务所有 bootstrap。

## 完全委托给宿主原生 package manager

Codex 和 Claude Code 接近该模型，但不是所有受支持宿主都提供充分的版本化 package、配置、验证或回滚接口。原生接口仍是
首选 adapter 机制，却不能替代共同的 Installation Plan 与结果契约。

## 独立 installer 与不可变 manifest

该设计增加一个发行组件，但建立了最清晰的 ownership boundary。它提供统一兼容坐标，隔离宿主修改，避免在平台 bootstrap
间复制策略，并让 Runtime CLI 围绕运行行为而不是发行机制演进。

# Prior art

Bub 的独立 installer 会 bootstrap `uv`、创建专用环境、解析版本化 preset catalog，并支持交互与非交互选择。它证明
插件选择可以发生在 Runtime 正常使用前，也证明 installer contract 可以通过 fake external command 测试。

Bub 仍调用 `bub install` 修改插件依赖，并允许跟随移动分支的 plugin coordinate。PowerContext 借鉴外部编排与 catalog，
但不继承 Runtime CLI ownership 或可变 release identity。PowerContext 面向异构宿主 package system，而不是一个共享
Python 环境，因此 Host Selection 与 Runtime Profile 保持独立。

Codex 与 Claude Code marketplace 展示了首选 adapter boundary：宿主负责插件注册和发现，PowerContext 提供版本化插件。
现有 PowerContext 文件安装器则说明，当宿主缺少该边界时，仍需要 ownership 与 rollback contract。

# Unresolved questions

- 首个公开 Release Manifest 除强制 digest 与 HTTPS origin 外，还需要哪种 signing 或 provenance 机制？
- 哪些宿主集成的 release artifact 足以进入第一份 manifest，哪些暂时保留手工安装文档？
- `powercontext setup` 兼容窗口多长，由哪个 release 移除？
- 初始 downgrade 契约是否只包含应用代码，还是必须先有 Runtime-owned data compatibility declaration？
- 哪些 Windows Runtime Profile 与宿主组合能够在公开 `install.ps1` 前通过 conformance？

# Future possibilities

release index 与 manifest 可以支持 signed provenance、artifact mirror、enterprise allowlist 和 policy-selected channel，
而不改变 Installation Plan。

Homebrew 或 WinGet 等 package-manager frontend 可以解析同一 manifest 并启动同一 Installer Engine，无需维护独立宿主语义。

未来的显式 repair 或 uninstall plan 可以复用已记录的 ownership 与组件结果。install 或 upgrade plan 中缺席的组件仍不隐含删除。

第三方集成未来可以通过 registry 提供独立签名的 adapter 与 artifact。这需要单独设计信任、兼容性和 extension API，
不由本 RFC 的一方 manifest schema 自动承诺。

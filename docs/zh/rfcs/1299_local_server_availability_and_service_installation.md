- Proposal Name: local_server_availability_and_service_installation
- Start Date: 2026-08-21
- RFC PR: [oceanbase/powercontext#1299](https://github.com/oceanbase/powercontext/pull/1299)
- Tracking Issue: [oceanbase/powercontext#1298](https://github.com/oceanbase/powercontext/issues/1298)

# Summary

PowerContext 将本地 Server 的运行职责与安装、部署生命周期分离。Server CLI 继续提供显式的前台命令
`powercontext server run`。Agent 集成仍然采用 fail-open，但必须让用户看见 Server 不可用；`powercontext doctor`
则负责解释已安装的服务注册、原生服务管理器、Server liveness 和 Server readiness 是否一致。

对于个人安装，可选的 distribution 所有的 service-install 层会把现有前台 Server 命令注册到操作系统原生的
当前用户服务管理器中。它不属于 Server CLI，不会在 setup 期间自行启用，不要求管理员权限，也不会创建第二个
PowerContext supervisor。托管部署继续使用容器或由管理员管理的系统服务。本 RFC 为 Linux、macOS 和 Windows
定义统一契约，同时允许接受后的实现拆成可独立评审的变更。

# Motivation

Agent 集成依赖可访问的 PowerContext Server，但不拥有其进程生命周期。正常的本地入口有意保持为前台进程：

```text
powercontext server run
```

这一默认方式可观察、可撤销，但终端关闭或机器重启后进程就会消失。由于集成采用 fail-open，后续 Agent 会话仍可继续，
但 recall 和 capture 会被跳过。现有结构化诊断可能写入日志或 stderr，用户仍可能把该故障体验为 PowerContext 行为的
静默丢失。

把登录后自动启动直接放在 `powercontext server` 下会混合两种职责。Server package 负责如何运行一个已配置进程；
安装和 distribution 负责操作系统是否应该持久地启动该进程。这一区分也分开了两种部署模式：

- 个人安装可以使用当前用户拥有的原生服务。
- 托管部署应使用容器或由管理员管理的系统服务，并采用部署专属的配置、凭据、健康检查和重启策略。

PowerContext 在实现前需要为这条边界形成持久设计。它还需要让用户能够区分：未安装服务、原生服务未运行、
Server 不可访问，以及 Server 已存活但 Runtime 未就绪。

# Guide-level explanation

## Deployment profiles

PowerContext 为 Server 记录三种运行方式。

### Interactive personal use

现有命令继续作为默认方式，并保持当前行为：

```text
powercontext server run
```

它在前台运行，把日志输出到终端，并在 `Ctrl-C` 后停止。安装 PowerContext 或 Agent 集成都不会创建持久的操作系统状态。

### Persistent personal use

同时安装 CLI 和 ready-to-run Server role 的用户，可以显式安装当前用户服务：

```text
powercontext service install
powercontext service status
```

公开的生命周期命令组确定为 `powercontext service`。它不属于 `powercontext server`，只管理当前操作系统用户下
唯一一个由 PowerContext 拥有的个人 Server 注册。初始契约不提供具名 service profile。默认不安装，且不需要
`root`、`SYSTEM` 或管理员账号。

个人服务只接受 loopback Server bind。它的 endpoint 来自注册所使用的本地 Server settings，而不是 Client 的
`server_url` 或远程 `powercontext doctor --server-url` 目标。其他本地实例仍以前台进程运行；共享、远程寻址或公开
绑定的 Server 属于托管部署。

原生注册调用 distribution 所有的内部 launcher。完成防重复 preflight 后，launcher 会在同一个 manager-owned process
中把控制权交给前台入口使用的同一个 Server runner；它不会作为第二个 daemon 或 supervisor 留存：

```text
powercontext server run
```

移除注册使用：

```text
powercontext service uninstall
```

卸载注册会停止由该注册拥有的 Server 实例，并只删除 PowerContext 创建的产物。它不会终止一个无关的前台 Server。

### Managed deployment

个人服务安装器不是生产部署管理器。托管安装使用项目的容器镜像，或者由管理员管理的 `systemd` system unit、
launch daemon、Windows service 或等价的 orchestrator。PowerContext 不通过 `powercontext service` 安装这些
高权限资源。

Client 和 integration settings 可以继续指向这类远程 Server。个人服务命令既不注册也不修改远程 endpoint，诊断也不得
为远程 endpoint 推荐安装本地服务。

## What users see when the Server is unavailable

Agent 集成继续 fail-open：recall 或 capture 失败不能阻塞宿主任务。但集成必须通过宿主的 warning 或 diagnostic
通道暴露不含内容的 `server_unavailable` 诊断。集成不得从 prompt hook 尝试安装、启动或重启 Server。

该提示引导用户运行：

```text
powercontext doctor
```

Doctor 分别报告事实，而不是把它们压缩为一个连接错误：

- 是否存在个人服务注册；
- 原生服务管理器是否报告该服务 active；
- 配置的 Server endpoint 是否 live；
- 已存活的 Server 是否 ready；
- 与观测状态相符的恢复动作。

示例：

```text
service_registration  ok             not_installed (optional)
server_liveness       failed         run powercontext server run, or install the personal service
server_readiness      skipped        not checked because Server liveness failed
```

以及：

```text
service_registration  installed
service_manager       inactive       inspect the native user-service logs
server_liveness       failed         registered service did not become reachable
server_readiness      skipped        not checked because Server liveness failed
```

`powercontext service status` 的范围比 `powercontext doctor` 更窄。它报告唯一的本地注册、原生 manager 状态、
definition drift、本地 Server liveness 和日志位置，但不替代 Server readiness 诊断。远程诊断目标只进行 liveness 和
readiness 检查，不关联本地个人服务。

# Reference-level explanation

## Responsibility boundaries

接受后的设计为每项关注点分配一个所有者：

| 关注点 | 所有者 | 必需行为 |
| --- | --- | --- |
| 构造并运行 ASGI 进程 | Server role | `powercontext server run` 保持前台且可独立使用 |
| 在 Agent 任务期间 recall 和 capture | Integration | fail-open，暴露 Server 不可用，但不拥有生命周期 |
| 诊断已安装环境 | CLI diagnostics | 关联注册、manager、liveness 和 readiness 状态 |
| 注册个人后台进程 | Distribution/service-install layer | 管理唯一的原生当前用户产物并调用内部 launcher |
| 运行托管部署 | Operator or orchestrator | 使用容器或由管理员管理的系统服务 |

任何集成都不导入平台服务 adapter。任何平台服务 adapter 都不属于 `src/powercontext/server`，也不改变 Server
应用启动。Distribution 层通过顶层 `powercontext service` 命令组暴露用户契约，但命令入口不会把职责转移给
Server role。

命令 provider 只在 CLI 与 ready-to-run Server role 同时安装时可用，与文档中的
`powercontext[cli,server]` 个人安装方式一致。只安装 Client 时不暴露本地 service lifecycle 命令。

## CLI contract

初始 distribution 契约为：

```text
powercontext service install
powercontext service uninstall
powercontext service status
```

这些命令只操作当前用户唯一的个人服务，不提供具名 profile，也不存在 `--system`、`--machine`、`--root` 或
管理员安装模式。它们从本地 `ServerSettings` 推导 endpoint；根命令的 Client `--server-url` 选项和
`ClientSettings.server_url` 都不会选择 service target。

### Install

Install 执行以下步骤：

1. 确认 ready-to-run Server role 和经过验证的原生当前用户服务 adapter 可用。
2. 加载本地 Server settings、推导目标 endpoint，并拒绝 non-loopback bind。
3. 解析 distribution 所有的内部 launcher 对应的绝对、非 shell 命令。
4. 探测目标 endpoint：有效的 PowerContext liveness 响应会跳过立即启动；端口被占用但响应无效时，作为冲突在改变
   原生状态前失败。
5. 渲染并验证包含固定 ownership marker、package version、definition version、目标 endpoint 和 launcher command
   的注册产物。
6. 只创建或更新 PowerContext 的个人 Server 注册，并为后续用户登录启用。
7. 除非第 4 步发现 PowerContext Server 已 live，否则默认立即启动。
8. 操作完成后报告 registration、definition、原生 manager、liveness 和 log location 事实。

使用相同目标定义重复安装应成功，且不产生语义变化。如果 PowerContext 拥有的定义已过期，则在原生 manager 支持时
原子 reconcile。如果 manager-owned service 处于 active 且 executable 或 definition 已变化，reconcile 会执行受控
重启。它绝不因为前台 Server 占用了目标 endpoint 就终止该 Server。

Preflight、artifact replacement 和 enablement 构成注册事务。事务提交前失败时，回滚新写入的 PowerContext artifact。
立即启动发生在提交之后：如果启动失败，有效且已启用的注册会保留，命令以非零状态退出，并报告 manager failure、
unreachable endpoint 和 log location。再次运行 `powercontext service install` 会 reconcile 并重试这一状态。
命令绝不覆盖 display name 相似的外部 unit、task 或 launch agent。

内部 launcher 在每次原生启动时（包括用户登录后）重复 endpoint preflight。目标 endpoint 已提供 PowerContext liveness
contract 时，它不创建进程并成功退出；地址被其他服务占用时，它报告冲突；其他情况下把控制权交给
`powercontext server run` 使用的 runner。该 launcher 只是一次性 guard，不是长期运行的 supervisor。

### Uninstall

Uninstall 首先要求原生 manager 停止 manager-owned process。如果停止失败，它保留注册并以非零状态退出，避免在进程
可能仍在运行时删除 ownership metadata。停止成功后，再禁用并删除经过验证的 PowerContext 注册。它不会仅仅因为某个
进程监听目标端口就终止它。注册不存在时重复卸载应成功。

如果删除不完整，命令会报告剩余产物和原生恢复命令。清理范围绝不能扩大到目录、任意 task name 或未经验证的 process
identifier。

### Status

Status 是只读操作，同时提供人类可读和 JSON 输出。其稳定状态模型包括：

```text
support: supported | unsupported
registration: installed | not_installed | invalid | unknown
definition: current | stale | missing_executable | unknown
manager: active | inactive | failed | unknown
server_liveness: live | unreachable | unknown
log_location: <native journal selector or per-user path> | unavailable
```

Registration 表示精确的 PowerContext-owned 原生产物状态；Definition 比较记录的 executable、package version 和
definition version 与当前 distribution；Manager state 来自原生 manager；Liveness 探测注册中记录的 loopback
endpoint。这些值有意保持独立：没有注册时前台 Server 也可能 live，存在注册时 Server 也可能 unreachable。

人类可读和 JSON 输出携带相同的事实与恢复动作。只有 support 可用、registration 已安装、definition 为 current、
manager active 且 Server live 时，退出状态才为零；其他组合均以非零状态退出，但不得隐藏各项独立事实。输出不得包含
凭据、完整 process environment 或无关的原生服务 metadata。

## Native personal-service adapters

以下内容是规范性的 adapter 设计，不代表每个 release 都已经支持全部平台。只有 adapter 已交付，并在对应操作系统
runner 上通过基础 CLI/Server smoke test 和原生 lifecycle test 后，它才能报告 `supported`；此前一律报告
`unsupported`。

每个通过验证的 adapter 都注册当前用户，使用无 shell 的绝对命令调用同一个内部 launcher，使用唯一且固定的项目原生
identifier，并通过 `powercontext service status` 和 `powercontext doctor` 暴露具体日志位置。

### Linux

Linux adapter 使用 `systemd --user`。它在用户的 systemd 配置目录下拥有一个 unit，并通过 user service manager
完成 enable、start、stop、status 和日志操作。日志进入 user journal，status 返回精确的 journal selector。它绝不
写入 `/etc/systemd/system`，也绝不启用 linger。没有可用 user `systemd` manager 的 Linux 环境报告
`unsupported`，且不会静默回退到 shell startup file 或桌面专属 autostart。

### macOS

macOS adapter 使用用户 `Library/LaunchAgents` 目录下的 per-user `LaunchAgent`。它使用 `launchd` 当前用户
domain，配置明确的 PowerContext-owned 当前用户 stdout 和 stderr 路径，绝不创建 `LaunchDaemon` 或
privileged helper。

### Windows

Windows adapter 使用在当前用户登录时触发的 `Task Scheduler` task。它以该用户身份运行，绝不使用 `SYSTEM`。
允许隐藏 process window。由于 Task Scheduler history 不是 Server stdout 或 stderr，launcher 会把 Server 输出重定向
到明确的 PowerContext-owned 当前用户日志文件。该 adapter 不安装 Windows Service。

原生 identifier 和 path 是一个服务对应一组固定的项目常量。每个产物都包含稳定的 ownership marker 和 definition
version，使 status 和 uninstall 在兼容的 package rename 后仍能区分 PowerContext-owned definition 与外部资源。
具体平台字符串属于 adapter 常量，并由 rendering 和 ownership test 覆盖。

## Configuration and credentials

服务安装器记录 executable、必需参数和不敏感的 service metadata。它不会把调用者的完整 environment、shell profile、
API key、bearer token 或 provider credential 复制进原生注册产物。

因此，初始个人服务模式依赖原生当前用户服务环境中可获得的配置。`powercontext service status` 和
`powercontext doctor` 可以报告可观测的配置偏差，但不会读取或输出 secret。需要超出原生当前用户服务契约的凭据
注入或环境管理的部署，仍由 operator 管理。

本 RFC 不引入 portable credential store、environment snapshot 或跨平台 secret-file 格式。File-backed settings
和 secrets 仍是独立关注点，沿用项目现有的 `pydantic-settings` 方向处理；它们不是本 service lifecycle 提案进入
general availability 的前置条件。

## Integration availability signal

每种集成都有自己的宿主执行模型，因此展示机制由 adapter 决定。共同语义契约为：

- transport failure、timeout 或 HTTP 503 映射为 `server_unavailable`；
- recall 和 capture 继续彼此独立地 fail-open；
- 诊断不包含 prompt、recalled content、token 或 credential；
- 宿主任务继续，且不注入 PowerContext content；
- 集成提供可发现的 `powercontext doctor` 路径；
- 重复不可用时，使用适合宿主且有界或去重的展示，不能永久地在每个 prompt 上重复 warning；
- hook 绝不启动或安装 Server。

Authentication failure、version mismatch、invalid response、成功的 empty result 和 Server unavailability 保持为
不同 outcome。每个受支持的 integration 都必须通过 acceptance test 或记录的 host fixture，证明选定通道对用户可见。
只有宿主确实暴露 stderr 时，向 stderr 写结构化 JSON 才满足要求。实现会保留不含内容的结构化诊断用于
troubleshooting；具体可见通道和去重机制由 host adapter 选择。

## Doctor diagnostics

`powercontext doctor` 继续作为 installed-environment 的权威诊断，并保留现有
`ok | degraded | failed | skipped` vocabulary。当目标是 loopback 且不存在注册，或目标与已有个人注册中记录的
endpoint 一致时，新增以下本地 service check：

| 检查 | 含义 |
| --- | --- |
| `service_support` | 经过验证的原生个人服务 adapter 可用 |
| `service_registration` | 精确的 PowerContext-owned 注册状态 |
| `service_definition` | executable、package version 和 definition version 与当前 distribution 一致 |
| `service_manager` | 原生 manager 对该注册报告的状态 |
| `server_liveness` | 所选 endpoint 满足现有 liveness contract |
| `server_readiness` | live Server 报告 ready、degraded 或 not ready |

Doctor 不从开放端口推断 registration，也不从 manager process identifier 推断 liveness。当事实不一致时，detail 会指出
差异并给出下一项安全动作。JSON 诊断保留与人类可读输出相同的 check name 和 status vocabulary。

个人服务安装是可选项，因此在不存在注册时，`unsupported` 和 `not_installed` 只是 advisory，不会让 doctor 的整体
结果变为非零。人类可读和 JSON detail 仍暴露这些事实；例如 `service_registration` 可以是 `ok`，detail 为
`not_installed (optional)`。存在注册前不输出 definition 和 manager check。一旦存在 PowerContext 注册，invalid 或
stale definition、原生 manager failure，或目标 Server unreachable 会按照现有诊断规则产生 `degraded` 或
`failed`。

对于远程目标，或与注册 endpoint 不同的 loopback 目标，doctor 只做 endpoint liveness 和 readiness 诊断，不关联本地
registration 或 manager，也不得为远程或 operator-managed Server 推荐 `powercontext service install`。

## Upgrade and executable drift

注册指向解析后的绝对内部 launcher，而不是 shell alias，并记录 package version 和 definition version。Status 验证
executable 是否仍然存在；已安装 distribution 不再匹配时，报告 `stale` 或 `missing_executable`。

更新 Python distribution 不会静默改写操作系统状态。再次运行 `powercontext service install` 会让注册与当前安装的
distribution 保持一致，并且只在 active manager-owned process 必须采用新 definition 时执行受控重启。在 distribution
拥有事务化 upgrade hook 之前，文档必须包含这一 reconciliation 步骤。

## Failure handling and observability

原生启动失败在 Linux user journal，以及 macOS 和 Windows 的明确当前用户日志文件中保持可见。
`powercontext service status` 和 `powercontext doctor` 会返回相应 selector 或 path。平台 adapter 返回结构化失败，
但不包含 secret environment value。

原生 manager 只在异常失败后重启，绝不在 launcher 因 already-live 而干净退出后重启。重启次数有限，并使用 manager
专属 delay 或 backoff，不能把持久配置错误或地址冲突转化为无限快速循环。每个 adapter 都要测试渲染出的精确重启条件
和限制。

## Security and compatibility

- 个人服务安装是 opt-in 且 per-user。
- Setup 命令绝不隐式启用。
- 任何操作都不请求 privilege elevation。
- 每个操作系统用户只能有一个个人注册；具名 profile 不在初始范围内。
- 个人服务安装器只接受 loopback bind，并拒绝 public 或 remote service target。
- Client 和 integration 的远程 endpoint settings 绝不选择本地 service registration。
- 该功能不引入新的 HTTP、MCP、persistence 或 authentication contract。
- `powercontext server run` 保持当前前台行为。
- Uninstall 只删除经过验证的 PowerContext-owned artifact。
- 诊断和日志绝不暴露 credential 或 captured content。

## Delivery and testing

接受本 RFC 不要求一个实现 PR。Tracking issue 可以把工作拆为：

1. 宿主可见的 integration diagnostics；
2. 相关联的 doctor diagnostics 和共享 service state model；
3. distribution-owned CLI 和 adapter protocol；
4. Linux、macOS 和 Windows adapter；
5. 文档和平台 integration test。

共享状态模型和安全规则适用于每个平台 adapter。文档和 `powercontext service status` 必须说明 release 中实际存在的
支持，不能在 adapter 交付前暗示已经支持。增加 Windows personal-service adapter 支持还要求项目的基础 install 和
Server smoke test 在 Windows 上通过；仅接受本 RFC 不会扩大 README 当前的 macOS/Linux 支持声明。

纯测试覆盖 definition rendering、ownership verification、loopback validation、幂等 reconciliation、transaction
rollback、提交后启动失败、stop-before-remove、executable drift、status exit semantics、redaction、log-location
输出，以及内部 launcher 的每种 preflight outcome。Platform test 只在匹配的操作系统运行，并验证原生 register、
query、start、stop 和 remove。CI 不必模拟 interactive login，但必须断言原生 login trigger、精确 launcher command
和有界 restart policy。

Integration test 验证 unavailable、authentication failure、version mismatch、invalid response 和 empty success 保持为
不同、不含内容且 fail-open 的 outcome。每个宿主的 acceptance evidence 验证 warning 确实可见且重复有界。Doctor
test 覆盖可选的未安装状态、本地 registration correlation、远程 endpoint 行为，以及每种会产生不同恢复动作的事实
组合。只有这些基础测试和原生测试持续在匹配的 maintained runner 上运行时，平台才能对外声明 supported。

# Drawbacks

- 包含三个原生 adapter 的 distribution 层比一个前台命令增加了更多代码和运维面。
- 原生当前用户服务环境不同，特别是在接收配置和凭据方面。
- Server 持续不可用时，宿主可见 warning 可能产生噪音；集成需要适合宿主的展示方式，同时不能隐藏问题。
- 拆分实现可能让不同 release 暂时存在平台支持差异。
- 顶层 service 命令为部分用户永远不会使用的生命周期增加了 CLI vocabulary。
- 个人服务安装不能解决托管部署、远程访问、多用户隔离或生产 secret management。

# Rationale and alternatives

## Put autostart under the Server CLI

`powercontext server autostart enable` 之类的命令与 `powercontext server run` 放在一起更容易发现，但会让
Server role 拥有安装和操作系统持久化。本 RFC 分离 process construction 与 service installation。

## Start the Server from an integration hook

这种方式减少了一项 setup 步骤，但会让短生命周期、对 latency 敏感的 hook 拥有 process lifecycle。并发宿主可能竞争，
cold startup 可能拖慢 prompt，credential 或 configuration 也可能不一致。Hook 继续作为 consumer，只暴露 unavailability。

## Enable a service during setup

隐式安装会意外创建持久进程和操作系统状态。Setup 继续安装 integration 并推荐显式下一步；service installation
保持 opt-in。

## Publish manual recipes only

手工 `systemd`、`launchd` 和 `Task Scheduler` 指南可以避免 adapter code，但会随 release 漂移，也没有共享 status、
ownership、uninstall 或 doctor contract。原生 manager 仍是实际机制，但由 PowerContext 拥有可撤销的个人注册。

## Install a privileged system service everywhere

Machine-wide service 在用户正常的 trust 和 configuration boundary 之外启动，并要求提权。个人安装不需要该权限。
Managed operator 仍可显式定义 privileged service。

## Build a PowerContext supervisor

跨平台 supervisor 会重复原生 restart、logging 和 lifecycle 能力。Service-install 层注册一次性 preflight launcher，
而不自行 supervise Server。

## Require one pull request for all platforms

一个变更可以避免临时的平台支持差异，但会耦合彼此独立的原生集成，使 review 和 rollback 更困难。RFC 固定共享契约；
tracking issue 可以拆分实现，同时文档报告实际 availability。

## Make no change

用户可以一直打开终端或自行创建 service definition，但不可用的 integration 仍可能令人困惑，个人注册也继续缺少受支持的
诊断与 uninstall 路径。

# Prior art

`systemd` user service、macOS `LaunchAgent` 和 per-user `Task Scheduler` task 提供原生的 login-session lifecycle、
logging 和 status，不需要项目自建 supervisor。Developer tool 通常保留 interactive foreground command，同时为持久个人
使用提供独立的 install 或 service command。

容器和 administrator-managed service 是托管 workload 的成熟部署边界，因为它们明确 identity、configuration、
credential、restart policy 和 observability。本 RFC 把这一区分应用到 PowerContext，而不是把所有本地或托管 Server
都看成同一种 autostart 问题。

# Unresolved questions

规范性契约层面没有未决问题。具体原生 identifier 字符串和宿主可见 warning 机制仍属于 adapter implementation detail，
但在该 adapter 对外声明 supported 前，必须在代码中固定并通过上述 acceptance evidence。Managed deployment
automation、public binding、remote multi-user service profile、privileged installation 和 portable secret store
仍不在本 RFC 范围内。

# Future possibilities

- 增加 distribution-specific container manifest 或 administrator template，而不改变个人服务契约。
- 如果每个用户一个 service 无法满足需求，再增加具名个人 service profile。
- 在单独提案中增加稳定的 non-secret Server configuration file 和平台 credential integration。
- 在事务化 package upgrade 期间 reconcile 个人服务注册。
- 在 CLI 和诊断契约稳定后增加原生 desktop status surface。
- 当能够避免暴露 secret 时，让 doctor 提供有界的 log excerpt。

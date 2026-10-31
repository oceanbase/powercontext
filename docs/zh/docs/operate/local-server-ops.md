---
title: 本地 Server 运维工具
description: 使用独立 Ops 工具配置和管理已准备好的个人 Server 运行环境。
---

# 本地 Server 运维工具

Ops 为个人 Server 提供非交互的配置和服务管理入口，复用 `powercontext service` 使用的当前用户服务控制器，调用时不需要已有的 Server HTTP 会话。
Ops 自带的 Python 与依赖必须和受管 Server 的运行环境分开分发。因此，Server 解释器缺失不会妨碍工具自身启动和检查服务注册。

此服务入口管理已准备好的 Server 环境。固定受管资源及安装、修复策略见[受管本地 Server 安装](managed-local-server.md)。受约束的 Desktop 原生桥接见下文；安装界面是另外的能力，原有的人用 CLI 命令继续可用。
Ops 不是 HTTP API，也不新增常驻进程。
包含 `isolated_runtime=true` 的受管注册需使用随包 Ops 或匹配版本的服务工具；不支持该定义字段的旧 CLI 会拒绝它，不再继续管理该注册。

## 绑定目标与启动

可信调用方在启动时提供 `--target-file` 和 `--state-dir`。JSON 请求不能替换任意可执行文件、配置文件或状态目录路径。
目标文件绑定实际 Server 的解释器和包版本，不能从 Ops 自己的解释器或包版本推断目标身份。

目标文件格式如下，其中路径必须替换为安装对应的绝对路径：

```json
{
  "format_version": 1,
  "target_id": "personal-server",
  "python_executable": "/absolute/server/bin/python3",
  "package_version": "1.2.3",
  "env_file": "/absolute/config/server.env",
  "configuration_owned": false
}
```

`package_version` 必须是已准备好的 Server 资源中的实际版本。目标文件、配置文件和状态目录需要防止其他用户写入。
只有可信安装器拥有该配置时才能设置 `configuration_owned`；它不能用来要求接管独立管理的配置文件。
同一操作状态目录对应的绑定应保持稳定。本入口不支持跨绑定升级或迁移已有操作记录，
修改目标文件不能授权绕过尚未解决的记录。

安装了 `ops` 可选依赖的源码开发环境可以使用：

```bash
python -m powercontext.ops --target-file /absolute/target.json --state-dir /absolute/ops-state
```

下文的独立分发包使用随包解释器及固定入口：

```powershell
& 'C:\absolute\ops\runtime\python.exe' -I -S -B 'C:\absolute\ops\entry.py' --target-file 'C:\absolute\target.json' --state-dir 'C:\absolute\ops-state'
```

```bash
/absolute/ops/runtime/bin/python3 -I -S -B /absolute/ops/entry.py --target-file /absolute/target.json --state-dir /absolute/ops-state
```

向 stdin 发送一个 UTF-8 JSON 请求。stdout 输出 JSON Lines，每条消息包含 `protocol_version`、`request_id`，以及值为 `accepted`、`progress` 或 `result` 的 `type`。
调用方应分别持续读取 stdout 和 stderr，不能仅凭进程退出就判断操作没有产生副作用。

## 请求与确认

每个请求均包含 `protocol_version: 1`、`request_id` 和 `action`。

| action | 其他输入 | 行为 |
| --- | --- | --- |
| `inspect` | 无 | 观察绑定的服务，不修改状态、不主动探测 readiness。 |
| `config_validate` | 无 | 校验绑定的配置，不应用修改。 |
| `preflight` | `operation`、`options` | 返回含 `plan_id` 的计划，不执行修改。 |
| `apply` | `operation_id`、完整 `plan`、`authorization` | 复核并执行准确批准的计划；重复请求返回已有操作。 |
| `operation_status` | `operation_id` | 读取持久尝试及结果，不自动恢复执行。 |
| `cancel` | `operation_id`、`attempt` | 请求指定尝试在安全边界取消。 |
| `resume` | `operation_id`、`expected_attempt`、完整恢复 `plan`、`authorization` | 核对先前尝试和当前事实后继续支持的剩余工作。 |

支持的操作为 `register`、`start`、`stop`、`restart`、`remove_registration` 和 `config_apply`。
注册操作包含启动，批准前应展示计划中的启动效果。移除注册保留配置与用户数据，不卸载 Desktop，也不删除 Server 环境。

例如，请求停止计划：

```json
{"protocol_version":1,"request_id":"plan-stop","action":"preflight","operation":"stop","options":{}}
```

展示返回的计划并获得批准后，将完整计划原样传给 `apply`，其 `authorization` 为 `{"plan_id":"<返回的 plan_id>","approved":true}`。
调用前生成并保留 `operation_id`，响应丢失后即可通过 `operation_status` 查询。
授权字段表示可信调用方已经确认，不是身份认证凭据，不能直接变成不受限制的 renderer API。
完整计划含安全的 `target_summary`，展示目标解释器、包版本、配置路径、端点、数据目录和登录启动设置。
调用方应展示该摘要供核对，不自行构造或修改摘要。未指定 `start_on_login` 时保留已有注册值，Windows 新注册默认 `false`。

`config_apply` 接受 `http_port`、`dashboard_enabled`、`start_on_login`，以及默认值为 `false` 的明确 `restart` 选项。
它要求已有有效且归工具所有的配置，并保留其他设置。首次配置创建属于受管安装器。
配置和服务定义需要在服务保护下共同切换，失败或中断后必须先核对，再允许后续启动。
Windows 登录启动是明确的选项，不能假定 macOS 也支持相同的关闭登录触发选项。
配置切换可能停止正在运行的服务；只有计划明确允许且符合条件时才重启。
原本显式停止的服务继续停止，恢复操作本身也不隐含启动授权。
服务状态提示 `config_apply` 中断时，应通过原 Ops 调用方查询 `operation_status`，获取并确认新的 `preflight` 计划，
再用 `resume` 恢复已有操作。人用 CLI 的 `service install` 和 `service start` 命令不能完成 Ops 配置切换。

## 失败、取消与恢复

注册、进程和 readiness 是不同事实。已注册、已有进程都不能证明 Server 就绪。
启动失败清理仅作用于本操作启动且归属已知的目标。无法确认停止时，必须保持状态不确定，不能报告已经停止。

取消请求不会立即打断服务管理的关键区间，也不承诺回滚。迟到的取消不能覆盖已经保存的成功或失败。
响应丢失或进程被强制退出后，先查询持久操作，不自动重复修改。
其他操作持有修改锁时，只读查询和取消请求仍需可用。

操作中断后，原生服务管理器命令可能仍在执行。恢复修改前必须确认记录中的命令进程已经退出。
如果中断发生在启动命令与保存进程身份之间，证据并不完整，应报告结果不确定，不能盲目重放命令。
只读查询和取消请求仍可用，但不能承诺对未知命令证据自动修复。
原命令仍存活或其身份无法确认时，新的修改操作（包括停止和移除注册）仍被阻止。
仅仅拿到操作锁不能证明遗留原生命令已经停止。状态查询分别返回已保存的结果和当前观察，后续观察失败不会抹掉持久结果。

每个新尝试记录执行者 PID。状态查询单独返回执行者观察及 `execution_state`：已保存最终结果时为 `finished`，
未结束且确认执行者已经退出时为 `interrupted`，其余情况为 `unknown`。PID 可能被复用，因此进程存在或无法访问
不能证明原执行者仍在运行。没有执行者字段的旧版 1 记录仍可读取，执行者观察为未知。
这些观察不能代替修改前必须取得的服务锁和原生命令证据。

配置文件切换完成不能单独证明操作完成。若停止保护仍在但进程已经启动，配置恢复返回结果不确定，
需要先通过明确的停止操作确认已停止。对于没有保护记录、仍在运行的目标，允许启动的恢复计划也包含必要的
readiness 验证；恢复只验证已有进程，不重复启动。收窄为不允许启动的计划不会静默执行 readiness 探测。

服务归属与配置检查仍然生效。Server 环境缺失或变化，不授权覆盖配置、接管未知注册或删除数据。
支持的检查、停止和移除注册继续使用原有归属规则。需要人工恢复时保留未完成操作的证据。

## 构建独立分发包

`scripts/build_ops_bundle.py` 供资源发布方使用，输入为可信的完整可再分发 Python 目录、包含 `powercontext[ops]` 及全部依赖的兼容 wheel 目录，以及固定版本和 SHA-256 摘要的 requirements 文件。
需要在目标操作系统和架构上构建，它不是交叉编译工具。构建机需要 Python 和 `uv`，分发包的使用者不需要在系统中安装它们。

```powershell
uv run python scripts/build_ops_bundle.py --runtime C:\inputs\python --python-relative python.exe --wheelhouse C:\inputs\wheels --requirements C:\inputs\ops-requirements.txt --output C:\artifacts\ops
```

macOS 使用对应的运行环境目录，并设置 `--python-relative bin/python3`。
构建工具拒绝虚拟环境和越出运行环境的链接；复制完整解释器后，以离线模式安装提供的、带摘要的 wheel，再使用空可执行文件搜索路径验证分发包入口。
运行时仅从隔离的 Python 标准库和包内 `packages` 目录导入，不从受管 Server 环境或源码目录导入。

对构建产物执行 `uv run python scripts/ci_ops_bundle_smoke.py --bundle C:\artifacts\ops`（macOS 使用对应的绝对目录）。
该检查在源码目录以外启动真实子进程，设置干扰性的 Python 环境变量，并绑定不存在的 Server 解释器。
入口与协议检查使用空 `PATH`；只读检查仅保留原生服务和 ACL 命令所需的操作系统工具目录，所有已加载模块的路径必须位于分发包内。
它不注册、启动、停止或移除任何服务，也不代替原生生命周期验收。

输出包含 `runtime/`、`packages/`、`entry.py` 和 `bundle.json`。
清单记录运行环境身份、启动参数、requirements 摘要、安装文件字节数和文件摘要。它是产物清单，不是信任锚或签名，不能把不可信运行环境或 wheel 变成可信输入。
已有输出目录不会被覆盖，构建失败后应检查残留产物，再决定清理或换目录构建。

[候选资源生产器](../../development/managed-resource-production.md)为 Windows/macOS 三个目标固定输入版本、摘要、平台与架构身份，
保留许可说明并记录来源；输出始终是未资格验收的候选资源。
Windows/macOS 打包（#2108/#2109）需要将 Ops 发布到受用户权限保护、独立于 Desktop 和受管 Server 的稳定目录。
卸载 Desktop 不应悄悄删除维护工具或用户数据。
签名、macOS notarization、干净机器执行以及原生服务、登录和重启验收是单独的发布检查，构建成功不能代替这些证据。

原有服务行为与平台限制见[部署 Server](deploy-server.md)。

## Desktop 原生桥接

Desktop 仅向随包主窗口开放类型化的 `ops_inspect`、`ops_config_validate`、`ops_preflight`、`ops_apply`、
`ops_operation_status`、`ops_cancel`、`ops_resume` 和 `ops_references` 命令。
`desktop/ui/src/shared/ops.ts` 提供类型化客户端及 `subscribeOpsProgress` 订阅；应先订阅，再执行批准的计划。
通过校验的 `accepted` 和 `progress` JSONL 消息会在工具运行期间实时到达，包含原生请求 ID、操作 ID、尝试编号、阶段及序号。
检查不依赖目标 Server 的 Python 是否存在。Linux 返回 `unsupported_platform`。

Renderer 只能选择支持的操作与选项，不能提供命令、解释器、服务名、配置路径、下载 URL、catalog 或完整计划。
原生 preflight 保存 Ops 返回的完整计划，只返回供核对的摘要、`planId` 和不透明的 `approvalId`。
批准十分钟后过期，且只能使用一次。Apply 要求准确匹配批准 ID 与计划 ID。
Resume 先通过包含 `resume: { operationId, expectedAttempt }` 的新 preflight 获取计划，再把新批准传给 `ops_resume`。
绑定变化会使批准失效，也不能把旧操作引用用于查询或恢复另一目标。

原生端在派发修改前，将操作 ID 与绑定摘要原子写入私有的 `PowerContext/Ops/bridge-operations.json`。
窗口或 Desktop 重开后，`ops_references` 找回引用，再由 `ops_operation_status` 查询原 Ops 日志。
引用文件不保存计划、结果、凭据，也不建立第二套恢复状态机；文件损坏时拒绝新修改，不会静默重置。
引用不会自动清除，上限为 4096 条。尝试、锁、取消、恢复判断和原生服务效果仍由 Ops 管理。
修改 target 文件不构成受支持的操作日志迁移。

有效的结构化非零退出结果仍会保留被拒绝或不健康的事实。超时、响应格式错误、响应丢失或退出会返回结果未知及保留的操作 ID。
应先查询原操作，再请求新的恢复计划；不能推断已回滚，也不能自动重放修改。
查询与取消通过独立工具进程执行，并保留两个专用并发名额；其他请求最多同时运行四个工具进程。
超时的工具在退出前继续占用名额。原生端不向 Ops、Server 或服务管理器进程发送终止信号。
正常退出 Desktop 时停止接收新请求，最多等待两秒。关闭管道可能使 Ops 在安全边界记录进度通道失败；
已经接受的原生工作仍可能继续并保存结果。取消是 Ops 协议请求，不等于杀进程，也不承诺回滚。

每个工具接收一条有界 stdin 请求、隔离的环境及固定 `-I -S -B <绝对 entry.py>` 参数。
被动查询的进程时限为 30 秒，preflight 为 180 秒以容纳有界的目标数据库准入检查，修改为 30 分钟；每条 JSONL 上限 64 KiB，stdout/stderr 合计上限 1 MiB，消息数上限 128。
两条输出流持续读取，不转发原始文本。完成以工具进程退出为边界，不会因后代保留管道句柄而无限等待 EOF。
协议版本、请求／操作身份、尝试编号、阶段序列、结果和允许字段在展示前校验。
原始 stderr、提供方异常、配置内容和凭据不会进入 renderer。

### 配置桥接资源

可信原生安装器或打包器将独立 Ops 分发包发布到固定当前用户目录：

- Windows：`%LOCALAPPDATA%\PowerContext\Ops\bundle`。
- macOS：`~/Library/Application Support/PowerContext/Ops/bundle`。

该目录与 Desktop、Server 保持独立；卸载 Desktop 时保留工具、引用和 Ops 日志。
桥接直接使用现有 `scripts/build_ops_bundle.py` 的 `runtime/`、`packages/`、`entry.py` 和 `bundle.json` 格式。
每次启动校验准确清单摘要、平台／架构、完整文件清单和文件内容。原始构建器的相对 runtime 文件别名，仅在解析结果仍位于
runtime 内、指向单独列出的普通文件且摘要一致时允许。外部／绝对链接、目录别名、reparse point、额外文件和依赖损坏均被拒绝。
生产方规范化的分发包可以全部为普通文件。每次启动都会读取完整分发包，此处不宣称性能提升或满足发布性能预算。

随包资源目录提供 `ops-provision.json`：

```json
{
  "format_version": 1,
  "bundle_manifest_sha256": "<已批准 bundle.json 原始字节的 SHA-256>",
  "binding_mode": "managed_catalog",
  "catalog_sha256": "<已批准 server/catalog.json 原始字节的 SHA-256>"
}
```

两个占位符必须替换为可信生产方和打包器固定的 64 位小写十六进制摘要。候选分发包自己的清单或校验文件不能自行授予信任。
Desktop 随包资源中的固定摘要是信任起点；生产分发的真实性仍要求合格的发布和签名流程。
原生端校验随包 `server/catalog.json`，原子写入私有 `PowerContext/Ops/catalog-<digest>.json`，再把不可变副本及摘要传给独立 Ops 入口。
Renderer 不能替换它们。受管目录仍由 Ops 的 `ManagedLayout.current_user()` 决定。

可选的真实 Tauri 合并配置 `desktop/src-tauri/tauri.ops.conf.json` 从 `desktop/.artifacts/ops-resource/` 打包上述两个文件。
打包器将已批准生产产物中的 `ops-provision.json` 和 `server/catalog.json` 放到该目录，另行将准确 Ops 分发包配置到固定位置，
然后在 `desktop/` 中运行：

```sh
pnpm tauri build --config src-tauri/tauri.ops.conf.json --ci -- --locked
```

此构建入口不下载资源、不安装独立工具，也不对候选产物作资格认定。
默认构建不包含这些可选资源，返回 `unavailable`，不会启动系统 Python。
固定摘要无效或文件变化时返回 `integrity_failure`，不会退回目标 Server 环境执行。

对于由操作者准备的 Server，使用 `binding_mode: "target_file"` 并省略 `catalog_sha256`，在私有的
`PowerContext/Ops/target.json` 放置前文定义的绑定。原生端传递校验后的摘要命名私有快照及固定 `PowerContext/Ops/state`。
提供的合并配置面向受管 catalog；target-file 打包器仅把 `ops-provision.json` 加入 Tauri 资源映射。
原生路径来自操作系统，并明确传入用户 home；不继承环境中的 Python／Server 配置变量。

桥接不提供安装向导，也不自动建立连接或选择 Scope。真实 Windows Task Scheduler、macOS LaunchAgent 修改、正式资源发布、
签名／公证、干净机器安装和发布验收仍是单独工作。合成工具进程及真实的源码 Ops 只读子进程分别验证对应协议和进程边界，
不能证明这些平台或发布结果。

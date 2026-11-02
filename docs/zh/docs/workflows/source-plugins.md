---
title: Source 插件
description: 加载可信本地插件，通过公共 MCP 同步写入或持续采集 Source。
---

# Source 插件

宿主始终连接 PowerContext Server 的公共 `/mcp`。`write` 在 Server 内同步保存宿主提供的内容；`collect` 由独立 Runtime 采集外部资料，共享管理服务负责目标与启停状态。

加载代码是拥有 `server.admin` 权限的部署操作。插件代码和依赖须在执行它的 Python 环境可用：write 在 Server 机器，collect 在 Runtime 机器。只加载受信任的代码。凭据文件须为普通文件，不能是符号链接；Linux 下将权限设为 `600`（`chmod 600 /path/to/token`）。

开始前，准备已启用鉴权和 PC Access 的 Server、已有 Scope，以及分开的部署管理员和普通宿主凭据。本文示例中的路径、地址和 `SCOPE_ID` 需替换为实际值；MCP 示例采用 `工具名(参数)` 简写，对应 `/mcp` 的 `tools/call` 中的 `name` 和 `arguments`。能力名称、版本和输入字段以发现结果为准。

| 操作 | PC 权限 |
| --- | --- |
| 发现能力、实例和查询目标 | 对应 Scope 的 `scope.read` |
| write、添加 collect 目标 | 对应 Scope 的 `scope.contribute` 和 `scope.read` |
| 启停目标、读取目标 control 视图 | 对应 Scope 的 `scope.contribute` |
| 加载插件、配置或启停实例、读取实例 control 视图 | `server.admin` |

插件访问外部系统的凭据由插件管理，不替代 PC 权限。宿主只连接 Server，不需要安装 collect Runtime。

## 加载 write

准备包含 `definitions` 和 write `capabilities` 的本地插件，以及部署配置：

```yaml
id: local-tickets
entry: .
```

将配置保存为插件目录中的 `write.yml`，入口 `.` 指向同目录的 `__init__.py`。管理员在该目录执行：

```sh
powercontext source load --kind write --config ./write.yml \
  --server-url http://127.0.0.1:8000 --credential-file /run/secrets/pc-admin
```

Server 实际导入代码、校验输入模型和 Definition/Projection 契约，注册成功后公开能力。它持久保存入口，重启重新导入。一致配置重复加载可复用。当前仅支持保持入口、能力和 Definition/Projection 声明不变的代码更新，通过受控重启 Server 生效；不做进程内 Python 热替换。修改声明中的版本、输入或输出 schema 后，重启会因持久契约不一致而恢复失败，不能用重启完成契约升级。

宿主使用具有目标 Scope 的 `scope.read` 和 `scope.contribute` 权限的身份调用：

```javascript
pc_source_capabilities({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","capability":"ticket.write","capability_version":"1",
  "input":{"namespace":"support","project":"demo","ticket_id":"A","title":"故障恢复","body":"服务已恢复"}
})
```

write 不传 `instance_id`。返回 `result.source_refs` 表示真实 Source/Projection 已保存。write 不创建持久请求，也不使用 collect 目标或 checkpoint。相同身份和相同内容复用，同身份不同内容冲突；工单示例按完整快照计算身份。超时表示结果未确认，不能认定未保存。完整 add 参数 JSON 上限为 1 MiB，物化 observation 上限为 4 MiB，包含 Base64 膨胀；超限报错，不截断。

## 在运行中的 Server 上加载 collect

Server 的环境文件中启用鉴权、PC Access，并指定可信实例目录：

```dotenv
POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR=/srv/powercontext/source-plugins
POWERCONTEXT_SERVER_ACCESS='{"mode":"enforced"}'
```

首次配置后重启 Server。自动部署创建独立管理和执行凭据，不修改用户身份文件。Server 启动扫描直接子目录的 `powercontext.yml`；之后新增实例显式 load，不持续扫描。此自动扫描用于 collect；write 使用前述 `--kind write` 显式加载。本地 collect 部署要求 Linux：`source_management.process_mode="background"`（默认）使用后台进程；`"local-service"` 还要求可用的 systemd 用户服务管理器（`systemctl --user`）。两种模式均不支持在 macOS 或 Windows 上本地部署。此限制针对 collect 进程管理，不针对 Server 内同步执行的 write 插件。

实例目录就近放置 `__init__.py`、`definition.py`、资料文件和 `powercontext.yml`，例如：

```yaml
id: my-tickets
plugin: .
scope_ids: [SCOPE_ID]
config:
  file: tickets.json
  poll_interval: 10
```

Scope 须已存在，配置里的范围不替代 PC 权限。业务参数由插件的 `Config` 校验，采集节奏由插件决定。

```sh
powercontext source load --config /srv/powercontext/source-plugins/my-tickets/powercontext.yml \
  --env-file ./pc.env
```

命令在 Server 所在机器使用相同环境配置执行，完成 Definition 注册、Scope 授权、实例登记和 Runtime 启动。重复加载一致配置保留启停状态和进程；修改代码入口或业务配置须显式更新。已部署实例的 Scope 范围与目标以管理服务记录为准，重复 load 不用文件覆盖它们。

宿主通过公共 MCP 建立持续采集目标：

```javascript
pc_source_instances({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","instance_id":"my-tickets",
  "capability":"tickets.item","capability_version":"1","input":{"id":"A"}
})
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID"})
```

add 和重复 add 返回目标身份及控制状态，不携带 Source 引用和提交时间。状态查询只携带管理服务最近收到的一次成功提交报告：`source_refs` 包含一个引用，`last_submitted_at` 是同一报告的提交时间；尚未收到报告时，引用为空数组，也没有提交时间。报告按接收顺序覆盖，这既不表示一批已完成，也不是已采集 Source 的历史清单。管理服务不累计历史引用，报告丢失不撤销已保存的 Source。插件仍从每次真实提交回执取得 SourceRef，并用 checkpoint 管理采集进度。相同目标重复 add 复用，不重启采集，也不恢复已停用目标。可选 `targets` 清单只用于首次初始化，后续以管理服务记录为准。

## 远端 collect Runtime

在执行机器准备插件代码，并在其清单中增加：

```yaml
deployment:
  server:
    url: https://pc.example.com
    credential_file: /run/secrets/pc-admin
  management_url: https://source-manager.example.com
  execution_credential_file: /run/secrets/pc-worker
  state_dir: ./instance-state
```

在该机器执行 `powercontext source load --config ./powercontext.yml`。Server 和管理服务须已可达；非本机连接要求 HTTPS。管理员和 Runtime 使用不同身份，Runtime 身份不能具有 `server.admin`。命令启动本机 Runtime，不分发代码或执行远程 shell。显式设置 `deployment.start: local-service` 可使用 systemd 用户服务。

普通后台进程不会在机器重启后自动拉起。已部署实例可使用 load 输出的 Runtime 配置重新启动，无需重新登记：

```sh
powercontext source runtime start --config /path/to/runtime-config.json
powercontext source runtime status --config /path/to/runtime-config.json
```

使用 systemd 托管时，上述 start/status 命令增加 `--system-service`。进程在线只表示 Runtime 存活，还需通过 MCP 查看实例可用性和实际 Source 提交。

## 扩展实例的 Scope

仅增加 Scope 且其他配置不变时，管理员先通过 Access 接口授予执行身份相应权限，再向 `POST /v1/source-management/configure` 提交完整实例登记和 `update=true`，扩展 `scope_ids`。实例可以保持运行，原 Context 与 checkpoint 保留；宿主取得新 Scope 权限后即可添加目标。修改清单后重复 load 不会完成这项扩展。

## 停用和更新

管理员通过 MCP 停用整个实例：

```javascript
pc_source_instance_set_enabled({"instance_id":"my-tickets","enabled":false})
pc_source_instances({"instance_id":"my-tickets","view":"control"})
```

等到 `actual=stopped` 后，在执行机器停止外层 Runtime，使用部署输出或私有状态目录中的配置路径：

```sh
powercontext source runtime stop --config /path/to/runtime-config.json
powercontext source load --update --config /path/to/powercontext.yml
```

本机自动部署的 load 仍须带相同 `--env-file`；系统服务 stop 带 `--system-service`。缩减 Scope、修改业务配置或升级代码时，保留实例 ID、状态目录和兼容的 Definition、能力、目标身份及 checkpoint。`source load --update` 必须确认旧 Runtime 已退出；成功后仍停用，管理员显式 `enabled=true` 才恢复采集。普通 load 不隐式升级。

只停止某份资料时，控制对应目标，不影响整个实例：

```javascript
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":false})
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","view":"control"})
```

`stopping` 表示停止意图已保存，`stopped` 表示 Runtime 已确认停止。恢复时将 `enabled` 设为 `true`；这不会自动启用已停用的实例。管理失联不等于业务停止。Source、目标和 checkpoint 不随停用删除；Source 保存和 checkpoint/报告提交是独立动作。

当前实现尚无删除实例登记及所属目标的完整卸载命令。`runtime stop` 只停止执行进程，保留管理记录和已保存数据；停止 Server 或删除插件目录也不等于卸载独立服务。

## 常见问题

| 现象 | 检查和处理 |
| --- | --- |
| 启动日志出现 `background-unsupported` 或 `service-unavailable` | 本地 collect 部署要求 Linux；`local-service` 还需要可用的 `systemctl --user`。检查执行平台和进程模式。 |
| 管理服务断线 | 已同步的 collect 可继续；新管理调用可能失败。内置及已加载的 write 不依赖管理服务。 |
| Runtime 重启后管理服务不可达 | 等待重新同步目标，不从文件初始 targets 回退采集。 |
| 目标长期没有新 Source | 检查插件采集逻辑是否仍运行、采集节奏、外部凭据和 Scope 权限；进程在线不保证采集成功。 |
| 一直处于 stopping | 检查原 Runtime 与管理连接，不能把断线当成停止完成。 |
| Source 已保存但目标状态没有引用 | 提交报告可能丢失；目标状态不是 Server 全部数据的清单，不应因此回退 checkpoint。 |
| Agent 没使用已保存的资料 | 核对采集 Scope 与宿主当前 Scope，以及后续 Projection、Artifact 和索引处理。 |

持续采集插件应在 `apply` 中保持自己的循环。`apply` 结束后，新目标或通信重连不会自动重跑它；需要重新运行时，显式停用并确认停止，再启用。

## 可运行示例

仓库 `examples/source_plugins` 按工单、工单/聊天、图片组织示例，每个目录就近放置代码和加载配置。工单示例共用一份 Definition 和 Projection：`write.yml` 用于 Server 内同步写入，`powercontext.yml` 用于托管采集。加载和 MCP 调用步骤见 `examples/source_plugins/README.zh.md`。

凭据、数据库、日志和生成的进程状态留在部署机器；只提交插件代码、无敏感信息的配置模板和合成示例资料。

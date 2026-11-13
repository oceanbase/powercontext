---
title: Source 插件
description: 用 Source 插件提供自己的 Source 类型，并由 Server 或设备上的 runner 按分配的目标持续采集。
---

# Source 插件

Source 插件与 Artifact 插件放在同一个插件目录中（`POWERCONTEXT_SERVER_PLUGINS_DIR`），`plugin.py` 把一个
`SourcePlugin` 赋给 `plugin`，目录名就是插件名。它可以：

- **只提供 Source 类型**：声明 `definitions`，调用方通过现有的 Source 写入接口提交内容，Server 执行 `resolve` 和
  Projection 后保存。
- **采集**：再声明 `collect`、`target` 和 `setup`。采集由 runner 执行：Server 自带名为 `server` 的 runner，设备上运行
  `powercontext source-agent`。用户把采集目标分配给某个 runner，runner 定时取回自己的目标并提交结果。

插件的加载、更新和移除与 Artifact 插件相同，见[编写插件](../develop/plugins.md)。可运行的示例在仓库的
`examples/source_plugins/` 中。

## 权限

| 操作 | 需要的权限 |
| --- | --- |
| 列出 runner 和它们的能力（`pc_source_runners`） | 已登录 |
| 查看 Scope 的目标与状态（`pc_source_status`） | 该 Scope 的 `scope.read` |
| 给设备 runner 新增、修改、停用、删除目标 | 该 Scope 的 `scope.contribute` |
| 给 `server` runner 新增、修改、停用、删除目标 | `server.admin` |
| 设备 runner 同步、提交 Source 和 checkpoint | runner 本身，以及目标 Scope 的 `scope.contribute` |
| 删除 runner 记录 | `server.admin` |

`server` runner 用 Server 环境中的凭据访问外部系统，决定它采集什么等同于运维决定，所以需要 `server.admin`。插件访问
外部系统的凭据由插件自己读取（例如环境变量），不替代 PowerContext 的权限。

## 只提供 Source 类型

`examples/source_plugins/images` 只声明了一个 Source 类型：

```python
plugin = SourcePlugin(version="1.0.0", definitions=(ImageDefinition(),))
```

把目录放进插件目录后，调用方用通用 Source 接口提交：

```bash
curl -X POST "$SERVER/v1/scopes/$SCOPE/sources" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type": "example-image", "content": {"media_type": "image/png", "data_base64": "..."}}'
```

Server 在插件的宿主进程中校验输入、执行 `resolve` 和 Projection，按 Source 身份去重。

## 由 Server 采集

`examples/source_plugins/tickets` 从 runner 本机的 JSON 文件采集工单：

```python
plugin = SourcePlugin(
    version="1.0.0",
    definitions=(TicketDefinition(),),
    collect=Collect(schedule=Interval(seconds=10)),
    target=TargetSpec(
        description="Collect tickets from a JSON file on the runner",
        parameters=TicketFile,
        identity={"file": ("strip",)},
    ),
    setup=setup,
)
```

管理员给 `server` runner 分配一个目标（MCP 示例采用 `工具名(参数)` 简写）：

```javascript
pc_source_runners()
pc_source_add({"scope_id": "SCOPE_ID", "runner": "server", "capability": "tickets",
               "parameters": {"file": "/srv/data/tickets.json"}})
```

新目标立即采集一次，之后按插件的 `schedule` 采集。每次采集在 Supervisor Worker 中执行，处理一个 Scope 中分配给
`server` 的已启用目标；返回的 Source、各目标新的 checkpoint 和状态在一个事务中保存。

## 由设备采集

设备上放一份同一插件，用一个对目标 Scope 有 `scope.contribute` 的凭据运行 runner：

```bash
export POWERCONTEXT_SOURCE_AGENT_TOKEN=...      # runner 的凭据
powercontext source-agent --server https://memory.example.com --plugins /opt/robot/plugins
```

- runner 的名字默认取自凭据的主体，也可以用 `--name` 指定；多台设备共用一个凭据时必须分别指定。名字 `server` 保留给
  Server 自带的 runner；一个名字第一次同步时与当时的凭据主体绑定，其他主体不能再以这个名字同步。
- runner 每 30 秒（`--interval`）同步一次：上报加载的插件和版本，取回分配给自己的全部目标（跨 Scope）。新目标立即
  采集，其余按插件的 `schedule` 采集。
- 每个目标先提交 Source，再以旧 checkpoint 为期望值提交新的 checkpoint 和状态。中途失败时 checkpoint 不前进，下次
  重新采集同样的内容，Server 按 Source 身份去重。
- 设备上使用的每个 Source 插件都要同时部署在 Server 的插件目录中：能力的说明、参数格式和 Projection 都以 Server 加载的
  版本为准。

给设备分配目标：

```javascript
pc_source_add({"scope_id": "SCOPE_ID", "runner": "robot-7", "capability": "ticket-chat",
               "parameters": {"file": "/var/lib/robot/chats.json"}})
```

## 管理目标

```javascript
pc_source_status({"scope_id": "SCOPE_ID"})
pc_source_target_set_enabled({"scope_id": "SCOPE_ID", "target_id": "...", "enabled": false})
pc_source_target_delete({"scope_id": "SCOPE_ID", "target_id": "..."})
```

- **去重**：Server 按自己加载的插件版本校验 `parameters`，并用 `identity` 中的参数（经过声明的规范化步骤）计算目标键。
  同一 Scope、runner、能力下键相同的目标已存在时，参数相同返回已有目标，参数不同返回冲突。
- **修改**：可以修改不参与唯一性的参数和启停；唯一性参数不能修改（返回 `identity_immutable`），要采集另一个对象就新增
  目标，这样 checkpoint 总是对应同一个外部对象。
- **停用与删除**：停用的目标不再同步给 runner，checkpoint 保留，恢复后从原位置继续；删除同时删除 checkpoint，已采集的
  Source 保留。
- **状态**：每个目标记录最近尝试时间、最近成功时间、连续失败次数和最近错误。runner 的 `last_seen_at` 晚于目标的
  `updated_at`，说明 runner 已经取到最新配置。

## 插件的采集契约

```python
async def collect(self, request: CollectRequest) -> CollectResult:
    result = CollectResult()
    for target in request.targets:
        ...
        result.add(target, items, checkpoint=new_checkpoint, has_more=False)
        # 只有这个目标失败时：result.fail(target, code="...", message="...")
    return result
```

- 一次调用对应一个 Scope，最多 `max_targets` 个已启用目标；目标较多时轮转。
- 返回的条目总数超过 `max_items`，或返回了请求中没有的目标，整次调用失败，不截断。没有出现在结果中的目标 checkpoint
  不变。
- `fail` 只让这个目标失败：它的 checkpoint 不变、状态记录错误，同一次调用中其他目标照常提交。`collect` 抛出异常时整次
  调用失败。
- 阻塞的读取放进 `await asyncio.to_thread(...)`。在设备上 `ctx.storage` 不可用；`ctx.center` 只在设备上存在，可以用
  `ctx.center.call_plugin(method, path, json=..., content=...)` 调用插件自己在 Server 上的路由，例如先上传大文件，再
  提交引用它的 Source。

## 版本与升级

`SourcePlugin` 声明 `version`（PEP 440）和可选的 `min_compatible_version`（默认等于 `version`）。设备上的插件版本满足
`Server 的 min_compatible_version <= 设备版本 <= Server 的 version` 时，Server 接受它的同步、Source、checkpoint 和
`call_plugin` 请求；否则返回 `plugin_version_incompatible`，同步时不下发该插件的目标，`pc_source_runners` 中显示为
不兼容。

- **先升级 Server，再升级设备**。比 Server 新的设备版本同样被拒绝。Server 有多个实例时，先让所有实例完成升级。
- 目标参数按 Server 版本的格式填写，可能带有旧设备不认识的字段，所以参数模型不能设置 `extra="forbid"`。新参数必须在
  所有设备上生效时，提高 `min_compatible_version`。
- Server 每次加载插件版本时调用 `upgrade(ctx)`（见[编写插件](../develop/plugins.md)）；设备不调用 `upgrade`，插件自己的
  本地文件在 `setup` 中检查和迁移。

## 从旧的 Source 插件框架迁移

`/v1/source-management/*`、`powercontext source load`、`powercontext.yml` 和 `write.yml` 已移除：

- write 插件改写为只声明 `definitions` 的 `SourcePlugin`，放进插件目录。
- collect 插件改写为声明 `collect`、`target` 和 `setup` 的 `SourcePlugin`；原来在设备上计算的目标身份改为 `identity`
  声明。
- 升级到本版本前，在旧版本上卸载所有已加载的 write 插件；数据库中仍登记着 write 插件时，迁移会拒绝升级。

---
title: Source 插件与采集
description: Source 插件提供 Source 类型并从外部系统持续采集；采集目标集中存放在中心 Server，由服务端或设备上的采集运行时拉取同步并汇报结果，取代旧 Source 插件框架的管理服务和反向通道。
---

- Proposal Name: `source_plugins_and_collection`
- Start Date: 2026-11-12
- RFC PR: [oceanbase/powercontext#2617](https://github.com/oceanbase/powercontext/pull/2617)

# 概要

本提案在 [插件接口 RFC #2549](https://github.com/oceanbase/powercontext/pull/2549) 之上定义独立的 Source 插件：插件提供
Source 类型，需要时从外部系统持续采集。采集可以在中心 Server 上运行，也可以在机器人、边缘设备等端侧运行，结果统一汇报到
中心。

- **Source 插件**：一个目录加 `plugin.py`，把 `SourcePlugin` 声明赋给 `plugin`。只提供 `definitions` 的插件扩展 Source
  类型；声明了 `collect` 的插件按采集目标从外部系统读取数据。
- **采集运行时（runner）**：执行 `collect` 的进程。中心 Server 自带一个名为 `server` 的 runner；设备上以
  `powercontext source-agent` 启动的进程也是 runner，名字取自它的凭据主体。
- **采集目标集中存放**：目标保存在中心的 `pc_source_collect_targets` 中，属于某个 Scope，指定由哪个 runner、用哪个插件
  采集。用户通过 Source 管理接口或 Dashboard 增删改，不需要登录设备。
- **拉取同步**：runner 定时到中心同步，一次取回分配给自己的全部目标，并上报自己加载的插件及其参数格式；中心不向设备推送，
  没有反向通道。
- **去重**：插件声明哪些参数组成目标的唯一键及其规范化规则，中心添加目标时就能识别重复。
- **结果**：Source 和 checkpoint 沿用 core 已有的远程采集接口提交；每个目标的最近状态随 checkpoint 一起汇报。
- **大块内容**：框架不规定传输方式；同一个 Source 插件可以同时在中心运行，提供接收文件的接口，并在 Projection 中读出文件。

本提案取代 [Source 插件框架 RFC #2012](2012-source-plugin-system.md) 的插件契约与管理机制：管理服务、实例登记、反向通道和
`source load` 命令移除，`pc_source_write_plugins` 在没有数据时删除。

本文描述目标设计，不代表主线或发布版本已经可用。接口名称和代码示例用于说明契约，最终语法以实现为准。

# 动机

旧 Source 插件框架（#2012）为插件承担了大量管理工作：独立的管理服务及其 SQLite（实例、能力、目标、期望状态、提交记录），
每个插件实例一个 Runtime 进程，管理服务与 Server 之间的 poll / reply 反向通道和能力目录同步，以及部署命令和实例启停
工具。部署者要运行多个进程、维护两个数据库；插件作者要理解实例、通道和部署流程。

RFC #2549 把 Artifact 插件改成"插件实现接口、core 在需要时调用"的模型，Source 插件也应如此。同时有一个需求是旧框架
支持、而 RFC #2549 最初的端侧设计做不到的：

- **具身智能等场景需要在中心配置一批设备的采集**。设备通常在 NAT 后面，从外部连不进去，但设备能连到中心。运维人员需要在
  中心的面板上决定"哪台设备采集什么、写进哪个 Scope"，而不是逐台登录设备。
- **在面板上配置需要即时去重和参数校验**：添加目标时就要知道它是否已经存在、参数是否合法，而不是等设备执行后才发现。

本提案的目标：

1. 插件作者只写一个 `plugin.py`，不需要理解实例、通道或部署流程。
2. 采集目标集中保存，用户在中心或 Dashboard 上管理任意 runner 的目标，增减目标不需要重启任何进程。
3. 设备只需要能连到中心；中心不主动连接设备。
4. 断线、重启和重传都按幂等处理，不产生重复 Source。
5. 部署者只运行 Server，以及需要端侧采集时每台设备上的一个采集进程。

# 指南级说明

## 1. 插件的形态

Source 插件与 Artifact 插件放在同一个插件目录下，形态相同：

```python
plugin = SourcePlugin(definitions=(CameraEventDefinition(),), collect=Collect(...), target=..., setup=setup)
```

| 接口 | 必需 | 说明 |
| --- | --- | --- |
| `definitions` | 是 | `SourceDefinition` 序列，与 RFC #2549 中插件自带的 Source 类型相同 |
| `target` | 采集时是 | 目标参数的 Pydantic 模型和唯一性表达式，见下文 |
| `setup(ctx) -> SourceCollector` | 采集时是 | 工厂，返回实现了 `collect` 的对象 |
| `collect(request) -> CollectResult` | 采集时是 | 按目标和 checkpoint 读取外部数据，返回 Source 和新的 checkpoint |

只提供 `definitions` 的插件只扩展 Source 类型：调用方通过现有的 Source 写入接口提交，core 在中心的宿主进程中执行 `resolve`
和 Projection，与 RFC #2549 中插件自带的 Source 类型一致。

## 2. 示例：机器人摄像头事件

```python
from pydantic import BaseModel, Field

from powercontext.plugins import (
    Collect, CollectedItem, CollectRequest, CollectResult, Interval, SourcePlugin, TargetSpec,
)


class CameraTarget(BaseModel):
    camera: str = Field(description="设备上的摄像头名，例如 front")
    min_interval_seconds: int = Field(default=5, ge=1)


class CameraCollector:
    def __init__(self, ctx):
        self.ctx = ctx

    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        for target in request.targets:
            since = (target.checkpoint or {}).get("since", 0)
            events = await read_local_events(target.parameters["camera"], since)
            items = [CollectedItem(definition="camera-event", value=event) for event in events]
            result.add(target, items, checkpoint={"since": events[-1].sequence if events else since})
        return result


async def setup(ctx):
    return CameraCollector(ctx)


plugin = SourcePlugin(
    definitions=(CameraEventDefinition(),),
    collect=Collect(schedule=Interval(seconds=30)),
    target=TargetSpec(
        description="采集设备摄像头的事件",
        parameters=CameraTarget,
        identity={"camera": ("strip", "lower")},
    ),
    setup=setup,
)
```

插件名取自目录名（这里假设是 `camera-events`），它也是这项采集能力的名字。

## 3. 采集运行时

- **服务端**：中心 Server 自带名为 `server` 的 runner，按 Supervisor 的调度在 Worker 中执行插件目录下的采集插件。
- **端侧**：在设备上运行

  ```text
  powercontext source-agent --server https://memory.example.com
  ```

  它只加载插件目录中的 Source 插件，用凭据连接中心。runner 的名字默认取自凭据的主体（例如服务账号 `robot-7`），也可以用
  `--name` 指定；多台设备共用一个凭据时必须分别指定。
- **同步**：runner 定时（默认 30 秒）调用中心的同步接口，完成三件事：
  1. 上报自己加载的插件、每个插件的参数格式和唯一性表达式，以及最近在线时间；
  2. 取回分配给自己的全部目标（跨所有 Scope）；
  3. 与本地正在执行的目标比对，开始新目标，停止已删除或已停用的目标。
- runner 不需要配置 Scope 列表：分配给它的目标本身带着 Scope。新建 Scope 并分配目标后，下一次同步就开始采集。

## 4. 管理采集目标

调用方使用 Source 管理接口（HTTP 和 MCP）：

```text
pc_source_runners()
pc_source_add(scope_id="robot-7-data", runner="robot-7", capability="camera-events",
              parameters={"camera": "front"})
pc_source_status(scope_id="robot-7-data")
pc_source_target_set_enabled(scope_id="robot-7-data", target_id="...", enabled=false)
pc_source_target_delete(scope_id="robot-7-data", target_id="...")
```

- **选择 runner 和能力**：`pc_source_runners` 列出所有 runner、它们加载的插件、参数格式和最近在线时间。Dashboard 据此生成
  "选 Scope → 选 runner → 选能力 → 按参数格式填写"的表单。
- **校验与去重**：中心按插件上报的参数格式校验 `parameters`，按唯一性表达式算出目标键。同一个 Scope、runner、能力下键相同的
  目标已经存在时，返回已有目标；键相同但参数不同时返回冲突。
- **状态**：每个目标的最近尝试时间、最近成功时间、连续失败次数和最近错误，由 runner 随 checkpoint 一起汇报。
  `last_seen_at` 晚于目标的 `updated_at`，说明 runner 已经拉到最新配置。
- **停用与删除**：停用后目标不再出现在同步结果中，checkpoint 保留，恢复后从原位置继续；删除同时删除它的 checkpoint，已采集的
  Source 保留。
- **权限**：增删改目标需要该 Scope 的 Source 写权限；查看 runner 列表只需要登录；runner 只能同步和汇报自己的目标；删除 runner
  记录需要 `server.admin`。

## 5. 结果提交与断线恢复

runner 每次采集一个 Scope 中分配给它的已启用目标：

- 在服务端，core 把返回的 Source 和各目标的新 checkpoint 在一个事务中保存。
- 在端侧，runner 先通过 `POST /v1/source-observations` 提交 Source，再以旧 checkpoint 为期望值通过
  `POST /v1/connector-checkpoints/commit` 提交新 checkpoint，同时带上目标的状态。中途失败时 checkpoint 不前进，下次从旧
  checkpoint 重新采集；重新提交的 Source 身份和内容相同，由中心去重。
- `resolve` 和 Projection 在端侧执行，中心按已登记的定义清单校验，不需要加载插件代码。

## 6. 大块内容

图片、音频等大块内容怎样传到中心由插件决定，框架支持三种做法：

| 做法 | 怎么做 | 适合 |
| --- | --- | --- |
| 内嵌 | 编码后直接放进 Source 内容，受 Server 请求体上限约束 | 缩略图、短音频 |
| 插件的对象存储 | runner 上传到插件指定的对象存储，Source 中记录 URI 和摘要 | 已有对象存储的部署 |
| 经中心插件 | 同一个 Source 插件也在中心加载，通过 RFC #2579 的插件接口接收文件并存进自己的存储；Source 中记录引用 | 不希望设备直接连接对象存储 |

第三种做法中，同一个插件包有两个角色：在设备上采集并上传，在中心接收文件、执行 `resolve` 和 Projection。设备上的插件通过
`ctx.center.call_plugin(...)` 调用自己在中心的接口，凭据和地址由 runner 提供，插件接触不到。中心的 Projection 可以读取插件
自己的存储，把文件内容交给消费方；文件按内容摘要保存、写入后不变，Projection 的结果仍是确定的。

## 7. 运维

- 定义清单（Source 类型和 Projection 的声明）按插件版本登记到中心，需要 `server.admin`，由部署者在发布插件时登记一次，
  例如 `powercontext source-definitions register <插件目录>`。设备只需要所服务 Scope 的 `scope.contribute` 权限。设备启动时
  发现定义未登记，就在状态中报告。
- runner 下线后，它的记录和目标保留；在线状态通过 `last_seen_at` 判断。设备退役时由用户删除它的目标和记录。

# 参考级说明

## 1. 声明

```python
class TargetSpec(BaseModel):
    description: str                        # 面板展示的能力说明
    parameters: type[BaseModel]             # 目标参数；core 取其 JSON Schema
    identity: dict[str, tuple[Normalizer, ...]]   # 组成唯一键的参数及其规范化步骤

class Collect(BaseModel):
    schedule: Interval | Cron
    max_targets: int = 50                   # 单次调用的目标数上限
    max_items: int = 200                    # 单次调用返回的 Source 条数上限
    timeout_seconds: float = 120
    max_workers: int = 1

class SourcePlugin(BaseModel):
    definitions: Sequence[SourceDefinition]
    collect: Collect | None = None
    target: TargetSpec | None = None        # collect 不为空时必填
    scopes: tuple[str, ...] | None = None   # 服务端 runner 的 Scope 白名单
    setup: Callable[[PluginContext], Awaitable[SourceCollector]] | None = None
```

- 插件名就是能力名，必须匹配 `^[a-z][a-z0-9-]{0,63}$`。
- 规范化步骤从固定集合中选择：`strip`、`lower`、`collapse_spaces`、`remove_prefix:<值>`、`remove_suffix:<值>`。中心和 runner
  使用同一份实现。目标键是能力名加上各参数规范化后的值，按参数名排序后取 SHA-256。
- 唯一性表达式中的参数必须是 `parameters` 中的字符串字段；未列出的参数不参与唯一性。

## 2. 采集契约

```python
class CollectTarget(BaseModel):
    target_id: str
    parameters: dict[str, JsonValue]
    checkpoint: JsonValue | None            # 该目标上次提交的 checkpoint；首次为 None；只读

class CollectRequest(BaseModel):
    scope_id: str
    reason: Literal["schedule", "target_added"]
    targets: Sequence[CollectTarget]
    max_items: int

class CollectedItem(BaseModel):
    definition: str                         # definitions 中某个定义的 name
    value: BaseModel                        # 该定义 input_class 的实例

class CollectResult(BaseModel):
    def add(self, target: CollectTarget, items: Sequence[CollectedItem], *, checkpoint: JsonValue | None,
            has_more: bool = False) -> None: ...

class SourceCollector(Protocol):
    async def collect(self, request: CollectRequest, /) -> CollectResult: ...
```

- 每次调用对应一个 Scope，最多 `max_targets` 个已启用目标；目标较多时轮转，`has_more` 的目标优先进入下一次调用。
- 返回的条目总数超过 `max_items`、返回了请求中不存在的目标，都视为失败，不截断。结果中没有出现的目标，checkpoint 不变。
- Source 身份由 `resolve` 决定。重复采集同一外部状态得到相同身份时不产生重复 Source；身份相同而内容不同时，中心返回冲突，
  本次调用失败。
- 采集失败进入 Supervisor（服务端）或 runner（端侧）的退避；某个目标持续失败会让该 Scope 的这次调用失败，可以先停用该目标。

## 3. 持久化

| 数据 | 处理 |
| --- | --- |
| `pc_source_runners` | 新增：`runner`（主键）、`capabilities`（规范化 JSON：每个插件的名字、说明、参数 JSON Schema、唯一性表达式）、`capabilities_digest`、`last_seen_at` |
| `pc_source_collect_targets` | 新增：主键 `(scope_id, target_id)`；`runner`（索引）、`capability`、`target_key`、`parameters`（规范化 JSON）、`enabled`、`updated_at`、`updated_by`；唯一约束 `(scope_id, runner, capability, target_key)`；runner 写入的 `last_attempt_at`、`last_success_at`、`consecutive_failures`、`last_error_code`、`last_error_message` |
| `pc_connector_checkpoints` | 保留；目标的 checkpoint，键为 `(scope_id, target_id)` |
| `pc_sources`、Projection、Source journal、`pc_source_definition_manifests` | 保留 |
| `pc_source_write_plugins` | 删除；只提供定义的插件改由插件目录提供 |
| 旧 Source 插件管理服务的 SQLite | 随管理服务移除，不迁移 |

- 写入频率：`capabilities` 只在摘要变化时写；`last_seen_at` 每台 runner 每次同步写一次；状态列在每次采集时与 checkpoint
  同一事务写入（端侧在 checkpoint 提交请求中）。用户修改目标只写用户列，不与 runner 的写入冲突。
- 两张新表和删除通过一个新的迁移版本完成，已发布版本的迁移资源保持不变。迁移检查发现 `pc_source_write_plugins` 中仍有
  数据时拒绝升级，由部署者先在旧版本上卸载旧插件。
- 导出与恢复包含两张新表；`pc_source_runners` 中的在线时间和 `pc_source_collect_targets` 的状态列不参与导出，恢复后为空。

## 4. 接口

| 接口 | 权限 | 说明 |
| --- | --- | --- |
| `GET /v1/source-runners`（MCP `pc_source_runners`） | 已登录 | 所有 runner、它们的能力和最近在线时间 |
| `POST /v1/source-runners/{runner}/sync` | runner 本身 | 请求带能力清单；返回分配给该 runner 的全部已启用目标及其 checkpoint |
| `DELETE /v1/source-runners/{runner}` | `server.admin` | 删除 runner 记录；它仍有目标时返回冲突 |
| `POST /v1/scopes/{scope_id}/source-targets`（MCP `pc_source_add`） | Scope 的 Source 写权限 | 新建目标：`runner`、`capability`、`parameters`；校验并去重 |
| `GET /v1/scopes/{scope_id}/source-targets`（MCP `pc_source_status`） | Scope 读权限 | 目标、配置和状态 |
| `PATCH /v1/scopes/{scope_id}/source-targets/{target_id}`（MCP `pc_source_target_set_enabled`） | Scope 的 Source 写权限 | 修改 `parameters` 或启停 |
| `DELETE /v1/scopes/{scope_id}/source-targets/{target_id}`（MCP `pc_source_target_delete`） | Scope 的 Source 写权限 | 删除目标及其 checkpoint |
| `POST /v1/connector-checkpoints/commit` | 现有 | 请求增加可选的 `status`：最近尝试、成功、错误分类和信息 |

"runner 本身"指认证主体等于 runner 名，或 runner 记录登记时的主体。

移除：`/v1/source-management/*` 中的实例、配置、通道和 write 插件加载接口，MCP `pc_source_instances`、
`pc_source_instance_set_enabled`，`source load` 命令和 `powercontext.yml` / `write.yml` 清单。

## 5. 设备端上下文

设备上的 `PluginContext` 多一个 `center`：

```python
class PluginCenter(Protocol):
    async def call_plugin(self, plugin: str, method: str, path: str, /, *, json: JsonValue | None = None,
                          content: bytes | None = None, content_type: str | None = None) -> PluginResponse: ...
```

它以 runner 的凭据调用中心的插件接口（`/v1/plugins/{plugin}/api{path}`），只能调用插件自己的路由。中心的 Source 插件可以
声明 RFC #2579 的路由；它的 Projection 可以通过 `ctx.storage` 读取插件存储。

## 6. 与旧框架的对照

| 旧框架 | 本提案 |
| --- | --- |
| 管理服务及其 SQLite | 中心的两张表 |
| 实例登记（管理员） | runner 同步时自己上报，名字取自凭据主体 |
| 反向 poll / reply 通道 | runner 拉取同步 |
| `scope_ids` 实例配置 | 目标自带 Scope |
| `default_capabilities` 默认实例 | 不提供；面板由用户选择 runner |
| 设备上的 `identify` 调用 | 声明式唯一性表达式，中心计算 |
| `content.inline` 内置能力 | 现有 Source 写入接口 |
| write 插件（`pc_source_write_plugins`） | 只提供 `definitions` 的 Source 插件 |
| `examples/source_plugins` 的 tickets、ticket_chat、images | 改写为 Source 插件示例 |

# 缺点

- **多两张表**：迁移、导出恢复和两种数据库后端都要维护。
- **规范化规则受限**：唯一性只能用固定的规范化步骤，URL 规范化等复杂规则写不出来；插件可以在采集时自行合并相同对象。
- **配置生效有延迟**：等于同步间隔，默认 30 秒。
- **共用凭据的设备要手动起名**：名字相同的两台设备会拿到同一批目标，core 无法区分，只能在状态中看到交替汇报。
- **定义登记仍需要管理员**：插件发布时由部署者登记一次。

# 理由与备选

| 方案 | 取舍 |
| --- | --- |
| 采集目标存在设备本地（RFC #2549 最初的设计） | 不加表，但中心看不到、管不了设备上的目标，具身场景必须逐台登录设备 |
| 目标存成一种内置 family 的 Artifact（参考 Scope Prompt 的做法） | 不加表，修订、恢复和导出都是现成的；但"某台设备有哪些目标"需要跨 Scope 查找，没有索引 |
| 设备配置一个主 Scope，在主 Scope 中维护服务的 Scope 列表 | 不加表，但给一台设备分配新 Scope 要先找到它的主 Scope，仍然要扫描 |
| 中心通过反向通道推送配置（旧框架） | 配置即时生效，但需要长连接、租约和实例登记，是旧框架复杂度的主要来源 |
| 每个目标带修订号和"已应用修订号" | 便于并发控制和展示生效状态；但 runner 每次全量拉取、按时间判断生效已经足够，且避免了时钟和增量拉取的问题 |
| `identify_target` 写成插件代码 | 表达能力强，但插件在设备上时中心执行不了，重复只能事后发现 |
| 去掉去重 | 重复目标只会重复采集，Source 本身仍由身份去重；但面板配置需要即时提示，列表和统计也需要唯一的目标 |

# 先例

- **AWS IoT Device Shadow、Azure IoT Hub Device Twin**：期望状态由云端保存，设备上报实际状态；设备主动连接云端同步。
- **Kubernetes 的 spec 与 status**：用户写期望，控制器写观测结果，两者分开保存。
- **Airbyte 连接器协议**：连接器接收上次的 state，输出记录和新 state，由平台保存 state。
- **PowerContext 现有基础**：远程采集接口 `POST /v1/source-observations`、`/v1/connector-checkpoints/*`，定义清单登记，
  RFC #2549 的插件宿主进程与 Supervisor 调度，RFC #2579 的插件接口。

# 未决问题

| 问题 | 说明 |
| --- | --- |
| 采集节点的专用角色 | 定义登记目前需要 `server.admin`。是否增加一个只能登记定义、同步自己目标的服务器级角色 |
| runner 列表的可见范围 | 目前所有已登录用户都能看到 runner 列表和能力。是否需要按 Scope 限制 |
| 单个 runner 的目标数 | 每次全量拉取适合每台设备几个到几十个目标；服务端 runner 采集成百上千个账号时是否需要分页或增量 |
| 默认 runner | 是否允许插件或部署者为某项能力指定默认 runner，减少面板上的选择 |

# 后续可能

- 插件安装与依赖：从 PyPI、Git 地址或 wheel 安装，设备上的插件随同步下载指定版本。
- 推送通知：在同步之外提供可选的长连接，让配置更快生效。

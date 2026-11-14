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
- **拉取同步**：runner 定时到中心同步，一次取回分配给自己的全部目标，并上报自己加载的插件及其版本；中心不向设备推送，
  没有反向通道。
- **去重**：插件声明哪些参数组成目标的唯一键及其规范化规则，中心添加目标时就能识别重复。
- **结果**：Source 和 checkpoint 沿用 core 已有的远程采集接口提交；每个目标的最近状态随 checkpoint 一起汇报。
- **版本**：Source 插件显式声明版本和最低兼容版本。中心加载的版本就是指定版本；设备上的插件版本落在中心声明的兼容范围
  内，中心才接受它的请求，交给中心的插件处理。数据升级沿用 RFC #2579 的 `upgrade(ctx)`，只在中心执行。
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
plugin = SourcePlugin(version="1.2.0", definitions=(CameraEventDefinition(),), collect=Collect(...), target=..., setup=setup)
```

| 接口 | 必需 | 说明 |
| --- | --- | --- |
| `version` | 是 | 插件版本，按 PEP 440 比较大小 |
| `min_compatible_version` | 否 | 中心运行本版本时仍接受的最低设备版本，默认等于 `version` |
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
    version="1.2.0",
    min_compatible_version="1.0.0",
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
  `--name` 指定；多台设备共用一个凭据时必须分别指定。名字 `server` 保留给中心自带的 runner；一个名字第一次同步时与当时的
  凭据主体绑定，其他主体不能再以这个名字同步。
- **同步**：runner 定时（默认 30 秒）调用中心的同步接口，完成三件事：
  1. 上报自己加载的插件、每个插件的版本和加载错误，以及最近在线时间；
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

- **选择 runner 和能力**：`pc_source_runners` 列出所有 runner、它们加载的插件及版本、版本是否与中心兼容，以及最近在线时间。
  能力的说明、参数格式和唯一性表达式取自中心加载的插件。Dashboard 据此生成"选 Scope → 选 runner → 选能力 → 按参数格式
  填写"的表单；同一能力在所有 runner 上是同一个表单。
- **校验与去重**：中心按自己加载的插件版本的参数格式校验 `parameters`，按唯一性表达式算出目标键。同一个 Scope、runner、能力下键相同的
  目标已经存在时，返回已有目标；键相同但参数不同时返回冲突。
- **状态**：每个目标的最近尝试时间、最近成功时间、连续失败次数和最近错误，由 runner 随 checkpoint 一起汇报。
  `last_seen_at` 晚于目标的 `updated_at`，说明 runner 已经拉到最新配置。
- **修改**：可以修改 `parameters` 中不参与唯一性的字段和启停；唯一性表达式中的字段不能修改，要采集另一个对象就新增一个
  目标，这样 checkpoint 总是对应同一个外部对象。
- **停用与删除**：停用后目标不再出现在同步结果中，checkpoint 保留，恢复后从原位置继续；删除同时删除它的 checkpoint，已采集的
  Source 保留。
- **权限**：
  - 给设备上的 runner 增删改目标，需要该 Scope 的 Source 写权限；设备只能把结果提交到它的凭据有 `scope.contribute` 的
    Scope。
  - 给 `server` runner 增删改目标需要 `server.admin`：服务端 runner 用 Server 环境中的凭据访问外部系统，决定它采集什么
    等同于运维决定，不能交给只有某个 Scope 写权限的用户。服务端 runner 提交结果在 Server 内部完成，不需要额外权限。
  - 查看 runner 列表只需要登录；runner 只能同步和汇报自己的目标；删除 runner 记录需要 `server.admin`。

## 5. 结果提交与断线恢复

runner 每次采集一个 Scope 中分配给它的已启用目标：

- 在服务端，core 把返回的 Source 和各目标的新 checkpoint 在一个事务中保存。
- 在端侧，runner 先通过 `POST /v1/source-observations` 提交 Source，再以旧 checkpoint 为期望值通过
  `POST /v1/connector-checkpoints/commit` 提交新 checkpoint，同时带上目标的状态。中途失败时 checkpoint 不前进，下次从旧
  checkpoint 重新采集；重新提交的 Source 身份和内容相同，由中心去重。
- 设备的每个请求都带上插件版本，中心按第 8 节核对后，交给中心加载的同一插件处理。

## 6. 大块内容

图片、音频等大块内容怎样传到中心由插件决定，框架支持三种做法：

| 做法 | 怎么做 | 适合 |
| --- | --- | --- |
| 内嵌 | 编码后直接放进 Source 内容，受 Server 请求体上限约束 | 缩略图、短音频 |
| 插件的对象存储 | runner 上传到插件指定的对象存储，Source 中记录 URI 和摘要 | 已有对象存储的部署 |
| 经中心插件 | 同一个 Source 插件也在中心加载，通过 RFC #2579 的插件接口接收文件并存进自己的存储；Source 中记录引用 | 不希望设备直接连接对象存储 |

第三种做法中，同一个插件包有两个角色：在设备上采集并上传，在中心接收文件、执行 `resolve` 和 Projection。设备上的插件通过
`ctx.center.call_plugin(...)` 调用自己在中心的接口，凭据和地址由 runner 提供，插件接触不到。设备先上传文件，再提交引用它的
Source；中心在写入 Source 时执行 Projection，可以读取插件存储中的文件，结果与 Source 一起保存，与其他 Projection 相同。
交付给 Artifact 插件时只读取保存的结果，不再执行 Projection，因此之后文件被清理也不会让消费方的窗口失败。文件按内容摘要
保存、写入后不变，Projection 的结果是确定的。

## 7. 运维

- 设备使用的每个 Source 插件都要部署在中心的插件目录中。中心加载插件时登记它的定义清单（Source 类型和 Projection 的
  声明），与 RFC #2549 相同，不需要单独的登记命令。设备只需要所服务 Scope 的 `scope.contribute` 权限。
- runner 下线后，它的记录和目标保留；在线状态通过 `last_seen_at` 判断。设备退役时由用户删除它的目标和记录。

## 8. 版本与升级

中心加载的插件版本是这项能力的指定版本，设备的版本与它比对；数据格式是否兼容由插件负责。

- **兼容范围**：设备上的插件版本满足 `中心的 min_compatible_version <= 设备版本 <= 中心的 version` 时，中心接受它的同步、
  Source 提交、checkpoint 提交和 `call_plugin` 请求，交给中心的插件处理；否则拒绝并返回 `plugin_version_incompatible`。
  同步时，不兼容插件的目标不下发，同一 runner 上其他插件照常同步；runner 列表显示哪些设备需要升级。中心没有加载该插件时，
  同样拒绝。
- **先升级中心，再升级设备**：比中心新的设备版本也被拒绝，因为中心无法判断更新的代码是否兼容。中心升级后，兼容范围内的
  旧设备继续工作，可以逐台升级。中心有多个实例时，各实例按自己加载的版本核对，滚动升级期间同一台设备的请求可能在一个
  实例被接受、在另一个被拒绝；应先让所有中心实例完成升级，再升级设备。runner 把 `plugin_version_incompatible` 当作可以
  重试的错误，在下一次同步时重试。
- **兼容的含义**：插件声明 `min_compatible_version` 时，承诺中心的新版本能处理范围内设备提交的 Source；新版本仍需读取
  旧版本的 Source 时，继续声明旧版本的定义（RFC #2579）。设备从中心读取的只有目标参数：参数按中心版本的格式填写，可能
  带有旧设备不认识的字段，设备端的参数模型必须忽略未知字段（Pydantic 的默认行为，不能设置 `extra="forbid"`）。新参数
  必须在所有设备上生效时，提高 `min_compatible_version`。checkpoint 由同一台设备写入和读取，不跨中心与设备。
- **数据升级**：中心每次加载一个插件版本时调用 `upgrade(ctx)`，规则与 RFC #2579 相同。设备不调用 `upgrade`：core 不在
  设备上为插件保存数据，插件自己的本地文件在 `setup(ctx)` 中检查和迁移。

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
    version: str                            # PEP 440
    min_compatible_version: str | None = None   # 默认等于 version
    definitions: Sequence[SourceDefinition]
    collect: Collect | None = None
    target: TargetSpec | None = None        # collect 不为空时必填
    scopes: tuple[str, ...] | None = None   # 服务端 runner 的 Scope 白名单
    setup: Callable[[PluginContext], Awaitable[SourceCollector]] | None = None
```

- 插件名就是能力名，必须匹配 `^[a-z][a-z0-9-]{0,63}$`。
- `version` 和 `min_compatible_version` 必须是合法的 PEP 440 版本，且 `min_compatible_version <= version`，否则插件加载失败。
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
    def fail(self, target: CollectTarget, *, code: str, message: str) -> None: ...   # 只有这个目标失败

class SourceCollector(Protocol):
    async def collect(self, request: CollectRequest, /) -> CollectResult: ...
```

- 每次调用对应一个 Scope，最多 `max_targets` 个已启用目标；目标较多时轮转，`has_more` 的目标优先进入下一次调用。
- 返回的条目总数超过 `max_items`、返回了请求中不存在的目标，都视为失败，不截断。结果中没有出现的目标，checkpoint 不变。
- Source 身份由 `resolve` 决定。重复采集同一外部状态得到相同身份时不产生重复 Source；身份相同而内容不同时，中心返回冲突，
  本次调用失败。
- 单个目标失败时，插件用 `fail` 报告：该目标的 checkpoint 不变，状态列记录失败，同一次调用中其他目标的结果照常提交。
  `collect` 抛出异常、返回值不合格或提交失败时，整次调用失败，进入 Supervisor（服务端）或 runner（端侧）的退避。

## 3. 持久化

| 数据 | 处理 |
| --- | --- |
| `pc_source_runners` | 新增：`runner`（主键）、`capabilities`（规范化 JSON：每个插件的名字、版本和加载错误）、`capabilities_digest`、`last_seen_at` |
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
| `GET /v1/source-runners`（MCP `pc_source_runners`） | 已登录 | 所有 runner、它们的能力、版本是否与中心兼容和最近在线时间；能力的说明和参数格式取自中心的插件 |
| `POST /v1/source-runners/{runner}/sync` | runner 本身 | 请求带能力清单（插件版本和加载错误）；返回分配给该 runner、已启用且版本兼容的全部目标及其 checkpoint |
| `DELETE /v1/source-runners/{runner}` | `server.admin` | 删除 runner 记录；它仍有目标时返回冲突 |
| `POST /v1/scopes/{scope_id}/source-targets`（MCP `pc_source_add`） | Scope 的 Source 写权限；runner 为 `server` 时需要 `server.admin` | 新建目标：`runner`、`capability`、`parameters`；校验并去重 |
| `GET /v1/scopes/{scope_id}/source-targets`（MCP `pc_source_status`） | Scope 读权限 | 目标、配置和状态 |
| `PATCH /v1/scopes/{scope_id}/source-targets/{target_id}`（MCP `pc_source_target_set_enabled`） | 同新建 | 修改 `parameters` 中不参与唯一性的字段或启停 |
| `DELETE /v1/scopes/{scope_id}/source-targets/{target_id}`（MCP `pc_source_target_delete`） | 同新建 | 删除目标及其 checkpoint |
| `POST /v1/source-observations` | 现有 | 来自 runner 的请求增加插件名和版本，按兼容范围核对 |
| `POST /v1/connector-checkpoints/commit` | 现有 | 请求增加可选的 `status`：最近尝试、成功、错误分类和信息；来自 runner 的请求增加插件名和版本，按兼容范围核对 |

"runner 本身"指认证主体等于 runner 名，或 runner 记录登记时的主体。

`call_plugin` 发出的请求同样带上插件版本。

移除：`/v1/source-management/*` 中的实例、配置、通道和 write 插件加载接口，MCP `pc_source_instances`、
`pc_source_instance_set_enabled`，`source load` 命令和 `powercontext.yml` / `write.yml` 清单。中心加载插件时自动登记定义，不提供单独的定义登记命令。

## 5. 设备端上下文

设备上的 `PluginContext` 多一个 `center`：

```python
class PluginCenter(Protocol):
    async def call_plugin(self, plugin: str, method: str, path: str, /, *, json: JsonValue | None = None,
                          content: bytes | None = None, content_type: str | None = None) -> PluginResponse: ...
```

它以 runner 的凭据调用中心的插件接口（`/v1/plugins/{plugin}/api{path}`），只能调用插件自己的路由。中心的 Source 插件可以
声明 RFC #2579 的路由；它的 Projection 在写入 Source 时可以通过 `ctx.storage` 读取插件存储（第 6 节）。

设备上 core 不为插件保存数据，`ctx.storage` 不可用，访问时抛出异常；插件的本地文件由插件自己管理。`ctx.center` 只在设备
上存在，插件据此判断自己运行在哪一端。

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
- **中心必须能加载设备上的每个 Source 插件**：只在设备上使用的库不能在 `plugin.py` 顶部导入，否则中心加载失败；按端
  拆分依赖留给后续的插件安装与依赖提案。
- **必须先升级中心**：设备先于中心升级时，新版本设备的请求被拒绝，直到中心升级。

# 理由与备选

| 方案 | 取舍 |
| --- | --- |
| 采集目标存在设备本地（RFC #2549 最初的设计） | 不加表，但中心看不到、管不了设备上的目标，具身场景必须逐台登录设备 |
| 目标存成一种内置 family 的 Artifact（参考 Scope Prompt 的做法） | 不加表，修订、恢复和导出都是现成的；但"某台设备有哪些目标"需要跨 Scope 查找，没有索引 |
| 设备配置一个主 Scope，在主 Scope 中维护服务的 Scope 列表 | 不加表，但给一台设备分配新 Scope 要先找到它的主 Scope，仍然要扫描 |
| 中心通过反向通道推送配置（旧框架） | 配置即时生效，但需要长连接、租约和实例登记，是旧框架复杂度的主要来源 |
| 每个目标带修订号和"已应用修订号" | 便于并发控制和展示生效状态；但 runner 每次全量拉取、按时间判断生效已经足够，且避免了时钟和增量拉取的问题 |
| `identify_target` 写成插件代码 | 表达能力强，但插件在设备上时中心执行不了，重复只能事后发现 |
| 每台 runner 上报自己的参数格式，目标按所属 runner 的格式校验 | 不要求中心先升级；但同一能力在不同设备上表单不同，格式变化后要逐个重新校验目标，也无法判断设备版本是否兼容 |
| 插件提供参数迁移函数，由 core 改写目标参数 | 不兼容的格式可以自动升级；但会让 runner 回写用户填写的参数，与用户的修改冲突；提高 `min_compatible_version` 已经能约束设备版本 |
| 设备也调用 `upgrade(ctx)` | 与中心对称；但 core 在设备上没有插件数据，同一份 `upgrade` 在两端执行还要求插件区分自己在哪一端 |
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
| runner 列表的可见范围 | 目前所有已登录用户都能看到 runner 列表和能力。是否需要按 Scope 限制 |
| 单个 runner 的目标数 | 每次全量拉取适合每台设备几个到几十个目标；服务端 runner 采集成百上千个账号时是否需要分页或增量 |
| 默认 runner | 是否允许插件或部署者为某项能力指定默认 runner，减少面板上的选择 |

# 后续可能

- 插件安装与依赖：从 PyPI、Git 地址或 wheel 安装，设备上的插件随同步下载中心指定的版本；按中心和设备分别声明依赖。
- 推送通知：在同步之外提供可选的长连接，让配置更快生效。

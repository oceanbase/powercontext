---
title: 插件接口
description: Source 和 Artifact 插件各自实现少量接口，由 PC Server 通过 Supervisor 在需要时调用；调用方只使用现有的 Source 与 Artifact 接口，core 只管理两类核心数据及其消费进度，不托管插件生命周期。
---

- Proposal Name: `plugin_interfaces`
- Start Date: 2026-11-08
- RFC PR: [oceanbase/powercontext#2549](https://github.com/oceanbase/powercontext/pull/2549)

# 概要

本提案重新定义 PowerContext 的第三方插件模型：插件只实现几个固定接口，PC Server 在需要时调用；调用方不直接调用插件，
而是像使用内置能力一样使用现有的 Source 和 Artifact 接口。

- **两类插件**：
  - **Source 插件**提供 Source 定义，需要持续采集时再实现 `identify_target` 和 `collect`。
  - **Artifact 插件**实现 `generate`，把尚未消费的 Source 加工成 Artifact。
- **插件形态**：一个插件是一个目录，包含 `plugin.py`，其中把一个声明对象（`SourcePlugin` 或 `ArtifactPlugin`）赋给
  `plugin`。声明写在代码里，没有单独的清单文件；部署相关的设置由插件从环境变量读取。
- **调用方式**：
  - 写入 Source、添加采集目标，使用现有的 Source 接口。
  - 读取、检索和修改 Artifact，使用现有的 Artifact 接口。
  - 遗忘、恢复、编辑等用户操作由 core 直接执行，不经过插件。
- **执行**：`collect` 和 `generate` 在 Supervisor 管理的 Worker 子进程中执行，沿用现有的隔离、超时、并发预算、
  领导租约和失败退避。
- **写入**：Artifact 插件在 `async with writer.transaction()` 块中执行写操作，块结束时 core 把写入、消费游标和
  完成确认一起提交，异常则整体回滚。插件读不到也改不了游标。
- **core 的职责**：只管理 `pc_sources`、`pc_artifacts`（含 heads、lineage、合并记录、生命周期、handle 和 tags）及其
  消费进度。插件可以有自己的索引和存储，由插件自己管理。
- **生命周期**：插件在 Server 启动时按目录加载，变更需要重启。Server 不保存插件的启用状态、注册代次或管理阶段。

本提案取代 [Source 插件框架 RFC #2012](2012-source-plugin-system.md) 和 [Artifact 插件框架 RFC #2175](2175-artifact-plugin-system.md)
的插件契约与管理机制。两者的实现尚未作为稳定接口发布，本提案不保留兼容：旧的插件清单、管理接口、管理服务和插件管理相关的
表直接移除，旧 Artifact 插件写入的自定义 Artifact 数据不迁移。

本文描述目标设计，不代表主线或发布版本已经可用。接口名称和代码示例用于说明契约，最终语法以实现为准。

# 动机

现有两套插件框架都让 PowerContext 替第三方插件承担了大量生命周期管理：

| 框架 | Server 为插件承担的工作 |
| --- | --- |
| Artifact 插件（#2175） | 在 `pc_artifact_processing_binding_states` 中维护 `desired_state`、`phase`、`binding_generation`、`last_error`；七种管理状态及其转换；`enable-all / reload / enable / disable / unload` 管理接口与 `expected_generation` 并发校验；用注册代次屏蔽旧 Worker 提交；在 `pc_artifact_family_schemas` 中固化内容 schema 和索引定义，变更返回 409；框架负责自定义声明式索引的构建、重建和可用状态 |
| Source 插件（#2012） | 独立的插件管理服务及其 SQLite（实例、capability、target、期望状态、提交记录）；每个插件实例一个 Runtime 进程；管理服务与 Server 之间的 poll / reply 通道和能力目录同步；`source load` 部署命令；实例启停 MCP 工具 |

这些机制的代价由所有人承担：

- **插件作者**需要理解 binding、generation、schema 固化规则、索引声明和管理通道等框架概念，才能写出一个插件。
- **部署者**需要运行多个进程、维护两个数据库、编写清单文件，并通过管理 API 让插件进入可运行状态。
- **调用方**需要区分内置能力和插件能力，使用不同的接口。
- **维护者**需要为第三方代码维护状态机、并发屏蔽、schema 和索引治理以及恢复路径，这部分代码的规模和测试面已经超过
  插件业务本身。

插件真正需要的是：在合适的时机被调用，拿到需要的输入，把结果交回 core。调度、隔离、消费进度和事务提交是 core
本来就有的能力，Supervisor 已经为内置 Artifact 提供了这些能力。Dify 等系统的插件模型也说明，宿主定义接口和调用时机、
插件只实现接口，足以支撑大量第三方扩展。

本提案的目标：

1. 插件作者只需写一个 `plugin.py`：一个声明对象和几个方法，不需要理解插件管理的内部机制。
2. 调用方不感知插件：Source 和 Artifact 的现有接口对插件产生的数据同样适用。
3. 插件代码在隔离的 Worker 进程中执行，插件故障不影响 Server 和其他插件。
4. 插件结果进入 `pc_sources` / `pc_artifacts`，与消费进度原子提交。
5. core 只管理这两类核心数据及其消费进度；内容结构、索引和其他存储交给插件自己。
6. 插件对核心数据的写入只经过 core 提供的写入器，覆盖 Artifact 的全部通用写操作，并沿用 core 现有的校验规则。
7. Artifact 生成可以按调度执行，也可以由新 Source 触发；采集目标可以在运行中增减。
8. 用户只需部署一个 Server；插件随 Server 启动加载。

# 指南级说明

## 1. 总体设计

```mermaid
flowchart LR
    Caller["应用 / Agent / 宿主"]
    subgraph Server["PC Server（core）"]
        SourceAPI["现有 Source 接口<br/>写入 Source、添加采集目标"]
        ArtifactAPI["现有 Artifact 接口<br/>读取、检索、修改"]
        Sup["Supervisor<br/>调度 / 触发 / 预算 / 退避"]
        Core[("core 数据<br/>pc_sources / pc_artifacts<br/>采集目标 / checkpoint / 游标")]
    end
    subgraph SW["Worker：Source 插件"]
        Collect["collect(request)"]
    end
    subgraph AW["Worker：Artifact 插件"]
        Generate["generate(request, writer)"]
        Tx["writer.transaction()"]
        Generate --> Tx
    end
    Caller --> SourceAPI
    Caller --> ArtifactAPI
    SourceAPI -->|"resolve / Projection（插件定义）"| Core
    ArtifactAPI <--> Core
    Sup -->|"目标与 checkpoint"| Collect
    Collect -->|"Source 与新 checkpoint，一次提交"| Core
    Core -->|"新 Source 触发"| Sup
    Sup -->|"未消费的 Source 窗口"| Generate
    Tx -->|"Artifact 写入、游标、确认，一次提交"| Core
    Collect -.-> Ext["外部系统"]
    Generate -.-> Own[("插件自有存储 / 模型")]
```

图中有两条独立的路径，一次 Worker 调用只执行其中一个插件：

- **采集路径**：Supervisor 按调度派发 Source 插件的 Worker，传入该 Scope 已启用的采集目标及其 checkpoint；插件从外部
  系统读取数据并返回 Source；core 校验后把 Source 和新 checkpoint 在一个事务中保存。
- **生成路径**：新 Source 进入 journal 后，关注这些 Source 的 Artifact 插件被标记为待处理；Supervisor 派发 Worker，传入
  该 Scope 尚未消费的 Source 窗口和写入器；插件在写事务块中写入 Artifact，core 连同游标和确认一起提交。

调用方只和 core 的现有接口打交道：

| 调用方要做的事 | 使用的接口 | 插件参与的方式 |
| --- | --- | --- |
| 提交一条 Source | 现有 Source 写入接口 | core 调用插件定义的 `resolve` 和 Projection 完成校验与转换 |
| 添加、查看、停用采集目标 | 现有 Source 管理接口 | core 调用插件的 `identify_target` 规范化输入 |
| 浏览、读取、检索 Artifact | 现有 Artifact 读取与检索接口 | 不参与 |
| 编辑、打 tag、合并、遗忘、恢复、撤销合并 | 现有 Artifact 写接口、tags 接口与通用合并接口 | 不参与；插件在下一次调用时看到新状态 |

职责划分：

| 职责 | 归属 |
| --- | --- |
| 认证、Scope 授权、公开 HTTP / MCP 接口、采集目标与 checkpoint 的保存、用户对 Artifact 的修改 | core |
| 何时调用：调度、触发合并、Worker 预算、超时、退避、领导租约 | core（现有 Supervisor） |
| 读取输入、提供受约束的写事务、在同一事务中提交核心数据 / 游标 / checkpoint / 完成确认 | core（Worker 中的插件执行器） |
| Source 定义与 Projection 计算、采集目标的识别与规范化、外部数据读取 | Source 插件 |
| Artifact 内容结构、生成算法、插件自有的索引与存储及其一致性 | Artifact 插件 |
| 插件代码、依赖、环境变量和插件自有存储的部署 | 部署者 |

插件代码只做一件事：给定输入，产出结果或抛出异常。抛出异常即失败，core 不提交任何核心数据，下次重试同一输入。

## 2. 插件的形态

Server 从 `POWERCONTEXT_SERVER_PLUGINS_DIR` 加载插件。每个直接子目录是一个插件，目录名就是插件名；目录中必须有
`plugin.py`，可以有其他模块：

```text
/opt/powercontext/plugins/
├── x-posts/
│   ├── plugin.py
│   └── definition.py
└── incident-report/
    └── plugin.py
```

`plugin.py` 把一个声明对象赋给模块级变量 `plugin`：

```python
plugin = ArtifactPlugin(family="incident-report", ..., create=create)   # 或 SourcePlugin(...)
```

声明对象回答 core 在运行插件代码之前需要知道的问题：插件提供什么（Source 类型或 Artifact family）、消费什么、何时调用、
资源上限多少。它是普通的 Python 对象，由 Pydantic 校验；没有 YAML 或其他清单文件。

- **部署设置**：凭据、外部服务地址、模型名等随部署变化的值，由插件在 `create` 中从环境变量读取。Server 不保存、
  不传递也不记录这些值。
- **启用与停用**：目录存在且检查通过，插件就可用；需要停用时移出目录并重启 Server。
- **检查**：Server 启动时在有时限的子进程中导入 `plugin.py`，校验声明，不调用 `create`。检查失败只影响该插件。

## 3. Source 插件

### 3.1 要实现的接口

| 接口 | 必需 | 说明 |
| --- | --- | --- |
| `definitions` | 是 | `SourceDefinition` 序列，沿用现有的 Source 定义：`input_class`、`resolve` 和各 Projection |
| `target_input` | 采集时必需 | Pydantic 模型，校验调用方添加目标时提交的输入 |
| `identify_target(value) -> TargetIdentity` | 采集时必需 | 纯函数，把输入规范化为稳定的目标键和规范化输入 |
| `create(ctx) -> SourceCollector` | 采集时必需 | 工厂，在 Worker 中每次调用执行一次，返回实现了 `collect` 的对象 |
| `collect(request) -> CollectResult` | 采集时必需 | 按目标和 checkpoint 读取外部数据，返回 Source 和新的 checkpoint |

只提供 `definitions` 的插件只扩展 Source 类型：调用方通过现有的 Source 写入接口提交内容，core 调用定义完成校验、
转换和保存。

### 3.2 声明

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `definitions` | 必填 | 见上表 |
| `collect` | `None` | 采集配置；为空表示只提供定义 |
| `collect.schedule` | 必填 | `Interval(seconds=...)` 或 `Cron(expression=..., timezone=...)` |
| `collect.max_targets` | 50 | 单次调用携带的目标数上限 |
| `collect.max_items` | 200 | 单次调用返回的 Source 条数上限 |
| `collect.timeout_seconds` | 120 | Worker 总时限 |
| `collect.max_workers` | 1 | 跨 Scope 的并发上限 |
| `target_input`、`identify_target`、`create` | 采集时必填 | 见上表 |
| `scopes` | `None` | 可服务的 Scope 名单；为空表示所有 Scope |

### 3.3 示例

下面的插件每 5 分钟采集各目标账号在 X 上的新帖子：

```python
import os

from pydantic import BaseModel

from powercontext.plugins import (
    Collect, CollectedItem, CollectRequest, CollectResult, Interval, PluginContext, SourcePlugin, TargetIdentity,
)

from definition import PostCapture, XPostDefinition


class TargetInput(BaseModel):
    account: str


def identify_target(value: TargetInput) -> TargetIdentity:
    account = value.account.strip().removeprefix("@").lower()
    if not account:
        raise ValueError("account must not be blank")
    return TargetIdentity(key=f"account:{account}", input={"account": account})


class XCollector:
    def __init__(self, client: XClient) -> None:
        self.client = client

    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        for target in request.targets:
            since = (target.checkpoint or {}).get("since_id")
            page = await self.client.user_posts(target.input["account"], since_id=since)
            result.add(target, [CollectedItem(definition="x-post", value=PostCapture(**p)) for p in page.posts],
                       checkpoint={"since_id": page.newest_id or since})
        return result


async def create(ctx: PluginContext) -> XCollector:
    client = await ctx.enter_async_context(XClient(os.environ["X_BEARER_TOKEN"]))
    return XCollector(client)


plugin = SourcePlugin(
    definitions=(XPostDefinition(),),
    collect=Collect(schedule=Interval(seconds=300), timeout_seconds=120),
    target_input=TargetInput,
    identify_target=identify_target,
    create=create,
)
```

### 3.4 调用方怎么使用

调用方使用现有的 Source 管理接口，不需要知道这是插件提供的能力：

```text
pc_source_add(scope_id="team-a", capability="x-posts", input={"account": "@PowerContextAI"})
pc_source_status(scope_id="team-a", target_id="...")
pc_source_target_set_enabled(scope_id="team-a", target_id="...", enabled=false)
```

- **添加**：core 用 `target_input` 校验输入，调用 `identify_target` 得到键和规范化输入。同一账号得到同一个键，复用已有
  目标。新目标保存为启用状态并立即请求一次采集，不需要重启。
- **checkpoint**：每个目标的 checkpoint 是只读输入，插件通过返回值提出新的 checkpoint。core 把本次返回的所有 Source
  与各目标的新 checkpoint 在同一事务中提交；`collect` 抛出异常或超时则不提交任何内容，下一次调用仍拿到旧 checkpoint。

## 4. Artifact 插件

### 4.1 要实现的接口

| 接口 | 必需 | 说明 |
| --- | --- | --- |
| `create(ctx) -> ArtifactGenerator` | 是 | 工厂，在 Worker 中每次调用执行一次 |
| `generate(request, writer) -> None` | 是 | 读取 `request.sources`（未消费的 Source 窗口），在写事务块中写入 Artifact |

插件只负责生成。用户对 Artifact 的修改由 core 直接执行（见 4.4），插件不需要实现命令处理。

### 4.2 声明

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `family` | 必填 | 插件拥有的 Artifact family，不能与内置 family 或其他插件重复 |
| `inputs` | `None` | `Inputs(source_types=..., projection=..., max_sources=50)`：消费的 Source 类型、每条 Source 交付的 Projection、单个窗口的条数上限；为空表示不消费 Source |
| `on_sources` | `False` | 新 Source 到达后触发；需要 `inputs` |
| `schedule` | `None` | `Interval(seconds=...)` 或 `Cron(expression=..., timezone=...)`；与 `on_sources` 至少配置一项 |
| `scopes` | `None` | 可服务的 Scope 名单；不消费 Source 的插件必须填写 |
| `search_fields` | `None` | 参与全文检索的顶层内容字段；为空表示所有顶层字符串字段 |
| `timeout_seconds` | 300 | 整次调用的时限 |
| `transaction_timeout_seconds` | 30 | 写事务块的时限，不超过 `timeout_seconds` |
| `max_workers` | 1 | 跨 Scope 的并发上限 |
| `create` | 必填 | 见上表 |

### 4.3 示例

下面的插件为每条工单 Source 生成一份报告：先用自己的向量索引查找相似的已有报告，命中则修订那份报告，否则新建；
同时把报告写进自己的向量索引。

```python
import os

from powercontext.plugins import ArtifactPlugin, ArtifactWriter, GenerateRequest, Inputs, PluginContext
from powercontext.sources import TextEvidence


class IncidentReports:
    def __init__(self, index: VectorIndex, model: Summarizer) -> None:
        self.index, self.model = index, model

    async def generate(self, request: GenerateRequest, writer: ArtifactWriter) -> None:
        # 1. 耗时工作放在写事务之外：模型调用、向量计算
        planned = []
        for source in request.sources:
            evidence = TextEvidence.model_validate(source.projection)
            summary = await self.model.summarize(evidence.content)
            planned.append((source, summary, await self.model.embed(summary)))

        # 2. 短写事务：块内读取能看到本块已写入的内容
        async with writer.transaction() as tx:
            for source, summary, vector in planned:
                match = await self.index.nearest(request.scope_id, vector, threshold=0.92)
                current = await tx.get(match.artifact_id) if match else None
                artifact_id = current.ref.artifact_id if current else writer.stable_id(
                    "report", source.ref.source_type, source.ref.source_id)
                written = await tx.put(artifact_id, content={"title": "工单报告", "summary": summary},
                                       source_refs=[source.ref])
                # 拿到真实的 artifact_id 与 revision 后写入自有索引，按 ID upsert
                await self.index.upsert(request.scope_id, written.ref.artifact_id, written.ref.revision, vector)
        # 块正常结束：core 提交 Artifact 写入、游标推进与完成确认


async def create(ctx: PluginContext) -> IncidentReports:
    index = await ctx.enter_async_context(VectorIndex.connect(os.environ["REPORT_INDEX_URL"]))
    return IncidentReports(index, Summarizer())


plugin = ArtifactPlugin(
    family="incident-report",
    inputs=Inputs(source_types=("example-ticket",), projection="powercontext.text-evidence@1"),
    on_sources=True,
    search_fields=("title", "summary"),
    create=create,
)
```

块内抛出异常时，core 回滚块内全部 Artifact 写入，游标不动，下次重试同一窗口。插件自有索引不在 core 的事务中：
插件按稳定 ID upsert，重试时会覆盖上一次留下的条目；读取自有索引时以 core 的当前数据为准（见参考级说明 3.5）。

### 4.4 调用方怎么使用

调用方使用现有的 Artifact 接口访问插件 family。读取接口直接读 `pc_artifacts`，写接口由 core 执行，与插件写入复用同一套
core 实现，都不运行插件代码：

| 用途 | 接口 |
| --- | --- |
| 浏览 | `GET /v1/scopes/{scope_id}/artifacts/{family}` |
| 读取当前内容、指定 revision、revision 历史、tags | `GET .../{artifact_id}`、`GET .../{artifact_id}/revisions/{revision}`、`GET .../{artifact_id}/revisions`、`GET .../{artifact_id}/tags` |
| 文本检索 | `POST /v1/scopes/{scope_id}/artifacts/{family}/search` |
| 编辑（提交完整内容，`If-Match` 校验当前 revision） | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` |
| 修改 tags | `PUT .../{artifact_id}/tags`；按 tag 查询用 `POST /v1/scopes/{scope_id}/artifact-tags/query` |
| 读取治理状态（遗忘、合并前取得 `state_version`） | `GET .../{artifact_id}/governance` |
| 合并 | `POST /v1/scopes/{scope_id}/artifacts/{family}/merges` |
| 遗忘 | `POST .../{artifact_id}/forget` |
| 恢复、恢复到旧 revision、撤销合并 | `POST .../{artifact_id}/restorations` |
| 补充知识 | 写入一条 Source，由插件在下一次调用中处理 |

修改同步返回结果。插件在下一次调用中通过写入器读到修改后的状态。

## 5. 调度与触发

插件的调度和触发都交给现有的 Artifact processing Supervisor。每个插件在启动时注册为一个 Supervisor binding，
binding 名为 `plugin:<name>`，调度键是 `(binding, Scope)`。

Artifact 插件：

| 声明 | 何时派发 Worker | `request.reason` |
| --- | --- | --- |
| `schedule=Interval(...)` 或 `schedule=Cron(...)` | 到期时，对有待处理工作的 Scope | 有新 Source 时为 `sources`，否则为 `schedule` |
| `on_sources=True` | Scope 的 Source journal 超过该插件的游标，约 2 秒内派发 | `sources` |

- 配置了 `inputs` 的插件，每次调用都附带该 Scope 尚未消费的 Source 窗口；窗口中没有匹配的 Source 时不调用插件，
  直接推进游标并确认。
- 未配置 `inputs` 的插件（例如每天整理一次已有 Artifact）不消费 Source，到期即对 `scopes` 中的每个 Scope 调用一次；
  第一版只支持 `Interval`。
- `Cron` 只能用于配置了 `inputs` 的插件，且不能与 `on_sources` 同时使用：Supervisor 的每个 binding 只接受一种
  interval 或 cron 策略。

Source 插件的 `collect` 按 `collect.schedule` 调用；添加或恢复目标时也会请求一次采集。

同一插件、同一 Scope 同时最多运行一个 Worker。运行期间到达的触发由 Supervisor 合并成结束后的一次调用。

## 6. 运维

- **启动**：Server 逐个检查插件目录。某个插件检查失败（缺少 `plugin.py`、导入失败、声明无效、family 或 Source 类型
  与内置或其他插件冲突）只影响该插件，Server 和其他插件正常启动。
- **查看**（都需要 `server.admin`，只读，数据都来自启动检查或数据库，任何 Server 角色都给出相同结果）：
  - `GET /v1/plugins`、`GET /v1/plugins/{name}`：每个插件的检查结果、类型、family 或 Source 类型、消费的 Source 类型、
    触发方式和 Scope 范围。
  - `GET /v1/plugins/{name}/scopes`：每个 Scope 是否有待处理的工作、已消费到的 journal 位置和当前 journal 位置。
  - 调用失败的原因通过日志和指标排查，不在接口中保存。
- **变更**：修改代码后重启 Server。Server 不监视文件，也不在运行中重新加载插件。采集目标是业务数据，增减不需要重启。
- **停用**：从插件目录移走后重启。已保存的 Source、Artifact、采集目标、游标和 checkpoint 保留并可继续读取；重新部署
  同名插件时从原位置继续。插件自有存储由部署者保留或清理。

# 参考级说明

## 1. 公共契约

公开接口位于新模块 `powercontext.plugins`，替换现有的 `powercontext.artifact_plugins` 和 `powercontext.source_management`
中面向插件作者的部分。接口以 `typing.Protocol` 和 Pydantic 模型表达。

### 1.1 声明与加载

```python
class Interval(BaseModel):
    seconds: float                        # > 0

class Cron(BaseModel):
    expression: str
    timezone: str = "Asia/Shanghai"

class ArtifactPlugin(BaseModel):          # 字段见指南级说明 4.2
    family: str
    inputs: Inputs | None = None
    ...
    create: Callable[[PluginContext], Awaitable[ArtifactGenerator]]

class SourcePlugin(BaseModel):            # 字段见指南级说明 3.2
    definitions: Sequence[SourceDefinition]
    collect: Collect | None = None
    ...
```

- 插件名取自目录名，必须匹配 `^[a-z][a-z0-9-]{0,63}$`。
- 声明在构造时校验，包括调度组合、`inputs` 与 `scopes` 的依赖、时限关系和 family 命名。`create` 必须是 async 函数。
- 声明中除 `create`、`identify_target`、`definitions` 等代码对象外的部分可以序列化，Server 在检查子进程中得到它，
  用于注册 binding 和回答运维查询；Worker 每次调用重新导入 `plugin.py`，声明与启动时不一致则调用失败，提示重启 Server。

### 1.2 上下文

```python
class PluginContext:
    name: str                          # 插件名
    scope_id: str                      # 本次调用的 Scope
    plugin_dir: Path                   # 插件目录（只读约定）
    logger: logging.Logger             # 已绑定插件名的 logger

    async def enter_async_context(self, cm: AbstractAsyncContextManager[T]) -> T: ...
```

- 工厂在每次 Worker 调用中执行一次，实例不跨调用复用。需要关闭的资源（包括插件自有存储的连接）通过
  `ctx.enter_async_context` 注册，调用结束时由执行器释放。
- 插件得不到 core 数据库的连接，也没有游标或 checkpoint 的写入接口。插件对核心数据的写入只有两条路径：Source 插件的
  `collect` 返回值，和 Artifact 插件的 `ArtifactWriter`。

## 2. Source 插件契约

```python
class TargetIdentity(BaseModel):
    key: str                          # 稳定的目标键，同一外部资源始终相同
    input: dict[str, JsonValue]       # 规范化后的输入，保留所有影响采集的参数

class CollectTarget(BaseModel):
    key: str
    input: dict[str, JsonValue]
    checkpoint: JsonValue | None      # 该目标上次成功提交的 checkpoint；首次为 None；只读

class CollectRequest(BaseModel):
    scope_id: str
    reason: Literal["schedule", "target_added"]
    targets: Sequence[CollectTarget]  # 本次要采集的已启用目标
    max_items: int

class CollectedItem(BaseModel):
    definition: str                   # definitions 中某个定义的 name
    value: BaseModel                  # 该定义 input_class 的实例

class TargetResult(BaseModel):
    items: Sequence[CollectedItem]
    checkpoint: JsonValue | None
    has_more: bool = False

class CollectResult(BaseModel):
    targets: dict[str, TargetResult] = {}       # 键为目标键
    def add(self, target: CollectTarget, items: Sequence[CollectedItem], *, checkpoint: JsonValue | None) -> None: ...

class SourceCollector(Protocol):
    async def collect(self, request: CollectRequest, /) -> CollectResult: ...
```

- **定义注册**：`definitions` 中的 Source 类型与 Projection 在启动时注册到现有的 Source 定义清单，与内置类型和其他插件
  冲突时检查失败。
- **写入**：调用方通过现有的 Source 写入接口（`POST /v1/scopes/{scope_id}/sources`、MCP `pc_source_add` 的 write 用法）
  提交内容。core 按 `definition.input_class` 校验输入，调用 `resolve` 得到 Source，计算各 Projection 后保存，语义与内置
  Source 写入一致。这一步在 Server 进程中同步执行，有超时限制。
- **collect**：每次调用对应一个 Scope，最多携带 `max_targets` 个已启用目标；目标较多时按目标键轮转，`has_more` 的目标
  优先进入下一次调用。执行器对返回的每条 Source 执行与写入相同的校验、`resolve` 和 Projection，然后在一个事务中保存全部
  Source、写入各目标的 checkpoint 和最近采集时间，并确认本次调用。任一条失败则整次不提交。条目总数超过 `max_items`、
  返回了请求中不存在的目标键，都视为失败，不截断。结果中没有出现的目标，checkpoint 保持不变。
- **Source 身份**：由 `resolve` 决定。重复采集同一外部状态得到相同身份时，沿用现有去重规则，不产生重复 Source。

**采集目标**是 core 保存的业务数据，与 Source 一样不属于插件生命周期状态。管理接口复用现有的 Source 管理接口：

| 操作 | 接口 | 说明 |
| --- | --- | --- |
| 添加 | MCP / HTTP `pc_source_add`（`capability` 为插件名） | 用 `target_input` 校验输入，调用 `identify_target` 得到键和规范化输入。同键且规范化输入相同则返回已有目标；同键但输入不同返回冲突。新目标保存为启用状态并请求一次采集 |
| 查看 | MCP / HTTP `pc_source_status` | 目标键、规范化输入、启用状态、最近成功采集时间、最近失败分类 |
| 停用 / 恢复 | MCP / HTTP `pc_source_target_set_enabled` | 停用后不再进入 collect 请求，checkpoint 和已采集的 Source 保留；恢复后从原 checkpoint 继续 |

- 添加和变更需要调用者对该 Scope 有 Source 写权限，并且 Scope 在插件的 `scopes` 范围内。
- `identify_target` 必须是纯函数：只做校验和规范化，不访问外部系统、不检查外部授权。外部资源是否存在、是否可读，
  在 collect 时由插件判断并以异常或空结果表达。它与写入一样在 Server 进程中同步执行，有超时限制。
- `pc_source_capabilities` 列出插件提供的 Source 类型和目标输入。

## 3. Artifact 插件契约

### 3.1 请求

```python
class ArtifactGenerator(Protocol):
    async def generate(self, request: GenerateRequest, writer: ArtifactWriter, /) -> None: ...

class GenerateRequest(BaseModel):
    scope_id: str
    reason: Literal["schedule", "sources"]
    sources: Sequence[GenerationSource]   # 本次窗口；未配置 inputs 时为空

class GenerationSource(BaseModel):
    ref: SourceRef                        # 精确 Source 引用
    position: int                         # Source 在 journal 中的位置，用于按采集顺序处理
    projection: JsonValue                 # inputs.projection 指定的 Projection 内容
```

### 3.2 写入器与写事务

```python
class ArtifactWriter(ArtifactReads):
    family: str                           # 固定为声明中的 family
    def stable_id(self, *parts: str) -> str: ...
    def transaction(self) -> AbstractAsyncContextManager[ArtifactTransaction]: ...

class ArtifactTransaction(ArtifactReads):
    ...                                   # 读取（见 3.4）加写操作（见下表）
```

`generate` 的执行分两部分：

- **事务外**：插件通过 `writer` 读取已提交的数据（每次读取使用独立的只读短事务），完成模型调用、向量计算等耗时工作。
- **写事务块**：`async with writer.transaction() as tx:`。
  - 块开始时，core 开启写事务并重新校验 Supervisor 租约和调用意图。
  - 块内的写操作直接在该事务中执行，立即返回写入后的 `ArtifactSnapshot`（含精确 revision）；块内读取能看到本块已写入的内容。
  - 块正常结束时，core 在同一事务中以 CAS 推进游标、确认本次调用并提交。
  - 块内抛出异常、超过 `transaction_timeout_seconds` 或提交冲突，事务整体回滚，本次调用失败。

规则：

- 每次调用最多开启一个写事务块；第二次开启抛出异常。游标和调用确认只随这一个事务提交，因此一个窗口的核心数据不会
  被部分提交。
- 插件没有开启写事务块就正常返回，表示本窗口无需产出，执行器单独提交游标推进和调用确认。
- 写事务块提交后，`generate` 中后续代码抛出的异常不再影响已提交的结果，执行器记录为提交后错误，不重试该窗口。
  插件应把写事务块作为 `generate` 的最后一步。
- 写事务块的时限计入整次调用的 `timeout_seconds`。块内只应做读取、写操作和插件自有存储的写入，不应调用模型。

### 3.3 写操作

写事务覆盖 Artifact 的全部通用写操作，每个操作复用 core 现有的实现与校验规则，违反规则时立即抛出异常：

| 操作 | 复用的 core 实现 | 语义 |
| --- | --- | --- |
| `put(artifact_id, *, content, source_refs=(), artifact_refs=(), expected_revision=None)` | 公共合并方法的修改路径 | 不存在则新建 revision 1；已存在则按当前 head 追加 revision。给出 `expected_revision` 时，当前 revision 不一致即冲突，用于事务外读取、事务内写入的乐观并发。内容与当前 revision 相同时为 no-op，返回当前快照 |
| `revert(artifact_id, *, to_revision)` | 公共恢复方法（选定 revision） | 以历史 revision 的内容追加新 revision，记录 `restored_from_revision`；目标已被遗忘或被合并冻结时一并恢复 |
| `set_lifecycle(artifact_id, state, *, replacement_artifact_id=None)` | 公共遗忘与恢复方法；指定替代者或退役时使用 `ArtifactGovernanceRepository.transition` | `deprecated` 为遗忘，`active` 为恢复，`retired` 不可逆。恢复走共享恢复流程：记录一个内容相同的新 revision，被合并冻结的输入会撤销冻结它的合并，按组恢复。遗忘和退役不追加 revision |
| `merge(inputs, *, into, content, source_refs=(), artifact_refs=())` | 公共合并方法（[RFC #1859](1859_artifact_merge.md)） | 以 `inputs` 的当前 revision 为输入，新建 `into`，记录合并关系并冻结输入。`into` 必须是新 ID；同一组输入在撤销后再次合并时，插件应把输入 revision 纳入 `stable_id`，避免与已退役的结果撞号 |
| `preview_undo_merge(artifact_id)` / `undo_merge(artifact_id)` | 公共合并方法的预览与恢复 | 撤销产生该 Artifact 的合并，恢复输入并退役结果，返回恢复与退役的精确引用 |
| `bind_handle(handle_key, ref)` | Artifact handle 绑定 | 把稳定 handle 绑定到指定 revision |
| `set_tags(artifact_id, tags)` | Artifact tags 替换 | 整体替换该 Artifact 的 tags |

写入约束：

- 只能操作本 Scope、声明中 `family` 的 Artifact，插件不能指定其他 family 或 Scope。
- `source_refs` 只能引用本次窗口中的 Source；`artifact_refs`、`to_revision` 和 `bind_handle` 的目标只能是本 family
  中实际存在的 revision。
- 检索文本由 core 按声明的 `search_fields` 从内容中提取，插件不单独提供；同一份内容无论由插件还是用户写入，
  检索文本都相同。
- 每个写事务块的操作数（1000）、单份内容大小（256 KiB）和总字节数（8 MiB）有上限，超出即抛出异常并回滚，
  不截断、不部分提交。
- core 不校验 `content` 的业务结构，也不保存或固化 schema。内容结构的演进由插件负责，插件可以在 `content` 中自带版本字段。

### 3.4 读取

`ArtifactWriter`（事务外，只读已提交数据）和 `ArtifactTransaction`（事务内，同时看到本块已写入的内容）提供相同的读取方法，
范围固定为本 Scope、本 family：

```python
class ArtifactSnapshot(BaseModel):
    ref: ArtifactRef                      # 精确 revision
    content: dict[str, JsonValue]
    lineage: ArtifactLineage              # 直接引用的 Source 与 Artifact
    lifecycle_state: LifecycleState       # active / deprecated / retired
    merged_into_id: str | None            # 被合并冻结时指向合并结果
    replacement_artifact_id: str | None
    governance_generation: int            # 治理状态版本，用于判断读取后是否被修改

class ArtifactReads(Protocol):
    async def get(self, artifact_id: str, *, revision: int | None = None) -> ArtifactSnapshot | None: ...
    async def revisions(self, artifact_id: str, *, cursor: str | None = None, limit: int = 50) -> ArtifactPage: ...
    async def list(self, *, states: Collection[LifecycleState] = ("active",), tags: Collection[str] = (),
                   cursor: str | None = None, limit: int = 50) -> ArtifactPage: ...
    async def search(self, query: str, *, limit: int = 10) -> Sequence[ArtifactSnapshot]: ...
    async def by_source(self, ref: SourceRef) -> Sequence[ArtifactSnapshot]: ...
    async def merge_inputs(self, artifact_id: str) -> Sequence[ArtifactRef]: ...
    async def tags(self, artifact_id: str) -> Sequence[str]: ...
    async def resolve_handle(self, handle_key: str) -> ArtifactRef | None: ...
```

- 快照同时携带精确 revision、lineage 和当前治理状态，插件不需要再分别读取 lineage 和生命周期。
- `get` 可以读当前内容或指定 revision；`revisions` 列出完整历史；`merge_inputs` 读取合并结果的精确输入。
- `list` 可以按生命周期状态和 tags 过滤，包括 deprecated、retired 和被合并冻结的 Artifact，用于查找之前已经存在的数据。
- `search` 是 core 提供的文本检索（见第 6 节），只覆盖 active 的当前 revision；在写事务中能检索到本块已写入、尚未提交的内容。
- `by_source` 反查 lineage 引用了某条 Source 的 Artifact，用于 Source 更新后找到需要修订的 Artifact。
- 读取返回的快照都带精确 revision，可以直接作为写操作的 `artifact_refs` 或 `to_revision`。

### 3.5 一致性与幂等

- **核心数据**：每个窗口的 Artifact 写操作、游标推进和调用确认在一个事务中提交。失败时核心数据完全不变，重试从干净状态
  开始；成功后游标已越过该窗口，同一窗口不会再次投递。冲突（例如 head 在读取后被用户修改）在操作时或提交时报告，整体
  回滚后由 Supervisor 重新调度，下一次调用读到修改后的状态。
- **重复处理同一业务对象**：不消费 Source 的定时整理等场景会再次处理已经处理过的对象。插件用 `writer.stable_id(...)` 从业务
  身份派生 Artifact ID；`put` 在内容相同时为 no-op，`set_lifecycle` 目标状态与当前相同时为 no-op，`merge` 在输入已全部
  合并到同一结果时为 no-op，从而不会产生重复 Artifact 或无意义的 revision。
- **插件自有存储**：插件的索引和其他存储与 core 是不同的数据库，不在 core 的事务中，core 不保证两者一致，由插件负责。
  推荐做法：
  1. 在写事务块内、拿到 `ArtifactSnapshot` 之后写自有存储，按 `artifact_id` upsert。这样自有存储的写入失败会让核心写入
     一起回滚。
  2. 写事务提交失败时，自有存储可能留下本次的条目。重试会处理同一窗口、得到同一个稳定 ID，覆盖这些条目。
  3. 把自有索引当作候选来源：命中的 ID 用 `get` 读取 core 中的当前快照，以快照内容和状态为准。这样回滚留下的条目和用户
     修改都不会让插件读到过期内容。插件也可以定期按 core 数据重建索引。
- `generate` 本身可能因失败重试而执行多次，模型调用和插件发出的外部请求不是恰好一次。

## 4. 用户对插件 Artifact 的修改

用户修改通过 core 的通用 Artifact 接口完成，由 Server 在短事务中同步执行，不调用插件，也不进入 Supervisor。

**编辑与 tags**

| 接口 | 规则 |
| --- | --- |
| `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 请求体为 `{"content": {...}}`，content 按原样保存，检索文本按声明的 `search_fields` 提取；`If-Match` 与当前 revision 不一致返回 412；Artifact 必须已存在。与内置 family 的编辑一样，提交的内容另存为一条内部 Content Source（`lineage_only`），作为新 revision 的直接来源记入 lineage；这类 Source 不进入任何插件的输入窗口 |
| `GET` / `PUT .../{artifact_id}/tags`、`POST /v1/scopes/{scope_id}/artifact-tags/query` | 与内置 family 相同；tags 不产生新的内容 revision |

**合并、遗忘与恢复**

这几项能力由共享的合并引擎提供（`ArtifactMergeService`，[RFC #1859](1859_artifact_merge.md)）。引擎本来就按 family 无关的方式
设计：每个 family 提供一个适配器，负责生成合并后的内容和维护自己的索引；版本校验、级联恢复、lineage 记录和权限检查都在
引擎中完成。运行时用一个注册表（`ArtifactMergeApplication`）按 family 名找到对应的引擎实例。内置 family 目前登记了
Experience 和 Atomic Memory；Server 加载插件时，为每个插件 family 创建一个引擎实例（插件适配器 + 共享的 Artifact 访问策略）
并登记进去。

在注册表之上提供一组不区分 family 的接口，对所有登记过的 family 生效；没有登记的 family 返回 422：

| 接口 | 规则 |
| --- | --- |
| `GET .../{artifact_id}/governance` | 返回当前 revision、生命周期状态、`state_version` 和合并目标 |
| `POST /v1/scopes/{scope_id}/artifacts/{family}/merges` | 输入为至少两个当前 Artifact 的 `artifact_id`、`revision`、`state_version`，以及结果的完整内容；新建结果 Artifact 并冻结所有输入。任何输入已变化即返回 409 |
| `POST .../{artifact_id}/forget` | 携带 `revision` 和 `state_version`；把 Artifact 标为 deprecated（无替代者），不追加 revision，退出检索 |
| `POST .../{artifact_id}/restorations` | `operation` 为 `restore`（默认，可指定 `revision` 恢复到旧内容）或 `undo_merge`；按组执行级联恢复和退役 |

- **权限**：由引擎检查，与内置 family 相同：修改已有 Artifact 需要该 Artifact 的写权限（`artifact.write`），新建合并结果
  需要 Scope 的贡献权限，结果归属于调用者。
  插件调用中的写入器使用同一个引擎类和适配器，只是权限不同：插件对本 family 的 Artifact 有完整写权限，包括用户合并
  产生的结果。
- **不提供预览**：Atomic Memory 的专用接口支持先预览恢复影响、再凭签名 token 执行。通用接口不提供预览，也就不需要签名
  密钥；恢复和撤销合并按执行时的当前状态进行，合并和遗忘依靠 `revision` 与 `state_version` 校验。
- **合并内容**：插件 family 的合并内容是不透明 JSON，core 只校验 JSON 结构并按 `search_fields` 提取检索文本；内置 family
  按各自的内容模型校验。
- **并发**：用户修改与插件调用可能同时发生。插件在事务外读取、事务内用 `expected_revision` 写入时会发现冲突并整体回滚，
  下一次调用基于修改后的状态重新决策；用户修改不会被插件静默覆盖。
- **不通知插件**：与内置 family 一样，用户修改只记录在 lineage 和治理状态中，不通知插件。插件在下一次调用时通过写入器
  读到新状态。
- **新建**：公开接口不为插件 family 直接新建 Artifact（合并结果除外）。调用方要补充知识，就写入 Source。

## 5. 插件执行器与消费游标

插件执行器是 Worker 中的 core 代码，实现 Supervisor 的 Worker 入口，替代旧的 `run_artifact_plugin_worker`。
它对 Artifact 插件的一次调用如下：

```text
Worker 收到 (plugin:<name>, scope_id, request_generation)
  -> 短事务：ScopeInvocation.start；读取 pc_source_cursors 中 (scope_id, plugin:<name>) 的游标
     -> 读取 journal 高水位，确定窗口 (after, through]，through ≤ after + max_sources
     -> list_window_for_generation 读取窗口，按 inputs.source_types 过滤，计算 inputs.projection
  -> 窗口为空且配置了 inputs：短事务完成游标 CAS 与 acknowledge，不加载插件
  -> 导入 plugin.py、核对声明、调用 create、创建写入器、调用 generate（受 timeout_seconds 约束）
     -> 事务外读取：独立只读短事务
     -> 写事务块开始：开启事务；guard（租约与调用意图）
        块内写操作在该事务中执行
        块结束：游标 CAS 到 through；acknowledge；提交
  -> generate 返回且未开启写事务块：短事务完成游标 CAS 与 acknowledge
```

- 窗口内类型不匹配的 Source 被过滤，但游标仍推进过整个窗口；匹配类型的 Source 缺少所需 Projection 时调用失败，不跳过。
- 游标只在执行器的提交事务中推进，插件得不到游标值，也没有写入接口。游标 CAS 保证在读取窗口与提交之间即使发生
  领导权变更或重复派发，同一窗口也只提交一次。
- 待处理工作的发现有两条路径：Server 进程中的 Source 写入会把关注该 Scope 的插件 binding 标记为待处理；启动时
  现有的 `SourceProcessingPendingProvider` 按 journal 高水位与游标的差值补齐其他进程写入的 Source。Supervisor 只在
  binding 的 interval 或 cron 到期时接纳待处理的 Scope，因此 `on_sources` 映射为约 2 秒的接纳间隔。判断基于持久化数据，
  Server 重启后不会丢失待处理输入。
- 未配置 `scopes` 时，自动调度对所有 journal 超过游标的 Scope 生效；配置了 `scopes` 时只接受名单内的 Scope。

collect 调用结构相同：输入换成该 Scope 的已启用目标及其 checkpoint，提交内容换成 Source 与各目标的新 checkpoint。
collect 不需要写事务块，由执行器根据返回值一次提交。添加采集目标通过现有的调用意图（`ArtifactProcessingIntentRepository.request`）
请求一次采集。

## 6. 公开读取与检索

插件 family 的公开读取完全由 core 处理，不执行插件代码：

- list / get / revisions / tags 复用现有的通用 Artifact 读取路径。插件 family 的 `content` 按原样 JSON 返回，不需要
  `pc_artifact_family_schemas` 中的 schema。被合并冻结、deprecated 和 retired 的 Artifact 按公共合并与生命周期规则展示。
- list 返回的条目只含元数据，`title` 和 `summary` 对插件 family 为空；内容需要通过 get 读取。
- search 是 core 为插件 family 提供的通用文本检索，与现有检索接口一样只接受文本 query：
  - 写入时，core 按 `search_fields` 从内容中取出文本，经现有词法分析写入 `pc_artifact_heads.searchable_text`，并随 revision
    保存，恢复旧 revision 时检索文本随内容一起恢复。
  - 检索时在该列上匹配，只返回请求 Scope 中调用者有权读取的 active 当前 revision。没有可检索文本的 Artifact 不参与检索。
  - 写入器的 `search` 使用同一实现。
  - SQLite 上与 Experience、Skill 共用 `pc_artifact_fts`：该辅助表的重建会回填所有带检索文本的 head，就绪校验只比对内置
    family 的行。实现 PR 需要给出 OceanBase 上的检索方式和规模验证。
- Review、Recall 和 Prepared Context 第一版不对插件 family 开放，对应接口返回明确的“不支持”错误；`GET /v1/capabilities`
  列出插件 family。

## 7. 加载与生命周期

```text
Server 启动
  -> 扫描 POWERCONTEXT_SERVER_PLUGINS_DIR 的直接子目录（未设置则不加载插件）
  -> 对每个目录：在有时限的检查子进程中导入 plugin.py，校验声明，不调用 create
     -> Source 插件：在 Server 进程导入 definitions、target_input 与 identify_target，注册 Source 类型
     -> 注册 family 的读取与检索；有调度或触发时注册 Supervisor binding
  -> 任一步失败：记录错误，该插件标记为 failed，继续下一个
Server 停止
  -> 按现有 Supervisor 关闭流程停止派发并回收 Worker
```

- 插件只有 `loaded` 和 `failed` 两种结果，只存在于进程内存中。Server 不持久化插件的启用状态、注册代次或配置快照；
  每次启动按当时的目录内容重新检查。
- 没有热加载，因此不需要注册代次来屏蔽旧 Worker，现有的 Supervisor 租约 fence 已经足以屏蔽领导权变更后的旧提交。
- Worker 每次调用重新导入插件代码并核对声明。插件目录在 Server 运行期间被修改、声明随之改变时，调用失败并提示重启；
  只改了实现代码时，新派发的 Worker 会使用新代码。部署者应在修改后重启 Server。
- 只读 API 角色的 Server 实例也会检查插件并注册 family 和 Source 类型，用于读取、检索和用户修改，但不派发 Worker。

## 8. 执行约束

- **隔离**：`collect` / `generate` 在 Supervisor 启动的 Worker 子进程中执行，崩溃、内存泄漏和阻塞不影响 Server。
  子进程隔离普通故障，不是不可信代码的沙箱：插件以 Worker 的操作系统权限运行，可以访问其文件、网络和环境变量。
- **超时与预算**：`timeout_seconds` 是 Worker 总时限，`transaction_timeout_seconds` 是写事务块时限，`max_workers` 是该插件
  跨 Scope 的并发上限，分别映射到 Supervisor binding 的现有参数或由执行器强制。
- **写事务的锁影响**：写事务块持有期间会锁住涉及的 head 和游标行；在 SQLite 上，写事务会阻塞整个数据库的其他写入。
  因此写事务块必须短，时限超出即回滚。
- **数据库访问**：Worker 打开数据库时不创建表，Schema 由 Server 准备。现有的数据库就绪检查会校验每个待处理调用意图的
  binding 都已持久注册；`plugin:` 前缀的 binding 由插件目录在启动时决定，就绪检查对其放行，插件移除后遗留的调用意图
  不再被处理。
- **失败与退避**：调用失败时不提交任何核心数据，该插件该 Scope 进入 Supervisor 现有的退避，不影响其他 Scope 和插件。
  core 不会因为重复失败自动跳过输入；持续失败需要修复插件或数据后重启。collect 中某个目标持续失败会阻塞同一 Scope
  的其他目标，可以先停用该目标。
- **多实例部署**：OceanBase 多 Server 实例时，派发只发生在持有 Supervisor 领导租约的实例上；Source 写入、目标管理、
  读取和用户修改可以在任意实例执行。插件自有存储需要能被执行 Worker 的实例访问。SQLite 部署只有一个 Server 进程。

## 9. 持久化

| 数据 | 处理 |
| --- | --- |
| `pc_sources`、Projection、Source journal、`pc_source_definition_manifests` | 保留；Source 插件的结果 |
| `pc_artifacts`、`pc_artifact_heads`、`pc_artifact_lineage_*`、合并记录、handle、tags | 保留；Artifact 插件的结果 |
| `pc_source_cursors` | 保留；Artifact 插件的消费游标，键为 `(scope_id, plugin:<name>)` |
| `pc_connector_checkpoints` | 保留；各采集目标的 checkpoint，键为 `(scope_id, 目标的 binding ID)` |
| `pc_source_collect_targets` | 新增；采集目标：Scope、插件名、目标键、规范化输入、启用状态、创建与更新时间、最近成功采集时间、最近失败分类 |
| Supervisor 的调用意图、pending、租约表 | 保留；插件 binding 与内置 binding 共用，也是 `GET /v1/plugins/{name}/scopes` 的数据来源 |
| `pc_artifact_processing_binding_states` 中为插件新增的列（`plugin_id`、`plugin_family`、`binding_generation`、`desired_state`、`phase`、`last_error`） | 移除 |
| `pc_artifact_family_schemas` | 移除 |
| 自定义 family 声明式索引使用的搜索状态与向量索引数据 | 移除；实现 PR 确认内置 family 不依赖后删除对应表 |
| 旧 Artifact 插件写入的 Artifact、head、lineage | 删除，不迁移 |
| `pc_source_write_plugins` | 移除；Source 定义改由插件目录在启动时提供 |
| Source 插件管理服务的 SQLite | 随管理服务一起移除 |
| 插件自有的索引与存储 | 不属于 core，由插件和部署者管理 |

目标的 binding ID 由插件名和目标键确定性派生，长度超过列宽时使用其摘要。新增与移除通过一次新的数据库迁移完成，
迁移说明中列出新增、删除的表、列和数据范围。

## 10. 接口变更

| 变更 | 内容 |
| --- | --- |
| 移除 | `/v1/admin/artifact-plugins` 及其子路径；Source 插件管理服务、插件实例 Runtime、poll / reply 通道；`source load` 命令；MCP `pc_source_instances`、`pc_source_instance_set_enabled`；`plugin.yml` 与 `powercontext.yml` 清单 |
| 移除 | 自定义 family 的声明式索引（`ArtifactSearchDeclaration`、`TextIndex` 等）、`ArtifactFamily` 的 schema 声明、`ArtifactDraft` 和旧的 Worker 插件执行器 |
| 新增 | `powercontext.plugins`：`SourcePlugin`、`ArtifactPlugin`、`Inputs`、`Interval`、`Cron`、`PluginContext`、写入器与读取接口 |
| 新增 | `GET /v1/plugins`、`GET /v1/plugins/{name}`、`GET /v1/plugins/{name}/scopes`（`server.admin`，只读） |
| 新增 | `GET .../{artifact_id}/governance`、`POST /v1/scopes/{scope_id}/artifacts/{family}/merges`、`POST .../{artifact_id}/forget`、`POST .../{artifact_id}/restorations`：基于共享合并引擎注册表的通用接口，对所有登记过的 family 生效（内置为 Experience 和 Atomic Memory，以及加载的插件 family）。Atomic Memory 的专用接口保持不变 |
| 调整 | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` 接受插件 family，请求体为 `{"content": {...}}` |
| 调整 | tags 接口接受插件 family：`TaggableArtifactFamily` 从枚举改为与 `ArtifactReadFamily` 相同格式的字符串，服务端校验为内置 family 或已加载的插件 family。网络上的数据格式不变，生成的客户端少了这个枚举类型 |
| 调整 | 通用 Artifact 读取与检索接口支持插件 family；`GET /v1/capabilities` 列出插件 family |
| 调整 | `pc_source_add` 的 write 用法使用插件定义，collect 用法添加采集目标；`pc_source_capabilities` 列出插件 Source 类型与目标输入；`pc_source_status` 报告目标及其最近采集结果；`pc_source_target_set_enabled` 作用于 core 保存的目标 |
| 调整 | 环境变量 `POWERCONTEXT_SERVER_PLUGINS_DIR` 取代 `POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR` 和 Source 插件目录配置 |

`openapi/powercontext.yaml` 按上表更新后重新生成 `src/powercontext/http/_generated/` 和各集成的操作表。

## 11. 可观测性

插件调用复用 Supervisor 与 Worker 现有的操作词汇、执行身份和结果字段，不为定时调用生成 request ID。新增字段：

| 字段 | 含义 |
| --- | --- |
| `plugin_name` | 插件名 |
| `plugin_method` | `collect` 或 `generate` |
| `trigger` | `schedule`、`sources`、`target_added` |
| `operation_count` | 本次提交的写操作数（Artifact）或 Source 条数（collect） |
| `target_count` | 本次 collect 携带的目标数 |
| `transaction_ms` | 写事务块从开始到提交或回滚的耗时，毫秒 |

指标标签可以使用 `plugin_name` 和 `plugin_method`，不包含 Scope ID 或目标键。日志经 `log_safely` 输出，不包含 Source
内容、Projection、Artifact 内容、目标输入和环境变量值；插件异常只记录类型和已知错误分类，不记录异常消息。新增字段按
开发指南登记到允许列表，并在中英文可观测性文档中说明。调用结果只进入日志和指标，不持久化，运维接口不提供“最近一次
调用结果”。

# 缺点

- **部分插件代码在 Server 进程中执行**：Source 写入需要同步返回，Source 定义的 `resolve`、Projection 和 `identify_target`
  不经过 Worker。它们应当是纯数据转换，但阻塞或异常仍会影响请求处理。Server 对这些调用设置超时并把异常转换为请求错误。
- **写事务持锁**：写事务块期间持有数据库锁，SQLite 上会阻塞其他写入。插件若在块内执行慢操作，会拖慢整个 Server 的写入，
  直到块超时回滚。
- **插件自有存储与核心数据不保证一致**：两者不在同一事务中，提交失败可能在自有存储中留下条目；用户修改也不通知插件。
  插件需要按稳定 ID 覆盖，并以 core 的当前数据为准。
- **每次调用都要启动 Worker 并加载插件**：子进程启动和工厂初始化有固定开销，不适合秒级高频调用；插件不能在调用之间
  复用连接或缓存。
- **不是安全沙箱**：Worker 隔离普通故障，不限制插件的文件、网络和凭据访问，也不提供硬性的内存或 CPU 配额。
- **依赖必须兼容**：所有插件与 Server 共用一个 Python 环境，依赖冲突需要部署者解决。
- **同一份代码部署多次需要额外处理**：family 写在声明中，同一份代码复制成两个目录会因 family 冲突而检查失败。需要多次
  部署时，插件应从环境变量读取 family 等标识。
- **内容结构不受 core 治理**：core 不校验 `content` 的 schema，历史 revision 的解释需要插件和应用自行保持兼容；
  用户通过 `PUT` 提交的内容也只由插件在后续读取时解释。
- **core 检索能力有限**：core 只提供基于声明字段的通用文本检索；向量等检索需要插件自建索引。
- **采集与 Server 部署在一起**：采集在 Server 的 Worker 中执行，需要 Server 能访问外部数据源。
- **代码变更需要重启**：没有热加载，插件更新期间 Server 会短暂不可用。

# 理由与备选

| 方案 | 取舍 |
| --- | --- |
| 保留现有设计，只简化接口 | 插件作者体验改善有限，生命周期状态机、管理服务和 schema 固化的维护成本仍在 |
| 清单文件（YAML）描述插件 | 插件需要同时维护清单和代码，两者的字段要保持一致；清单中的变量替换、配置模型和导出约定又是一套额外规则。声明写在代码里，由同一个 Pydantic 模型校验，插件作者只写 Python |
| 插件在 Server 进程内执行 | 部署和调用最简单，但插件故障会拖垮 Server；Supervisor 已经提供隔离和调度，没有理由另起一套 |
| 插件完全自管存储和游标 | core 读取接口无法直接使用，各插件需要重复实现窗口读取与幂等，游标与结果也难以原子提交 |
| 插件返回草稿，或由写入器在内存中暂存操作，最后统一提交 | 可以原子提交，但插件在提交前拿不到真实的 revision，难以同步写入自有索引；合并、撤销合并、生命周期等操作需要在内存中重新实现一遍 core 的语义；检索也看不到暂存内容 |
| 用一个事务包住整个 `generate` | 模型调用可能持续数分钟，长时间持锁会阻塞其他写入，SQLite 上会阻塞整个数据库；插件自有存储是另一个数据库，一个事务本来也覆盖不了 |
| `generate` 内的短写事务块（本提案） | 耗时工作在块外；块内直接复用 core 的写操作和校验，拿到真实 revision，读取能看到本块写入；块结束时与游标、确认一起提交，异常即回滚。代价是块内持锁，需要时限约束 |
| 用户命令经插件执行（手动请求排队，插件在下一次调用中处理） | 插件要实现命令解析，调用方只能异步等待；一个始终失败的命令会阻塞该 Scope 的后续生成；还需要持久化请求队列。遗忘、恢复、编辑都是 core 已有的通用操作，由 core 同步执行更简单 |
| 插件专用的触发与管理接口 | 调用方要区分插件与内置能力；现有的 Source 与 Artifact 接口已经覆盖调用方需要的操作 |
| 通用合并接口只对插件 family 开放 | 需要在接口里额外维护一份 family 白名单。能操作哪些 family 本来就由注册表决定，没有适配器的 family 无法登记；内置 family 的权限检查在引擎内，两个入口走同一套检查 |
| 常驻插件进程（类似 Dify 的插件守护进程） | 可以复用连接、支持多语言，但需要进程管理、序列化协议和取消协议。接口以请求 / 响应模型定义，后续可以替换执行方式而不改插件代码 |
| 支持热加载 | 需要重新引入加载状态、注册代次屏蔽和旧调用回收，与目标冲突 |

选择这一方案的原因：

- 插件作者只需写一个 `plugin.py`：一个声明、一个工厂、一两个方法。
- 调用方只用现有的 Source 与 Artifact 接口，不感知插件。
- 隔离、调度、触发、退避和领导权都复用已经在生产中使用的 Supervisor。
- 写操作直接复用 core 现有实现，插件写入和用户修改遵循同一套语义。
- Source 与 Artifact 留在 core 的统一存储中，现有读取接口、lineage、合并记录和访问控制直接适用。
- core 只管理两类核心数据及其进度，其余交给插件。

# 先例

- **Dify 插件**：按插件类型定义接口，插件实现对应方法，Dify 在工作流或应用需要时调用；插件运行在独立的插件守护进程中。
  本提案沿用“宿主定义接口与调用时机”的思路，执行隔离复用 PowerContext 已有的 Supervisor Worker。
- **Airbyte 连接器协议**：Source 连接器的 `read` 接收上次的 state 并输出记录和新 state，由平台持久化 state。
  本提案的采集 checkpoint 采用相同分工，并按目标分别保存。
- **pytest 插件与 setuptools entry points**：插件以 Python 对象声明自己提供的能力，宿主导入后读取，不需要单独的清单文件。
- **事务上下文管理器**：SQLAlchemy 的 `async with engine.begin()` 等接口在块开始时开启事务、正常结束时提交、异常时回滚。
  `writer.transaction()` 沿用这一使用方式，并在提交时附带游标与调用确认。
- **PowerContext 现有基础**：[Artifact processing Supervisor](1515_artifact_processing_supervisor.md) 的调度、调用意图、
  Worker 与租约 fence；[公共合并方法](1859_artifact_merge.md)；`ArtifactRepository`、`ArtifactGovernanceRepository`、
  handle 与 tags 的写入实现；通用 Artifact 读写接口；`SourceDefinition` / `SourceProjection`；Source journal、
  `pc_source_cursors` 和 `list_window_for_generation`；`pc_connector_checkpoints`；Source 管理接口与 `identify_target`
  目标识别规则。

# 未决问题

| 问题 | 说明 |
| --- | --- |
| 插件自定义检索 | 现有检索接口只接受文本 query，第一版由 core 提供通用文本检索。后续是否允许插件为公开检索接口提供实现，需要确定检索代码在哪个进程执行 |
| 写事务块的时限与限额默认值 | 默认 30 秒是否合适；SQLite 与 OceanBase 是否采用不同默认值；单块操作数与字节上限 |
| 共享模型客户端 | 是否在 `PluginContext` 中提供 Server 已配置的模型客户端，让 LLM 插件不必自带凭据，并计入现有模型用量统计 |
| 采集目标的删除 | 第一版只支持停用和恢复。是否需要彻底删除目标及其 checkpoint，以及删除后已采集 Source 的处理 |
| 插件 family 接入 Recall / Prepared Context | 需要定义候选内容和预算约束，建议另行提案 |
| 窗口大小的回退 | 内置 Atomic Memory 在模型超时时会缩小下一次的 Source 窗口。插件无法影响窗口大小，过大的窗口会持续超时。是否允许插件以约定的异常请求缩小该 Scope 的窗口 |
| 持续失败的窗口 | 始终失败的 Source 窗口会阻塞该 Scope 的后续调用。是否提供跳过或转入死信的运维操作，以及如何记录被跳过的输入 |
| 消费全部 Source 类型 | `inputs.source_types` 需要逐一列出类型；内置 Atomic Memory 消费所有可用于生成的 Source。是否支持 `"*"` 表示全部类型 |
| 内置专属能力 | Scope Prompt 管理、写入闸门与决策观测、模型用量统计目前只服务内置 family，插件无法接入。是否以及如何向插件开放 |
| Atomic Memory 专用接口的去留 | 通用合并接口上线后，Atomic Memory 的合并、遗忘、恢复同时有专用和通用两个入口，行为一致。专用接口是否保留，或改为通用接口的别名 |

# 后续可能

- 常驻插件进程，在保持接口不变的前提下复用连接和初始化结果。
- 更多插件类型，例如为 Recall 提供候选内容、为 Prepared Context 提供文本片段。
- 插件打包与安装工具，例如从 Git 地址或 wheel 安装到插件目录。

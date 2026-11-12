---
title: 插件接口
description: Artifact 插件实现少量接口，由 PC Server 在需要时调用：后台调用在 Supervisor Worker 中执行，同步调用在 core 管理的插件宿主进程中执行；调用方只使用现有的 Source 与 Artifact 接口，core 只管理两类核心数据及其消费进度，不托管插件生命周期。
---

- Proposal Name: `plugin_interfaces`
- Start Date: 2026-11-08
- RFC PR: [oceanbase/powercontext#2549](https://github.com/oceanbase/powercontext/pull/2549)

# 概要

本提案重新定义 PowerContext 的第三方插件模型：插件只实现几个固定接口，PC Server 在需要时调用；调用方不直接调用插件，
而是像使用内置能力一样使用现有的 Source 和 Artifact 接口。

- **Artifact 插件**：拥有一个 Artifact family，实现 `generate` 把尚未消费的 Source 加工成 Artifact，实现
  `render_artifact` 给出默认展示，可选实现检索和用户修改。插件可以自带 Source 类型，用来记录用户提交给它的内容。
- **插件形态**：一个插件是一个目录，包含 `plugin.py`，其中把一个声明对象 `ArtifactPlugin` 赋给 `plugin`。
  声明写在代码里，没有单独的清单文件；部署相关的设置由插件从环境变量读取。
- **调用方式**：
  - 写入插件自带类型的 Source，使用现有的 Source 写入接口。
  - 读取、检索 Artifact，使用现有的 Artifact 接口。
  - 新建、编辑、合并、遗忘、恢复插件 Artifact，也使用现有的 Artifact 接口。core 负责鉴权、版本校验和溯源，再同步调用
    插件对应的方法；插件校验内容、维护自己的存储，并通过写入器写入。插件没有实现某项操作时，该接口返回 422。
- **执行**：
  - `generate` 是后台调用，在 Supervisor 管理的 Worker 子进程中执行，沿用现有的隔离、超时、并发预算、领导租约和失败退避。
  - Source 写入时的 `resolve` 与 Projection、展示、检索和用户对插件 Artifact 的修改是同步调用，在 core 管理的常驻
    **插件宿主进程**中执行。每个 Server 进程为每个加载的插件启动一个宿主进程，部署者不需要管理它。
- **写入**：Artifact 插件在 `async with writer.transaction()` 块中执行写操作，块结束时 core 把写入、消费游标和
  完成确认一起提交，异常则整体回滚。插件读不到也改不了游标。
- **core 的职责**：只管理 `pc_sources`、`pc_artifacts`（含 heads、lineage、合并记录、生命周期、handle 和 tags）及其
  消费进度。插件可以有自己的索引和存储，由插件自己管理；core 在部署使用的数据库后端上为每个插件提供一块专属存储，
  并把 Server 已配置的模型提供给插件使用。
- **生命周期**：插件在 Server 启动时按目录加载，变更需要重启。每个 Server 进程只在内存中记录本进程已激活的插件，
  不持久化插件的启用状态、注册代次或管理阶段。

本提案取代 [Artifact 插件框架 RFC #2175](2175-artifact-plugin-system.md) 的插件契约与管理机制。旧框架随 v1.5 发布，
但目前没有对外发布的插件，因此本提案不保留兼容：旧的插件清单、管理接口和插件管理相关的表直接移除，旧插件写入的自定义
Artifact 数据不迁移。为了不悄悄丢失数据，升级前的迁移检查发现旧框架留有数据时拒绝升级，由部署者在旧版本上先导出和
清理（见参考级说明第 10 节）。

独立的 Source 插件，即只提供 Source 类型，或从外部系统持续采集（采集目标、checkpoint、在设备上采集并汇报到中心），
不在本提案范围内，由后续提案定义并取代 [Source 插件框架 RFC #2012](2012-source-plugin-system.md)。在那之前，旧 Source
插件框架保持不变。

本文描述目标设计，不代表主线或发布版本已经可用。接口名称和代码示例用于说明契约，最终语法以实现为准。

# 动机

现有的 Artifact 插件框架让 PowerContext 替第三方插件承担了大量生命周期管理：

| 框架 | Server 为插件承担的工作 |
| --- | --- |
| Artifact 插件（#2175） | 在 `pc_artifact_processing_binding_states` 中维护 `desired_state`、`phase`、`binding_generation`、`last_error`；七种管理状态及其转换；`enable-all / reload / enable / disable / unload` 管理接口与 `expected_generation` 并发校验；用注册代次屏蔽旧 Worker 提交；在 `pc_artifact_family_schemas` 中固化内容 schema 和索引定义，变更返回 409；框架负责自定义声明式索引的构建、重建和可用状态 |

这些机制的代价由所有人承担：

- **插件作者**需要理解 binding、generation、schema 固化规则和索引声明等框架概念，才能写出一个插件。
- **部署者**需要编写清单文件，并通过管理 API 让插件进入可运行状态。
- **调用方**需要区分内置能力和插件能力，使用不同的接口。
- **维护者**需要为第三方代码维护状态机、并发屏蔽、schema 和索引治理以及恢复路径，这部分代码的规模和测试面已经超过
  插件业务本身。

插件真正需要的是：在合适的时机被调用，拿到需要的输入，把结果交回 core。调度、隔离、消费进度和事务提交是 core
本来就有的能力，Supervisor 已经为内置 Artifact 提供了这些能力。Dify 等系统的插件模型也说明，宿主定义接口和调用时机、
插件只实现接口，足以支撑大量第三方扩展。

本提案的目标：

1. 插件作者只需写一个 `plugin.py`：一个声明对象和几个方法，不需要理解插件管理的内部机制。
2. 调用方不感知插件：Source 和 Artifact 的现有接口对插件产生的数据同样适用。
3. 插件代码只在 core 管理的子进程中执行（Worker 或插件宿主进程），插件故障不影响 Server 和其他插件。
4. 插件结果进入 `pc_sources` / `pc_artifacts`，与消费进度原子提交。
5. core 只管理这两类核心数据及其消费进度；内容结构、索引和其他存储交给插件自己。
6. 插件对核心数据的写入只经过 core 提供的写入器，覆盖 Artifact 的全部通用写操作，并沿用 core 现有的校验规则。
7. 用户对插件 Artifact 的修改经过插件，插件自有存储能与修改同步更新。
8. Artifact 生成可以按调度执行，也可以由新 Source 触发。
9. 用户只需部署一个 Server；插件随 Server 启动加载。

# 指南级说明

## 1. 总体设计

```mermaid
flowchart LR
    Caller["应用 / Agent / 宿主"]
    subgraph Server["PC Server（core）"]
        SourceAPI["现有 Source 接口<br/>写入 Source"]
        ArtifactAPI["现有 Artifact 接口<br/>读取、检索、修改"]
        Sup["Supervisor<br/>调度 / 触发 / 预算 / 退避"]
        Core[("core 数据<br/>pc_sources / pc_artifacts / 游标")]
    end
    subgraph Host["插件宿主进程（常驻，core 管理）"]
        Sync["resolve / Projection / render_artifact<br/>search_artifacts / create_artifact / ..."]
    end
    subgraph AW["Worker：Artifact 插件"]
        Generate["generate(request, writer)"]
        Tx["writer.transaction()"]
        Generate --> Tx
    end
    Caller --> SourceAPI
    Caller --> ArtifactAPI
    SourceAPI -->|"同步调用"| Sync
    ArtifactAPI -->|"读取"| Core
    ArtifactAPI -->|"展示、检索，同步调用"| Sync
    ArtifactAPI -->|"修改，同步调用"| Sync
    Sync -->|"Source 或 Artifact 写入，一次提交"| Core
    Sync -.-> Own
    Core -->|"新 Source 触发"| Sup
    Sup -->|"未消费的 Source 窗口"| Generate
    Tx -->|"Artifact 写入、游标、确认，一次提交"| Core
    Generate -.-> Own[("插件自有存储 / 模型")]
```

图中有两条路径，一次调用只执行一个插件：

- **生成路径**：新 Source 进入 journal 后，关注这些 Source 的 Artifact 插件被标记为待处理；Supervisor 派发 Worker，传入
  该 Scope 尚未消费的 Source 窗口和写入器；插件在写事务块中写入 Artifact，core 连同游标和确认一起提交。
- **同步路径**：调用方写入插件类型的 Source、展示或检索插件 Artifact、修改插件 Artifact 时，Server 完成鉴权和版本校验后，把请求交给该插件的
  宿主进程；插件在宿主进程中完成转换或写入，结果在一个事务中提交后同步返回给调用方。

调用方只和 core 的现有接口打交道：

| 调用方要做的事 | 使用的接口 | 插件参与的方式 |
| --- | --- | --- |
| 提交一条插件类型的 Source | 现有 Source 写入接口 | 宿主进程执行插件定义的 `resolve` 和 Projection |
| 浏览、读取 Artifact | 现有 Artifact 读取接口 | 不参与 |
| 展示、检索插件 Artifact | 展示接口、现有检索接口、Prepared Context | 宿主进程执行 `render_artifact`、`search_artifacts` |
| 新建、编辑、合并、遗忘、恢复、撤销合并 | 现有 Artifact 新建与编辑接口、通用合并接口 | 宿主进程执行插件对应的方法；未实现返回 422 |
| 打 tag、按 tag 查询 | 现有 tags 接口 | 不参与；tags 是 core 数据 |

职责划分：

| 职责 | 归属 |
| --- | --- |
| 认证、Scope 授权、公开 HTTP / MCP 接口、用户修改的版本校验与溯源记录 | core |
| 何时调用：调度、触发合并、Worker 预算、超时、退避、领导租约 | core（现有 Supervisor） |
| 宿主进程的启动、复用、超时终止与重启 | core |
| 读取输入、提供受约束的写事务、在同一事务中提交核心数据 / 游标 / 完成确认 | core（Worker 或宿主进程中的插件执行器） |
| 插件专属存储空间的分配、Server 已配置模型的封装 | core |
| 插件自带 Source 类型的定义与 Projection 计算；Artifact 内容结构、生成算法、展示、检索、用户提交内容的校验、插件自有的索引与存储及其一致性 | 插件 |
| 插件代码、依赖和环境变量的部署 | 部署者 |

插件代码只做一件事：给定输入，产出结果或抛出异常。抛出异常即失败，core 不提交任何核心数据；后台调用下次重试同一输入，
同步调用把错误返回给调用方。

## 2. 插件的形态

Server 从 `POWERCONTEXT_SERVER_PLUGINS_DIR` 加载插件。每个直接子目录是一个插件，目录名就是插件名；目录中必须有
`plugin.py`，可以有其他模块：

```text
/opt/powercontext/plugins/
├── knowledge-base/
│   ├── plugin.py
│   └── parsers.py
└── incident-report/
    └── plugin.py
```

`plugin.py` 把一个声明对象赋给模块级变量 `plugin`：

```python
plugin = ArtifactPlugin(family="incident-report", ..., setup=setup)
```

声明对象回答 core 在运行插件代码之前需要知道的问题：插件提供什么（Artifact family 和自带的 Source 类型）、消费什么、何时调用、
资源上限多少。它是普通的 Python 对象，由 Pydantic 校验；没有 YAML 或其他清单文件。

- **部署设置**：插件自己的凭据、外部服务地址等随部署变化的值，由插件在 `setup` 中从环境变量读取。Server 不保存、
  不传递也不记录这些值。
- **core 提供的资源**：插件需要数据库和模型时，不必另行配置，通过上下文使用 core 封装好的对象（见参考级说明 1.2）：
  - `ctx.storage`：部署所用数据库后端上的一块插件专属空间。SQLite 是数据目录下的一个单独文件，OceanBase 和 seekdb
    是一个单独的库。插件拿到的连接只指向这块空间，不指向 core 的表。
  - `ctx.models`：Server 已配置的生成模型和 embedding 模型。插件只能调用，拿不到模型的地址和凭据；调用计入现有的模型
    用量统计。
- **启用与停用**：目录存在且检查通过，插件就可用；需要停用时移出目录并重启 Server。
- **检查**：Server 启动时在有时限的子进程中导入 `plugin.py`，校验声明，不调用 `setup`。检查失败只影响该插件。

## 3. 插件自带的 Source 类型

Artifact 插件可以在声明的 `definitions` 中提供自己的 Source 类型（见 4.2）。插件用它记录"用户要求做什么"，例如上传了
一份文档、改写了一个分块，再在 `generate` 中把这些 Source 加工成 Artifact。这样每次变化都有对应的 Source，来源可以追溯，
而不需要另写一个 Source 插件。

```python
plugin = ArtifactPlugin(
    family="knowledge-base",
    definitions=(DocumentUploadDefinition(),),       # Source 类型 kb-document
    inputs=Inputs(source_types=("kb-document",), projection="knowledge-base.upload@1"),
    on_sources=True,
    setup=setup,
)
```

- **定义**：沿用现有的 `SourceDefinition`：`input_class` 校验输入，`resolve` 把输入解析为 Source，Projection 给出消费方
  读取的形态。
- **写入**：调用方通过现有的 Source 写入接口提交（`POST /v1/scopes/{scope_id}/sources`，`source_type` 为插件定义的类型），
  插件自己的代码通过 `ctx.sources.submit` 提交。校验、`resolve` 和 Projection 在插件的宿主进程中执行，保存语义与内置
  Source 一致。
- **只能追加**：与所有内置 Source 一样，没有修改和删除；要更正内容，提交一条新的 Source。
- **大块内容**：`resolve` 必须是纯函数，现有 Source 写入接口也只接受 JSON。文件、图片由插件自定义接口接收
  （见 [RFC #2579](https://github.com/oceanbase/powercontext/pull/2579)），插件存进自己的存储，Source 中只记录位置、摘要
  和媒体类型等元数据。
- **消费**：插件在 `inputs.source_types` 中列出这些类型即可消费；其他 Artifact 插件也可以消费它们。

## 4. Artifact 插件

### 4.1 要实现的接口

| 接口 | 必需 | 说明 |
| --- | --- | --- |
| `setup(ctx) -> ArtifactGenerator` | 是 | 插件对象的工厂（不是新建 Artifact）；Worker 中每次调用执行一次，宿主进程中只执行一次后复用 |
| `generate(request, writer) -> None` | 配置了 `schedule` 或 `on_sources` 时是 | 读取 `request.sources`（未消费的 Source 窗口），在写事务块中写入 Artifact |
| `render_artifact(request) -> Rendering` | 是 | 给出一个精确 revision 的默认展示内容（Markdown 或纯文本），见参考级说明第 7 节 |
| `search_artifacts(request) -> SearchResult` | 否 | 用插件自己的索引检索，返回命中的 revision 和片段；通用检索接口和 Prepared Context 调用它 |
| `create_artifact(request, writer) -> None` | 否 | 用户新建；`request.artifact_id` 由 core 分配 |
| `update_artifact(request, writer) -> None` | 否 | 用户编辑指定 ID 的 Artifact，`request.expected_revision` 为调用方看到的 revision |
| `merge_artifacts(request, writer) -> None` | 否 | 用户合并；结果的 ID 由 core 分配 |
| `forget_artifact(request, writer) -> None` | 否 | 用户遗忘 |
| `restore_artifact(request, writer) -> None` | 否 | 用户恢复、恢复到旧 revision 或撤销合并 |

后五个方法处理用户修改（见 4.4），每个都是可选的：没有实现的操作，对应接口返回 422，表示这个 family 不支持该操作。
插件在这些方法中校验用户提交的内容、更新自己的存储，并通过写入器完成 core 要求的那一项写操作。

core 不为插件 family 建立任何索引，也不理解 `content` 的结构：插件的内容怎样分块、是否可以全文或向量检索、怎样展示，
只有插件知道。因此展示和检索都交给插件：`render_artifact` 必须实现，`search_artifacts` 可选，未实现时通用检索接口和
Prepared Context 对该 family 返回 422。

### 4.2 声明

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `family` | 必填 | 插件拥有的 Artifact family，不能与内置 family 或其他插件重复 |
| `inputs` | `None` | `Inputs(source_types=..., projection=..., projections={...}, max_sources=50)`：消费的 Source 类型、每条 Source 交付的 Projection、单个窗口的条数上限；为空表示不消费 Source。`projection` 适用于所有类型，`projections` 按 Source 类型指定不同的 Projection，每个类型必须有其一 |
| `on_sources` | `False` | 新 Source 到达后触发；需要 `inputs` |
| `schedule` | `None` | `Interval(seconds=...)` 或 `Cron(expression=..., timezone=...)`。`schedule` 和 `on_sources` 都不配置时插件没有后台生成，不需要实现 `generate`，Artifact 只由用户修改和插件接口写入（例如笔记） |
| `scopes` | `None` | 可服务的 Scope 名单；配置了 `schedule` 但不消费 Source 的插件必须填写 |
| `timeout_seconds` | 300 | 整次调用的时限 |
| `transaction_timeout_seconds` | 30 | 写事务块的时限，不超过 `timeout_seconds` |
| `max_workers` | 1 | 跨 Scope 的并发上限 |
| `change_timeout_seconds` | 30 | 一次用户修改调用的时限，包括其中的写事务块 |
| `definitions` | `()` | 插件自带的 Source 类型，随插件加载登记，见第 3 节和参考级说明第 2 节 |
| `setup` | 必填 | 见上表 |

### 4.3 示例

下面的插件为每条工单 Source 生成一份报告：先用自己的向量索引查找相似的已有报告，命中则修订那份报告，否则新建；
同时把报告写进自己的向量索引。它还支持用户编辑报告，没有实现的新建、合并、遗忘、恢复对该 family 返回 422。向量索引
放在 core 分配的专属存储中，模型使用 Server 已配置的模型。

```python
from powercontext.plugins import (
    ArtifactPlugin, ArtifactWriter, GenerateRequest, Inputs, InvalidContent, PluginContext, Rendering, RenderRequest,
    SearchHit, SearchRequest, SearchResult, UpdateRequest,
)
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

    async def update_artifact(self, request: UpdateRequest, writer: ArtifactWriter) -> None:
        # 用户编辑：校验内容，重算向量，再写入 core 和自有索引
        report = Report.model_validate(request.content)          # 不合格时抛出 InvalidContent，接口返回 422
        vector = await self.model.embed(report.summary)
        async with writer.transaction() as tx:
            written = await tx.put(request.artifact_id, content=report.model_dump(),
                                   expected_revision=request.expected_revision)
            await self.index.upsert(request.scope_id, written.ref.artifact_id, written.ref.revision, vector)

    async def render_artifact(self, request: RenderRequest) -> Rendering:
        report = Report.model_validate(request.artifact.content)
        return Rendering(text=f"# {report.title}\n\n{report.summary}")

    async def search_artifacts(self, request: SearchRequest) -> SearchResult:
        # 通用检索请求原样转交；本插件只支持向量检索
        if request.search.mode not in (None, "vector"):
            raise InvalidContent("only vector search is supported", field="mode")
        vector = await self.model.embed(request.search.query)
        hits = await self.index.nearest_many(request.scope_id, vector, limit=request.search.limit)
        return SearchResult(hits=[SearchHit(ref=hit.ref, text=hit.summary, score=hit.score) for hit in hits])


async def setup(ctx: PluginContext) -> IncidentReports:
    index = await ctx.enter_async_context(VectorIndex.connect(ctx.storage.engine()))
    return IncidentReports(index, Summarizer(ctx.models))


plugin = ArtifactPlugin(
    family="incident-report",
    inputs=Inputs(source_types=("example-ticket",), projection="powercontext.text-evidence@1"),
    on_sources=True,
    setup=setup,
)
```

块内抛出异常时，core 回滚块内全部 Artifact 写入，游标不动，下次重试同一窗口。插件自有索引不在 core 的事务中：
插件按稳定 ID upsert，重试时会覆盖上一次留下的条目；读取自有索引时以 core 的当前数据为准（见参考级说明 3.5）。

### 4.4 调用方怎么使用

调用方使用现有的 Artifact 接口访问插件 family。读取接口直接读 `pc_artifacts`，不运行插件代码；展示、检索和修改接口
由 core 鉴权后，同步调用插件对应的方法：

| 用途 | 接口 | 插件方法 |
| --- | --- | --- |
| 浏览 | `GET /v1/scopes/{scope_id}/artifacts/{family}` | 无 |
| 读取当前内容、指定 revision、revision 历史、tags | `GET .../{artifact_id}`、`GET .../{artifact_id}/revisions/{revision}`、`GET .../{artifact_id}/revisions`、`GET .../{artifact_id}/tags` | 无 |
| 默认展示 | `GET .../{artifact_id}/rendering`（可带 `revision`） | `render_artifact` |
| 检索（请求原样转交，检索方式由插件解释） | `POST /v1/scopes/{scope_id}/artifacts/{family}/search` | `search_artifacts` |
| 为 Agent 准备上下文 | `POST /v1/context/prepare` 的 `assembly.sections` 中列出插件 family | `search_artifacts` |
| 新建（只提交内容，ID 由 core 分配） | `POST /v1/scopes/{scope_id}/artifacts`，请求体为 `{"family": ..., "content": {...}}` | `create_artifact` |
| 编辑（提交完整内容，`If-Match` 校验当前 revision） | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | `update_artifact` |
| 读取治理状态（遗忘、合并前取得 `state_version`） | `GET .../{artifact_id}/governance` | 无 |
| 合并 | `POST /v1/scopes/{scope_id}/artifacts/{family}/merges` | `merge_artifacts` |
| 遗忘 | `POST .../{artifact_id}/forget` | `forget_artifact` |
| 恢复、恢复到旧 revision、撤销合并 | `POST .../{artifact_id}/restorations` | `restore_artifact` |
| 修改 tags | `PUT .../{artifact_id}/tags`；按 tag 查询用 `POST /v1/scopes/{scope_id}/artifact-tags/query` | 无，由 core 处理 |

修改同步返回结果。插件拒绝内容或请求时返回 422，插件没有实现对应方法时也返回 422，宿主进程超时或不可用时返回 503。

### 4.5 方法一览

| 方法 | 适用 | 必须实现 | 调用时机 | 执行位置 |
| --- | --- | --- | --- | --- |
| `setup(ctx)` | 所有插件 | 是 | 宿主进程启动时一次；Worker 每次调用一次 | 宿主进程、Worker |
| Source 定义：`input_class`、`resolve`、Projection 的 `project`、`read` | 声明了 `definitions` 的插件 | 是 | Source 写入（通用写入接口、`ctx.sources.submit`）；把 Source 交付给 `generate` 时计算 Projection | 宿主进程、Worker |
| `generate` | Artifact 插件 | 配置了 `schedule` 或 `on_sources` 时是 | 定时，或新 Source 到达 | Worker |
| `render_artifact` | Artifact 插件 | 是 | 默认展示接口 | 宿主进程 |
| `search_artifacts` | Artifact 插件 | 否 | 通用检索接口；Prepared Context 中列出该 family | 宿主进程 |
| `create_artifact`、`update_artifact`、`merge_artifacts`、`forget_artifact`、`restore_artifact` | Artifact 插件 | 否 | 对应的通用修改接口 | 宿主进程 |

列出、读取当前内容或指定 revision、revision 历史、治理状态和 tags 不调用插件方法，由 core 直接从它保存的数据提供。
必须实现的方法在宿主进程执行 `setup` 之后核对，缺少时插件加载失败，状态为 `failed`。

## 5. 调度与触发

插件的后台调用（`generate`）的调度和触发都交给现有的 Artifact processing Supervisor；同步调用由请求触发，
不经过 Supervisor。每个插件在启动时注册为一个 Supervisor binding，
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

同一插件、同一 Scope 同时最多运行一个 Worker。运行期间到达的触发由 Supervisor 合并成结束后的一次调用。

## 6. 运维

- **启动**：Server 逐个检查插件目录。某个插件检查失败（缺少 `plugin.py`、导入失败、声明无效、family 或 Source 类型
  与内置或其他插件冲突）只影响该插件，Server 和其他插件正常启动。
- **查看**（都需要 `server.admin`，只读，数据都来自启动检查或数据库，任何 Server 角色都给出相同结果）：
  - `GET /v1/plugins`、`GET /v1/plugins/{name}`：每个插件的检查结果、类型、family 或 Source 类型、消费的 Source 类型、
    触发方式和 Scope 范围。
  - `GET /v1/plugins/{name}/scopes`：每个 Scope 是否有待处理的工作、已消费到的 journal 位置和当前 journal 位置。
  - 调用失败的原因通过日志和指标排查，不在接口中保存。
- **变更**：修改代码后重启 Server。Server 不监视文件，也不在运行中重新加载插件。
- **停用**：从插件目录移走后重启。已保存的 Source、Artifact 和游标都保留，但该插件的 family 和
  Source 类型不再注册：通用接口读取、检索或修改该 family 时返回 404，与不存在的 family 相同。插件 Artifact 的内容通常只
  记录摘要或位置，正文、分块和文件在插件自己的存储中，没有插件就无法解释，因此不提供脱离插件的读取。插件 Source 类型的
  新写入返回 422。重新部署同名插件时，读取恢复，生成从原游标继续。插件的专属存储
  不随之删除，由部署者保留或清理。
- **宿主进程**：由 core 按需启动、复用和重启，部署者不需要配置或管理。宿主进程的启动、终止和重启只体现在日志和指标中。

# 参考级说明

## 1. 公共契约

公开接口位于新模块 `powercontext.plugins`，替换现有的 `powercontext.artifact_plugins`。接口以 `typing.Protocol` 和 Pydantic 模型表达。

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
    setup: Callable[[PluginContext], Awaitable[ArtifactGenerator]]
```

- 插件名取自目录名，必须匹配 `^[a-z][a-z0-9-]{0,63}$`。
- 声明在构造时校验，包括调度组合、`inputs` 与 `scopes` 的依赖、时限关系和 family 命名。`setup` 必须是 async 函数。
- 声明中除 `setup`、`definitions` 等代码对象外的部分可以序列化，Server 在检查子进程中得到它，
  用于注册 binding 和回答运维查询。
- 检查时，core 记录插件目录的**代码指纹**（按文件内容计算）。Worker 和宿主进程导入插件前核对，不一致则调用失败并提示
  重启 Server，同一个 Server 进程中的调用不会混用新旧代码。

### 1.2 上下文

```python
class PluginContext:
    name: str                          # 插件名
    scope_id: str | None               # Worker 调用的 Scope；宿主进程中为 None，Scope 随每个请求传入
    plugin_dir: Path                   # 插件目录（只读约定）
    logger: logging.Logger             # 已绑定插件名的 logger
    storage: PluginStorage             # 插件专属存储
    models: PluginModels | None        # Server 已配置的模型；未配置时为 None

    async def enter_async_context(self, cm: AbstractAsyncContextManager[T]) -> T: ...

class PluginStorage(Protocol):
    backend: Literal["sqlite", "oceanbase", "seekdb"]
    def engine(self) -> AsyncEngine: ...   # 只指向本插件的专属空间

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class PluginModels(Protocol):
    embedding_available: bool                    # Server 是否配置了 embedding 模型
    embedding_dimension: int | None              # 向量维度；未配置时为 None
    generation_available: bool                   # Server 是否配置了生成模型
    async def embed(self, texts: Sequence[str], /) -> Sequence[Sequence[float]]: ...
    async def generate(self, messages: Sequence[ChatMessage], /, *, max_tokens: int | None = None) -> str: ...
    # 调用未配置的模型时抛出 ModelUnavailable；同步调用中返回 503
```

- 工厂在每次 Worker 调用中执行一次，实例不跨调用复用；在宿主进程中只执行一次，实例服务所有 Scope 的请求，在宿主进程
  存续期间复用，资源占用不随 Scope 数量增长。宿主进程中的每个请求都带有 `scope_id`。需要关闭的资源（包括插件自有存储的
  连接）通过 `ctx.enter_async_context` 注册，Worker 调用结束或宿主进程停止时由执行器释放。
- **专属存储**：core 使用 Server 现有的数据库连接配置，在同一个后端上为插件分配一块空间，不需要额外的数据库账号：
  - SQLite：数据目录下的 `plugins/<插件名>.sqlite3`。
  - OceanBase / seekdb：名为 `powercontext_plugin_<插件名>` 的库（`-` 换成 `_`），首次使用时由 core 创建。Server 的数据库
    登录没有建库权限时，由部署者事先建好。
  - 表结构和迁移由插件自己管理，core 不读取、不迁移、不备份这块空间。它不在 core 的事务中，与核心数据的一致性按 3.5 处理。
  - 这块空间属于整个插件，不按 Scope 隔离。插件按 `scope_id` 划分自己的数据，并在读取时自行确认数据属于本次请求的 Scope，
    不能只凭调用方给出的 ID 返回数据。
  - 插件与 Server 使用同一个数据库登录，这一划分防止插件误写 core 的表，不防止恶意插件；插件目录本身被当作可信代码。
- **模型**：`models` 封装 Server 的推理配置，插件拿不到模型的地址、请求头和凭据。Server 两种模型都没有配置时 `models`
  为 None；只配置了其中一种时，插件用 `embedding_available`、`generation_available` 判断。插件应把 `embedding_dimension`
  等 embedding 配置记在自己的索引上，配置变化后重建，避免把不同模型的向量混在一起比较。调用沿用 Server 的超时设置，并计入现有的模型用量统计。插件也可以从环境变量读取自己的
  模型配置，自行创建客户端。
- 插件得不到 core 数据库的连接，也没有游标的写入接口。插件对核心数据的写入只有两条路径：宿主进程中 `resolve` 的
  返回值，和 `ArtifactWriter`。

## 2. 插件 Source 类型契约

- **定义注册**：`definitions` 中的 Source 类型与 Projection 在加载时登记到现有的 Source 定义清单
  （`pc_source_definition_manifests`）。与内置类型、旧 Source 插件框架登记的类型或其他插件冲突时，检查失败。
- **写入**：core 按 `definition.input_class` 校验输入，调用 `resolve` 得到 Source，计算各 Projection 后保存，语义与内置
  Source 写入一致。校验、`resolve` 和 Projection 在该插件的宿主进程中同步执行，使用 Server 的同步调用时限。`resolve`
  必须是纯函数，不能有复制文件等副作用。
- **Source 身份**：由 `resolve` 决定。重复提交同一内容得到相同身份时，沿用现有去重规则，不产生重复 Source；身份相同但
  内容不同时返回冲突。
- **权限**：写入需要调用者对该 Scope 有 Source 写权限，并且 Scope 在插件的 `scopes` 范围内。
- **发现**：`GET /v1/capabilities` 与 `pc_source_capabilities` 列出已加载插件的 Source 类型。

## 3. Artifact 插件契约

### 3.1 请求

```python
class ArtifactGenerator(Protocol):
    # 配置了 schedule 或 on_sources 时必须实现
    async def generate(self, request: GenerateRequest, writer: ArtifactWriter, /) -> None: ...

class GenerateRequest(BaseModel):
    scope_id: str
    reason: Literal["schedule", "sources"]
    sources: Sequence[GenerationSource]   # 本次窗口；未配置 inputs 时为空

class GenerationSource(BaseModel):
    ref: SourceRef                        # 精确 Source 引用
    position: int                         # Source 在 journal 中的位置，用于按写入顺序处理
    projection: JsonValue                 # 按该 Source 类型声明的 Projection 内容
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

| 操作 | 语义 |
| --- | --- |
| `put(artifact_id, *, content, source_refs=(), artifact_refs=(), expected_revision=None)` | 不存在则新建 revision 1；已存在则按当前 head 追加 revision。给出 `expected_revision` 时，当前 revision 不一致即冲突；不给出时，使用本次调用在事务外读到的 revision（见 3.5），没有读过则不校验。内容与当前 revision 相同时不写入，返回当前快照，本次传入的 `source_refs` 和 `artifact_refs` 不会被记录（见下方写入约束）。用户修改中的 `put` 例外，见 4.1 |
| `revert(artifact_id, *, to_revision)` | 以历史 revision 的内容追加新 revision，记录 `restored_from_revision`；目标已被遗忘或被合并冻结时一并恢复。返回 `ArtifactChange`：目标的新快照，以及一并恢复和退役的其他 Artifact 的精确引用 |
| `set_lifecycle(artifact_id, state, *, replacement_artifact_id=None)` | `deprecated` 为遗忘，`active` 为恢复，`retired` 不可逆。恢复走共享恢复流程：记录一个内容相同的新 revision，被合并冻结的输入会撤销冻结它的合并，按组恢复。遗忘和退役不追加 revision。返回值同 `revert`，插件据此同步更新自有索引中受级联影响的条目 |
| `merge(inputs, *, into, content, source_refs=(), artifact_refs=())` | 沿用公共合并方法（[RFC #1859](1859_artifact_merge.md)）：以 `inputs` 的当前 revision 为输入，新建 `into`，记录合并关系并冻结输入。`into` 必须是新 ID；同一组输入在撤销后再次合并时，插件应把输入 revision 纳入 `stable_id`，避免与已退役的结果撞号 |
| `preview_undo_merge(artifact_id)` / `undo_merge(artifact_id)` | 撤销产生该 Artifact 的合并，恢复输入并退役结果，返回恢复与退役的精确引用 |
| `bind_handle(handle_key, ref)` | 把稳定 handle 绑定到指定 revision |
| `set_tags(artifact_id, tags)` | 整体替换该 Artifact 的 tags |

写入约束：

- 只能写本 Scope、声明中 `family` 的 Artifact，插件不能写其他 family 或 Scope。
- `source_refs` 在 `generate` 中只能引用本次窗口中的 Source，在用户修改中只能引用目标 Artifact 当前 lineage 中已有的
  Source（用于保留原始输入）。`artifact_refs` 可以引用本 Scope 中任意 family 实际存在的 revision；`to_revision` 和
  `bind_handle` 的目标只能是本 family 中实际存在的 revision。
- 内容不变时不产生 revision，与内置 Atomic Memory 的处理一致：模型判断已有记忆已经表达了新 Source 的内容时，该记忆
  不追加 revision，新 Source 也不进入它的 lineage。插件需要把新 Source 记为某个 Artifact 的证据时，应同时修改其内容。
- core 不为插件 family 提取检索文本或建立索引，检索由插件用自己的索引完成（见第 7 节）。
- core 在每个 revision 的元数据中记录写入它的插件名和插件版本摘要，便于在多个 Server 加载了不同版本时追溯每个 revision
  由哪个版本写入（见第 10 节）。
- 每个写事务块的操作数、单份内容大小和总字节数有上限，超出即抛出异常并回滚，不截断、不部分提交。
- core 不校验 `content` 的业务结构，也不保存或固化 schema。内容结构的演进由插件负责，插件可以在 `content` 中自带版本字段。

### 3.4 读取

`ArtifactWriter`（事务外，只读已提交数据）和 `ArtifactTransaction`（事务内，同时看到本块已写入的内容）提供相同的读取方法，
范围默认为本 Scope、本 family。`reads(family)` 返回同一 Scope 中其他 family 的只读视图，方法相同，可以读取内置 family
和其他插件的 family（内容按原样 JSON 返回），不能写入。这让一个插件可以基于另一个插件的结果工作，例如知识库插件读取
文档插件写入的 Artifact：

```python
class ArtifactSnapshot(BaseModel):
    ref: ArtifactRef                      # 精确 revision
    content: dict[str, JsonValue]
    lineage: ArtifactLineage              # 直接引用的 Source 与 Artifact
    lifecycle_state: LifecycleState       # active / deprecated / retired
    merged_into_id: str | None            # 被合并冻结时指向合并结果
    replacement_artifact_id: str | None
    governance_generation: int            # 治理状态版本，用于判断读取后是否被修改

class ArtifactChange(BaseModel):          # revert / set_lifecycle 的返回值
    artifact: ArtifactSnapshot            # 目标的新快照
    restored: Sequence[ArtifactRef]       # 一并恢复的其他 Artifact
    retired: Sequence[ArtifactRef]        # 一并退役的其他 Artifact

class ArtifactReads(Protocol):
    async def get(self, artifact_id: str, *, revision: int | None = None) -> ArtifactSnapshot | None: ...
    async def revisions(self, artifact_id: str, *, cursor: str | None = None, limit: int = 50) -> ArtifactPage: ...
    async def list(self, *, states: Collection[LifecycleState] = ("active",), tags: Collection[str] = (),
                   cursor: str | None = None, limit: int = 50) -> ArtifactPage: ...
    async def by_source(self, ref: SourceRef) -> Sequence[ArtifactSnapshot]: ...
    async def merge_inputs(self, artifact_id: str) -> Sequence[ArtifactRef]: ...
    async def tags(self, artifact_id: str) -> Sequence[str]: ...
    async def resolve_handle(self, handle_key: str) -> ArtifactRef | None: ...
    def reads(self, family: str) -> ArtifactReads: ...   # 同一 Scope 中其他 family 的只读视图
```

- 快照同时携带精确 revision、lineage 和当前治理状态，插件不需要再分别读取 lineage 和生命周期。
- `get` 可以读当前内容或指定 revision；`revisions` 列出完整历史；`merge_inputs` 读取合并结果的精确输入。
- `list` 可以按生命周期状态和 tags 过滤，包括 deprecated、retired 和被合并冻结的 Artifact，用于查找之前已经存在的数据。
- `by_source` 反查 lineage 引用了某条 Source 的 Artifact，用于 Source 更新后找到需要修订的 Artifact。
- 读取返回的快照都带精确 revision，可以直接作为写操作的 `artifact_refs` 或 `to_revision`。

### 3.5 一致性与幂等

- **核心数据**：每个窗口的 Artifact 写操作、游标推进和调用确认在一个事务中提交。失败时核心数据完全不变，重试从干净状态
  开始；成功后游标已越过该窗口，同一窗口不会再次投递。冲突（例如 head 在读取后被用户修改）在操作时或提交时报告，整体
  回滚后由 Supervisor 重新调度，下一次调用读到修改后的状态。
- **重复处理同一业务对象**：不消费 Source 的定时整理等场景会再次处理已经处理过的对象。插件用 `writer.stable_id(...)` 从业务
  身份派生 Artifact ID；`put` 在内容相同时为 no-op，`set_lifecycle` 目标状态与当前相同时为 no-op，`merge` 在输入已全部
  合并到同一结果时为 no-op，从而不会产生重复 Artifact 或无意义的 revision。
- **事务外读取后写入**：插件在事务外读到某个 Artifact，又在写事务块中对它 `put` 而没有给出 `expected_revision` 时，core
  以事务外读到的 revision 作为期望值。读取后该 Artifact 被用户修改，写入即冲突，本次调用回滚后重新调度。这与内置
  Atomic Memory 在提交前核对所读记忆是否变化的做法一致，用户修改不会被插件基于旧内容的写入静默覆盖。
- **插件自有存储**：插件的索引和其他存储（core 分配的专属存储，或插件自己连接的任何数据库、对象存储、向量库）不在 core
  的事务中。插件用自己的客户端写入并提交自有存储，core 不参与，也不保证两者一致。core 的事务是核心数据的唯一提交点：
  自有存储中的数据只有被 core 中已提交的 revision 引用后才算生效。这不需要两阶段提交，按下面的做法即可保持一致：
  1. 在写事务块内、拿到 `ArtifactSnapshot` 之后写自有存储，按 `artifact_id` upsert。这样自有存储的写入失败会让核心写入
     一起回滚。
  2. 写事务提交失败时，自有存储可能留下本次的条目。重试会处理同一窗口、得到同一个稳定 ID，覆盖这些条目。
  3. 把自有索引当作候选来源：命中的 ID 用 `get` 读取 core 中的当前快照，以快照内容和状态为准。这样回滚留下的条目
     不会让插件读到过期内容。插件也可以定期按 core 数据重建索引。
  4. 大块内容（正文、分块、文件字节）按内容摘要存成不可变的记录：先写入并提交到自有存储，再在写事务块中提交内容里
     带有该摘要的 Artifact。不能用 `(artifact_id, revision)` 作为键：写事务回滚后，同一个 revision 号会再次分配给内容
     不同的写入。按内容摘要存储时，重试写入同样的内容得到同一个键；提交失败只会留下没有被任何 revision 引用的记录，
     由插件按"不被引用"定期清理。
- **对插件自有存储的要求**：上述做法与存储的种类无关，但存储需要满足三点，插件选型时自行确认，core 不做检查：
  - 能按插件给定的键幂等写入（upsert 或"不存在才插入"）。只能由存储分配 ID 的系统，需要插件先确定 ID 再写入。
  - 写入返回时数据已经持久化。
  - 写入后立即可读。有刷新延迟的系统（例如 Elasticsearch 的 refresh 间隔、异步建索引的向量库）可能出现 core 已经引用、
    自有存储中暂时读不到的情况，插件读取时需要重试或按暂时缺失处理。
- `generate` 本身可能因失败重试而执行多次，模型调用和插件发出的外部请求不是恰好一次。

## 4. 用户对插件 Artifact 的修改

用户修改使用 core 的通用 Artifact 接口。Server 完成鉴权和版本预检后，把请求交给该插件的宿主进程；插件方法在宿主进程中
执行，通过写入器完成写入，结果在一个短事务中提交并同步返回。用户修改不进入 Supervisor，也不排队。

### 4.1 请求与方法

```python
class CreateRequest(BaseModel):
    scope_id: str
    artifact_id: str                      # core 分配，调用方不提交
    content: dict[str, JsonValue]

class UpdateRequest(BaseModel):
    scope_id: str
    artifact_id: str
    expected_revision: int
    content: dict[str, JsonValue]

class MergeRequest(BaseModel):
    scope_id: str
    inputs: Sequence[MergeInput]          # 每项为 artifact_id、revision、state_version
    into: str                             # core 分配的结果 ID
    content: dict[str, JsonValue]

class ForgetRequest(BaseModel):
    scope_id: str
    artifact_id: str
    expected_revision: int
    expected_state_version: int

class RestoreRequest(BaseModel):
    scope_id: str
    artifact_id: str
    operation: Literal["restore", "undo_merge"]
    revision: int | None = None           # restore 时可指定恢复到的 revision

class InvalidContent(Exception):          # 插件拒绝内容；接口返回 422，消息和字段返回给调用方
    def __init__(self, message: str, *, field: str | None = None) -> None: ...
```

| 方法 | 插件必须完成的写操作 |
| --- | --- |
| `create_artifact(request, writer)` | 恰好一次 `put(request.artifact_id, ...)`，目标此前不存在 |
| `update_artifact(request, writer)` | 恰好一次 `put(request.artifact_id, ..., expected_revision=request.expected_revision)` |
| `merge_artifacts(request, writer)` | 恰好一次 `merge(输入 ID, into=request.into, ...)`，输入与请求一致 |
| `forget_artifact(request, writer)` | 恰好一次 `set_lifecycle(request.artifact_id, "deprecated")` |
| `restore_artifact(request, writer)` | `restore` 为恰好一次 `revert` 或 `set_lifecycle(..., "active")`；`undo_merge` 为恰好一次 `undo_merge(request.artifact_id)` |

- 这些调用中的写事务只允许上表对应的那一项写操作，以及对同一 Artifact 的 `set_tags`；写其他 Artifact 时立即抛出异常。
  方法正常返回但没有完成要求的写操作，core 回滚并返回 502，表示插件没有按约定完成。
- 插件可以规范化内容（例如补全默认字段），写入的内容以插件提交的为准。
- `create_artifact`、`update_artifact` 中的 `put` 总是追加 revision，内容与当前 revision 相同时也一样。这与内置 family 的
  编辑一致：每次用户编辑都有一条新的 `lineage_only` Content Source（见 4.2），需要一个新 revision 记录它。
- 插件应在写事务块中、拿到快照之后更新自己的存储，做法同 3.5。

### 4.2 core 负责的部分

- **鉴权**：与内置 family 相同。新建和新建合并结果需要 Scope 的贡献权限，结果归属于调用者；修改已有 Artifact 需要该
  Artifact 的写权限（`artifact.write`）。插件在这些调用中只获得完成本次请求所需的写入范围。
- **版本校验**：调用插件前先检查 `If-Match`、`revision` 和 `state_version`，不一致直接返回 412 或 409，不调用插件；提交时
  在写事务中再次校验，期间被其他写入改变则回滚并返回 409。
- **溯源**：与内置 family 相同，新建、编辑、恢复和撤销合并时，core 在同一事务中把调用方提交的内容另存为一条内部
  Content Source（`lineage_only`，操作分别为 `artifact_create`、`artifact_replace`、`artifact_restore`、`artifact_undo_merge`），
  作为新 revision 的直接来源记入 lineage。这类 Source 由 core 写入，不进入任何插件的输入窗口。
- **合并注册表**：通用的合并、遗忘、恢复接口建立在共享合并引擎（[RFC #1859](1859_artifact_merge.md)）的注册表之上，对所有登记过的 family 生效。内置 family 登记了 Experience 和 Atomic Memory，
  由各自的适配器处理；插件 family 在加载时登记，处理方式是转交给插件对应的方法。没有登记的 family 返回 422。
- **不提供预览**：Atomic Memory 的专用接口支持先预览恢复影响、再凭签名 token 执行。通用接口不提供预览，也就不需要签名
  密钥；恢复和撤销合并按执行时的当前状态进行，合并和遗忘依靠 `revision` 与 `state_version` 校验。
- **tags**：`GET` / `PUT .../{artifact_id}/tags` 和 `POST /v1/scopes/{scope_id}/artifact-tags/query` 对插件 family 与内置
  family 相同，由 core 直接处理，不调用插件，也不产生新的内容 revision。

### 4.3 接口

| 接口 | 规则 |
| --- | --- |
| `POST /v1/scopes/{scope_id}/artifacts` | 请求体增加插件分支 `{"family": "<插件 family>", "content": {...}}`，family 不能是内置 family 的名字；ID 由 core 分配；响应与内置 family 的新建相同，包括 `Location` 和 `ETag` |
| `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 请求体为 `{"content": {...}}`；`If-Match` 与当前 revision 不一致返回 412；Artifact 必须已存在 |
| `GET .../{artifact_id}/governance` | 返回当前 revision、生命周期状态、`state_version` 和合并目标；由 core 处理 |
| `POST /v1/scopes/{scope_id}/artifacts/{family}/merges` | 输入为至少两个当前 Artifact 的 `artifact_id`、`revision`、`state_version`，以及结果的完整内容；新建结果 Artifact 并冻结所有输入 |
| `POST .../{artifact_id}/forget` | 携带 `revision` 和 `state_version`；把 Artifact 标为 deprecated（无替代者），不追加 revision，退出检索 |
| `POST .../{artifact_id}/restorations` | `operation` 为 `restore`（默认，可指定 `revision` 恢复到旧内容）或 `undo_merge`；按组执行级联恢复和退役 |

| 情况 | 响应 |
| --- | --- |
| 插件没有实现对应方法 | 422，说明该 family 不支持此操作 |
| 插件抛出 `InvalidContent` | 422，带插件给出的消息和字段 |
| 版本不一致 | 412（`If-Match`）或 409（`revision`、`state_version`） |
| 插件抛出其他异常 | 500，只记录异常类型，不返回异常消息 |
| 插件没有完成要求的写操作 | 502 |
| 超过 `change_timeout_seconds`、宿主进程不可用或处于重启退避 | 503 |

- 新建请求的插件分支没有判别映射：OpenAPI 无法为任意插件 family 写映射。内置分支保留原来按 family 的判别，插件分支与它
  并列，由 family 名决定走哪个分支，内置 family 的请求不会被插件分支接受。这与 tags 接口把 family 改为字符串的处理相同。
- 只有删除语义的需求（例如撤下一份文档）使用遗忘；core 不提供物理删除。插件可以在 `forget_artifact` 中清理自己存储中的
  正文和索引，core 中的 revision 与 Source 保留。
- **并发**：用户修改与后台的 `generate` 可能同时发生。`generate` 对事务外读过的 Artifact 写入时，core 按读到的 revision
  校验（见 3.5），发现冲突即整体回滚，下一次调用基于修改后的状态重新决策；用户修改不会被插件基于旧内容的写入静默覆盖。

## 5. 消费游标

Artifact 插件的每次后台调用处理该 Scope 中游标之后的一个 Source 窗口：

- 窗口不超过 `inputs.max_sources` 条。窗口中没有匹配的 Source 时不加载插件，直接推进游标并确认。
- 窗口内类型不匹配的 Source 被过滤，但游标仍推进过整个窗口；匹配类型的 Source 缺少所需 Projection 时调用失败，不跳过。
- 游标只在执行器的提交事务中以 CAS 推进，插件得不到游标值，也没有写入接口。读取窗口与提交之间即使发生领导权变更或
  重复派发，同一窗口也只提交一次。
- 是否有待处理的工作由持久化数据判断（Source journal 位置与游标之差），Server 重启后不会丢失待处理输入。
  `on_sources` 的插件在新 Source 写入后约 2 秒内被派发。
- 未配置 `scopes` 时，自动调度对所有 journal 超过游标的 Scope 生效；配置了 `scopes` 时只接受名单内的 Scope。

## 6. 插件宿主进程

同步调用需要在请求时限内返回，不能每次都启动 Worker：每个 Worker 都是全新的 Python 进程，导入 core 的执行模块需要数秒。
因此同步调用交给常驻的宿主进程：

| 调用 | 执行位置 |
| --- | --- |
| Source 写入的输入校验、`resolve`、Projection | 宿主进程 |
| `create_artifact`、`update_artifact`、`merge_artifacts`、`forget_artifact`、`restore_artifact` | 宿主进程 |
| `render_artifact`、`search_artifacts` | 宿主进程 |
| `generate` | Supervisor Worker |

- **数量与启动**：每个 Server 进程为每个加载的插件维护至多一个宿主进程，插件加载后在后台启动，之后复用。插件都要实现
  `render_artifact`，所以每个加载的插件都有宿主进程。
- **执行**：宿主进程启动时导入一次 `plugin.py` 并核对声明，写入器在宿主进程中执行。一个宿主进程可以并发处理多个请求，
  并发上限由 Server 配置决定。
- **超时与故障**：每次调用受各自的时限约束（Source 写入、展示和检索使用 Server 的同步调用时限，用户修改使用
  `change_timeout_seconds`）。超时、崩溃或失去响应时，core 终止该进程，进行中的请求返回 503，并在退避后重新启动；退避期间
  该插件的同步调用返回 503，后台调用不受影响。
- **代码变更**：宿主进程在存续期间不重新导入插件，启动时核对代码指纹。修改插件后需要重启 Server，与 Worker 的规则一致。
- **隔离**：与 Worker 相同，宿主进程隔离普通故障，不是不可信代码的沙箱。

**已激活插件的内存记录**：每个 Server 进程在内存中记录本进程加载的插件：插件名、目录、类型、声明摘要、检查结果，以及
宿主进程的当前状态（未启动、运行中、重启退避）。路由用户请求、启动宿主进程和派发 Worker 都以这份记录为准。它不持久化，
也不跨进程同步；Server 重启后按插件目录重新建立。运维接口只返回各进程一致的检查结果和声明，宿主进程状态通过日志和指标查看。

## 7. 公开读取、展示与检索

读取由 core 直接提供，不执行插件代码：

- list / get / revisions / governance / tags 复用现有的通用 Artifact 读取路径。插件 family 的 `content` 按插件写入时的
  JSON 原样返回，不需要 `pc_artifact_family_schemas` 中的 schema。被合并冻结、deprecated 和 retired 的 Artifact 按公共
  合并与生命周期规则展示。
- list 返回的条目只含元数据，`title` 和 `summary` 对插件 family 为空；内容需要通过 get 读取。

插件可能只在 `content` 中保存摘要或引用，把大块内容放在自己的存储中（例如知识库文档只记录切块组的摘要），这时原样返回
的 `content` 不足以展示。展示和检索因此交给插件：

```python
class RenderRequest(BaseModel):
    scope_id: str
    artifact: ArtifactSnapshot            # 要展示的精确 revision
    caller: Caller | None

class Rendering(BaseModel):
    format: Literal["markdown", "text"] = "markdown"
    text: str

class SearchRequest(BaseModel):
    scope_id: str
    search: dict[str, JsonValue]          # 通用检索接口的完整请求（JSON），原样转交
    max_bytes: int | None = None          # 来自 Prepared Context 时，请求的字节预算
    caller: Caller | None

class SearchHit(BaseModel):
    ref: ArtifactRef                      # 命中的精确 revision
    text: str                             # 用作上下文的片段，例如一个切块
    score: float | None = None
    locator: dict[str, JsonValue] | None = None   # 片段在 Artifact 中的位置，例如页码、切块序号

class SearchResult(BaseModel):
    hits: Sequence[SearchHit]
```

- **展示**：`GET .../{artifact_id}/rendering` 调用 `render_artifact`，返回插件给出的默认展示内容；原始 `content` 仍由读取
  接口返回，供程序使用。同一 revision 在同一插件版本下的展示结果不变，core 可以按 family、ID、revision 和插件版本缓存。
  内置 family 不提供这个接口。
- **检索**：通用检索接口把 `SearchArtifactsRequest` 的全部字段（`query`、`query_vector`、`indexes`、`mode`、`filters`、
  `admission`、`fusion`、`rerank`、`min_score` 等）原样交给 `search_artifacts`，由插件按自己的索引解释；插件不支持的参数
  抛出 `InvalidContent`，接口返回 422。core 只保留命中中调用者有权读取的 active 当前 revision，按插件给出的顺序返回
  精确 revision，`include_scores` 时附带插件给出的分数。插件因此不能借检索返回调用者看不到的数据。
- **Prepared Context**：`assembly.sections` 可以列出插件 family。core 以 Prepared Context 的 `query`、该项的 `limit` 和
  请求的字节预算调用 `search_artifacts`，把命中的片段作为上下文条目按 section 顺序装填，与内置 family 一样带精确 revision
  引用、计入字节预算和回执，超出预算的条目被截断或省略。每项可以携带可选的 `search`，内容是检索请求中除 `query`、`limit` 以外的字段，原样转交插件；内置 family 携带
  `search` 时返回 422。
- Review 和 Recall 不对插件 family 开放，对应接口返回明确的“不支持”错误；`GET /v1/capabilities` 列出插件 family。

## 8. 加载与生命周期

- Server 启动时检查 `POWERCONTEXT_SERVER_PLUGINS_DIR` 的每个直接子目录（未设置则不加载插件）：在有时限的子进程中导入
  `plugin.py` 并校验声明，不调用 `setup`。检查通过后注册插件提供的 Source 类型或 family（读取、检索和通用合并接口），
  有调度或触发时注册 Supervisor binding，并写入本进程的已激活插件记录。检查失败只影响该插件。
- Server 停止时先停止接受同步调用、结束宿主进程，再按现有流程停止 Supervisor 并回收 Worker。
- 插件只有 `loaded` 和 `failed` 两种结果，只存在于进程内存中。Server 不持久化插件的启用状态、注册代次或配置快照；
  每次启动按当时的目录内容重新检查。插件代码只在检查子进程、宿主进程和 Worker 中导入，Server 进程不导入插件代码。
- 没有热加载，因此不需要注册代次来屏蔽旧 Worker，现有的 Supervisor 租约 fence 已经足以屏蔽领导权变更后的旧提交。
- Worker 每次调用重新导入插件代码，导入前核对代码指纹（见 1.1）。插件目录在 Server 运行期间被修改后，新的 Worker 调用
  和新启动的宿主进程都会失败并提示重启，不会出现同一个 Server 中新旧代码混用。部署者应在修改后重启 Server。
- `api` 角色的 Server 实例不运行后台任务，但也会检查插件并注册 family 和 Source 类型，用于读取、检索、Source 写入和用户
  修改；它按需启动宿主进程，不派发 Worker。`background` 角色的实例不接收请求，不启动宿主进程。
- 运行中增减插件（热加载）和插件自定义 API 见 [RFC #2579](https://github.com/oceanbase/powercontext/pull/2579)，建立在宿主进程之上。

## 9. 执行约束

- **隔离**：`generate` 在 Supervisor 启动的 Worker 子进程中执行，同步调用在宿主进程中执行，崩溃、内存泄漏和
  阻塞不影响 Server。子进程隔离普通故障，不是不可信代码的沙箱：插件以子进程的操作系统权限运行，可以访问其文件、网络和
  环境变量。
- **超时与预算**：`timeout_seconds` 是 Worker 总时限，`transaction_timeout_seconds` 是写事务块时限，`max_workers` 是该插件
  跨 Scope 的并发上限，分别映射到 Supervisor binding 的现有参数或由执行器强制。
- **写事务的锁影响**：写事务块持有期间会锁住涉及的 head 和游标行；在 SQLite 上，写事务会阻塞整个数据库的其他写入。
  因此写事务块必须短，时限超出即回滚。
- **遗留的调用意图**：插件移除后，它遗留的调用意图不再被处理，也不阻塞数据库就绪检查。
- **失败与退避**：调用失败时不提交任何核心数据，该插件该 Scope 进入 Supervisor 现有的退避，不影响其他 Scope 和插件。
  core 不会因为重复失败自动跳过输入；持续失败需要修复插件或数据后重启。
- **多实例部署**：OceanBase 多 Server 实例时，派发只发生在持有 Supervisor 领导租约的实例上；Source 写入、读取、
  展示、检索和用户修改可以在任意实例执行，各实例使用自己的宿主进程。插件自有存储需要能被所有实例访问；core 分配的专属存储
  位于共享的数据库后端上，天然满足这一点。SQLite 部署只有一个 Server 进程。

## 10. 持久化

| 数据 | 处理 |
| --- | --- |
| `pc_sources`、Projection、Source journal、`pc_source_definition_manifests` | 保留；插件 Source 类型的定义与写入结果 |
| `pc_artifacts`、`pc_artifact_heads`、`pc_artifact_lineage_*`、合并记录、handle、tags | 保留；Artifact 插件的结果 |
| `pc_source_cursors` | 保留；Artifact 插件的消费游标，键为 `(scope_id, plugin:<name>)` |
| Supervisor 的调用意图、pending、租约表 | 保留；插件 binding 与内置 binding 共用，也是 `GET /v1/plugins/{name}/scopes` 的数据来源。插件 binding 以 `plugin:` 开头，不在数据库中登记 |
| `pc_artifacts` 的新列 `metadata` | 新增；可为空，按与 `content` 相同的方式保存规范化 JSON 字节，存放不参与检索的附加信息。插件写入的 revision 记录插件名和插件版本摘要，内置 family 为空。不参与内容摘要的计算 |
| `pc_artifact_fts` 与 `pc_artifact_heads.searchable_text` | 只用于内置 family；插件 family 不写入 |
| `pc_artifact_processing_binding_states` 中为旧插件框架新增的列（期望状态、管理阶段、注册代次等） | 移除 |
| `pc_artifact_family_schemas` | 移除 |
| 自定义 family 声明式索引使用的搜索状态与向量索引数据 | 移除；实现 PR 确认内置 family 不依赖后删除对应表 |
| 旧 Artifact 插件写入的 Artifact、head、lineage | 不迁移；留有数据时拒绝升级，见下文 |
| 插件专属存储（`plugins/<插件名>.sqlite3` 或 `powercontext_plugin_<插件名>` 库） | 不属于 core 的 schema；core 只负责分配，表结构、迁移和清理由插件和部署者负责 |
| 插件自有的其他索引与存储 | 不属于 core，由插件和部署者管理 |

新增与移除通过**一个新的迁移版本**完成，已发布版本的迁移资源保持不变。迁移说明中列出新增、删除的表、列和数据范围。
迁移检查（preflight）先确认旧 Artifact 插件框架没有留下数据：旧插件 family 的 Artifact、旧插件的登记与调用意图。
发现任何一项时拒绝升级，列出插件名、family 和行数，数据库保持不变；部署者
在旧版本上导出所需内容、卸载旧插件并清理数据后再升级。只有这些表为空时，迁移才删除它们。

这个版本的就绪检查（Server 每次启动时对当前数据库执行）与旧版本有两点不同：

- 调用意图中以 `plugin:` 开头的 binding 视为插件 binding，不要求在数据库中登记。插件被移除后遗留的意图不处理、
  可以在运维接口中看到，不阻止 Server 启动；同名插件重新放入后继续处理。
- 全文索引的核对只覆盖内置 family 的数据。

导出与恢复（portable archive）中 Artifact revision 记录的格式版本加一，包含 `metadata`。恢复旧格式的导出时 `metadata`
为空；旧版本的 Server 不能恢复新格式的导出。

## 11. 接口变更

| 变更 | 内容 |
| --- | --- |
| 移除 | `/v1/admin/artifact-plugins` 及其子路径；旧 Artifact 插件的 `plugin.yml` 清单 |
| 移除 | 自定义 family 的声明式索引（`ArtifactSearchDeclaration`、`TextIndex` 等）、`ArtifactFamily` 的 schema 声明、`ArtifactDraft` 和旧的 Worker 插件执行器 |
| 新增 | `powercontext.plugins`：`ArtifactPlugin`、`Inputs`、`Interval`、`Cron`、`PluginContext`、`PluginStorage`、`PluginModels`、写入器与读取接口、用户修改的请求模型与 `InvalidContent`、展示与检索的请求和结果模型 |
| 调整 | `POST /v1/scopes/{scope_id}/artifacts` 的请求体增加插件分支 `{"family", "content"}`，ID 由 core 分配，由插件的 `create_artifact` 处理。`CreateArtifactRequest` 改为内置分支 `CreateBuiltinArtifactRequest`（原有的按 family 区分的联合类型）与插件分支 `CreatePluginArtifactRequest` 的二选一，按 family 名选择：内置 family 名走内置分支，校验与原来相同；其他名字走插件分支，没有插件提供该 family 时返回 422。生成的 Python 客户端中，内置请求多包一层；`ArtifactCreated.family` 从内置 family 枚举改为字符串 |
| 调整 | `POST /v1/scopes/{scope_id}/sources` 的 `source_type` 从只能是 `content` 的枚举改为字符串（默认仍为 `content`），可以是插件定义的 Source 类型，此时 `content` 是该类型的输入，由插件校验并解析为 Source；未知类型返回 404。`SourceType` 枚举保留 |
| 新增 | `GET /v1/plugins`、`GET /v1/plugins/{name}`、`GET /v1/plugins/{name}/scopes`（`server.admin`，只读） |
| 新增 | `GET .../{artifact_id}/governance`、`POST /v1/scopes/{scope_id}/artifacts/{family}/merges`、`POST .../{artifact_id}/forget`、`POST .../{artifact_id}/restorations`：基于共享合并引擎注册表的通用接口，对所有登记过的 family 生效（内置为 Experience 和 Atomic Memory，以及加载的插件 family；插件 family 由插件对应的方法处理）。Atomic Memory 的专用接口保持不变 |
| 调整 | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` 接受插件 family，请求体为 `{"content": {...}}`，由插件的 `update_artifact` 处理 |
| 调整 | tags 接口接受插件 family：`TaggableArtifactFamily` 从枚举改为与 `ArtifactReadFamily` 相同格式的字符串，服务端校验为内置 family 或已加载的插件 family。网络上的数据格式不变，生成的客户端少了这个枚举类型 |
| 调整 | 通用 Artifact 读取接口支持插件 family；通用检索接口对插件 family 把完整请求交给插件的 `search_artifacts`，未实现时返回 422；`GET /v1/capabilities` 列出插件 family |
| 新增 | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/rendering`：插件 family 的默认展示内容，由插件的 `render_artifact` 提供 |
| 调整 | `POST /v1/context/prepare`：`ContextAssemblySection.family` 从枚举改为字符串，可以是已加载的插件 family；新增可选的 `search`（检索请求中除 `query`、`limit` 以外的字段），只对插件 family 生效；回执中的 `PreparedContextFamily` 从枚举改为字符串，可以是插件 family |
| 调整 | 导出与恢复的 Artifact revision 记录格式版本加一，增加 `metadata` |
| 调整 | 环境变量 `POWERCONTEXT_SERVER_PLUGINS_DIR` 取代 `POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR` |

`openapi/powercontext.yaml` 按上表更新后重新生成 `src/powercontext/http/_generated/` 和各集成的操作表。

## 12. 可观测性

插件调用复用 Supervisor 与 Worker 现有的操作词汇、执行身份和结果字段，不为定时调用生成 request ID。新增的字段标识
插件名、调用的方法、执行位置（Worker 或宿主进程）、触发原因和写入规模；宿主进程的启动、终止和重启各记录一条事件。

- 指标标签可以使用插件名、方法和执行位置，不包含 Scope ID。
- 日志经 `log_safely` 输出，不包含 Source 内容、Projection、Artifact 内容和环境变量值；插件异常只记录类型和
  已知错误分类，不记录异常消息。
- 新增字段按开发指南登记到允许列表，并在中英文可观测性文档中说明。
- 调用结果只进入日志和指标，不持久化，运维接口不提供“最近一次调用结果”。

# 缺点

- **宿主进程常驻占用资源**：每个 Server 进程为每个需要同步调用的插件多一个常驻进程，多实例部署时数量随实例数增加。
  同步调用多一次进程间往返和序列化。
- **同步调用依赖插件可用**：宿主进程崩溃或处于重启退避时，该插件的 Source 写入和用户修改返回 503；插件代码中的缺陷会
  直接表现为用户请求失败。
- **写事务持锁**：写事务块期间持有数据库锁，SQLite 上会阻塞其他写入。插件若在块内执行慢操作，会拖慢整个 Server 的写入，
  直到块超时回滚。
- **插件自有存储与核心数据不保证一致**：两者不在同一事务中，提交失败可能在自有存储中留下条目。插件需要按稳定 ID 覆盖、
  按内容摘要保存不可变记录、清理未被引用的记录，并以 core 的当前数据为准；自有存储还要满足幂等写入、写入即持久和
  写后可读的要求。
- **后台调用每次都要启动 Worker 并加载插件**：子进程启动和工厂初始化有固定开销，不适合秒级高频的后台调用；插件不能在
  Worker 调用之间复用连接或缓存。
- **专属存储只按约定隔离**：插件与 Server 使用同一个数据库登录，技术上能访问 core 的表。
- **新旧 Source 扩展方式并存**：在后续提案落地前，添加 Source 类型有两种方式：本提案中插件自带的 Source 类型，以及旧
  Source 插件框架的 write 插件。两者登记到同一份定义清单，名字不能重复。
- **不是安全沙箱**：Worker 隔离普通故障，不限制插件的文件、网络和凭据访问，也不提供硬性的内存或 CPU 配额。
- **依赖必须兼容**：所有插件与 Server 共用一个 Python 环境，依赖冲突需要部署者解决。
- **同一份代码部署多次需要额外处理**：family 写在声明中，同一份代码复制成两个目录会因 family 冲突而检查失败。需要多次
  部署时，插件应从环境变量读取 family 等标识。
- **内容结构不受 core 治理**：core 不校验 `content` 的 schema，用户提交的内容由插件校验；历史 revision 的解释需要插件和
  应用自行保持兼容。
- **展示和检索依赖插件**：插件 family 的展示、检索和 Prepared Context 都要进入宿主进程，插件不可用时这些接口返回 503；
  插件没有实现 `search_artifacts` 时，它的数据不能通过通用检索和 Prepared Context 使用。
- **多个 Server 的插件版本可能不一致**：core 记录每个 revision 由哪个插件版本写入，但不阻止不同实例加载不同版本；新旧
  版本能否同时读写同一份数据，由插件负责。汇总各实例当前加载的版本需要维护一张实例登记表，留给插件安装的后续提案。
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
| 用户修改排队，插件在下一次后台调用中处理 | 调用方只能异步等待；一个始终失败的修改会阻塞该 Scope 的后续生成；还需要持久化请求队列，而 core 目前没有任务管理机制 |
| 用户修改由 core 直接执行，不经过插件 | 实现简单，但插件拿不到用户提交的内容：无法校验，自有存储和索引只能等之后重建才能跟上。存储由插件决定，修改也应由插件处理；内置 family 的新建和编辑同样经过各自的 family 代码 |
| 用一个 `write` 方法同时处理新建和编辑 | 新建不带 ID、由 core 分配，编辑必须指定 ID 和期望 revision；两者的输入和校验不同，合成一个方法需要插件自己分辨 |
| 同步调用每次派发一个 Worker | 复用现有机制，但 Worker 每次都启动新的 Python 进程，仅导入 core 模块就需要数秒，不能放在用户请求路径上 |
| Source 的 `resolve` 等代码在 Server 进程中执行 | 省去进程间往返，但插件阻塞或崩溃会影响 Server；插件的同级模块以顶层名字进入 `sys.modules`，多个插件的同名模块会互相冲突，后续也无法可靠地重新加载 |
| 常驻宿主进程处理同步调用，Worker 处理后台调用（本提案） | 同步调用不需要每次启动进程，Server 进程不导入插件代码；后台调用保留 Worker 的按次隔离和预算。代价是常驻进程的资源占用和进程管理 |
| 插件存储进入 core 的事务（插件在 core 库中建表） | 可以原子提交，但 core 要管理插件的迁移、升级和权限，三种后端都要支持，插件数据会混入 core 的 schema。插件的存储由插件决定，不放进 core |
| 插件存储与 core 之间用两阶段提交 | 对象存储、向量库等多数插件存储不支持 XA，core 也要为每种存储实现协调者。按内容摘要先写不可变记录、以 core 的提交为唯一生效点，就能得到需要的一致性 |
| 一个插件声明多个 family | 可以在一个事务中写多个 family，但声明、权限和合并注册都要按多 family 设计。需要配合的场景用两个插件加跨 family 只读即可 |
| 把 core 的环境变量或数据库配置交给插件 | core 的配置不全来自环境变量；插件拿到 core 的数据库凭据就能绕过写入器。提供封装好的 `ctx.storage` 和 `ctx.models` |
| 插件专用的触发与管理接口 | 调用方要区分插件与内置能力；现有的 Source 与 Artifact 接口已经覆盖调用方需要的操作 |
| 通用合并接口只对插件 family 开放 | 需要在接口里额外维护一份 family 白名单。能操作哪些 family 本来就由注册表决定，没有适配器的 family 无法登记；内置 family 的权限检查在引擎内，两个入口走同一套检查 |
| core 按声明的字段为插件 family 建全文索引 | 插件作者不用自建索引，但 core 不知道内容怎样分块、是否适合全文或向量检索，建出的索引常常用不上；还要与内置 family 共用索引表，就绪检查也要区分两者。检索交给插件，core 只负责权限过滤 |
| 读取也经过插件（例如 `read_artifact` 展开内容） | 通用读取就能拿到完整内容，但最常用的读取路径每次都要进入宿主进程，插件不可用时连读取都失败。读取保持由 core 提供，展示另设接口 |
| 在数据库中登记插件 binding 与 family（旧 Artifact 插件框架的做法） | 能发现其他插件冒用已有 family 和遗留的调用意图，但 core 要保存并维护插件状态，还要提供解除冲突的管理操作。新框架没有稳定的插件身份，登记表能多发现的问题有限；留到插件安装与分发的提案中，与插件身份一起设计 |
| 插件版本记在单独的表、`created_by` 或 `content` 的保留键中 | 单独的表同样要进入导出与恢复；改动 `created_by` 或 `content` 会改变已有字段的含义和内容摘要。`metadata` 列只需一次加列和导出格式升级 |
| 本提案中支持热加载 | 需要处理多实例各自重新扫描、旧 Worker 提交的屏蔽和宿主进程切换，单独提案更清楚 |

选择这一方案的原因：

- 插件作者只需写一个 `plugin.py`：一个声明、一个工厂、一两个方法。
- 调用方只用现有的 Source 与 Artifact 接口，不感知插件。
- 隔离、调度、触发、退避和领导权都复用已经在生产中使用的 Supervisor。
- 写操作直接复用 core 现有实现，插件写入和用户修改遵循同一套语义；用户修改经过插件，插件存储能同步更新。
- Source 与 Artifact 留在 core 的统一存储中，现有读取接口、lineage、合并记录和访问控制直接适用。
- core 只管理两类核心数据及其进度，其余交给插件。

# 先例

- **Dify 插件**：按插件类型定义接口，插件实现对应方法，Dify 在工作流或应用需要时调用；插件运行在独立的插件守护进程中。
  本提案沿用“宿主定义接口与调用时机”的思路：同步调用使用 core 管理的常驻宿主进程，后台调用复用 PowerContext 已有的
  Supervisor Worker。
- **pytest 插件与 setuptools entry points**：插件以 Python 对象声明自己提供的能力，宿主导入后读取，不需要单独的清单文件。
- **事务上下文管理器**：SQLAlchemy 的 `async with engine.begin()` 等接口在块开始时开启事务、正常结束时提交、异常时回滚。
  `writer.transaction()` 沿用这一使用方式，并在提交时附带游标与调用确认。
- **PowerContext 现有基础**：[Artifact processing Supervisor](1515_artifact_processing_supervisor.md) 的调度、调用意图、
  Worker 与租约 fence；[公共合并方法](1859_artifact_merge.md)；通用 Artifact 读写接口与内置 family 编辑时记录的
  `lineage_only` Content Source；`SourceDefinition` / `SourceProjection`、Source journal 与消费游标。

# 未决问题

| 问题 | 说明 |
| --- | --- |
| 写事务块的时限与限额默认值 | 默认 30 秒是否合适；SQLite 与 OceanBase 是否采用不同默认值；单块操作数与字节上限 |
| 宿主进程的默认参数 | 同步调用时限、单个宿主进程的并发上限和重启退避的默认值 |
| 专属存储的建库权限 | Server 的数据库登录没有建库权限时，除了由部署者事先建库，是否允许退回到 core 库中按插件名前缀建表 |
| 插件 family 接入 Recall | Prepared Context 已接入，Recall 是否需要接入、候选内容怎样定义 |
| 通用检索结果带片段 | 通用检索接口目前只返回精确 revision，插件返回的片段只用于 Prepared Context。是否在检索结果中增加片段和位置 |
| family 与 Source 类型的命名 | 是否要求以插件名为前缀，从命名上避免不同插件冒用同一个 family |
| Prepared Context 的检索控制 | 内置 family 也接受每项的 `search`，是新增的可选字段，不影响现有调用，建议另行提案。Prepared Context 中其他按功能定制的参数，移除或改名是不兼容变更，需要新的 API 版本，不在 v1 中整理 |
| 窗口大小的回退 | 内置 Atomic Memory 在模型超时时会缩小下一次的 Source 窗口。插件无法影响窗口大小，过大的窗口会持续超时。是否允许插件以约定的异常请求缩小该 Scope 的窗口 |
| 持续失败的窗口 | 始终失败的 Source 窗口会阻塞该 Scope 的后续调用。是否提供跳过或转入死信的运维操作，以及如何记录被跳过的输入 |
| 消费全部 Source 类型 | `inputs.source_types` 需要逐一列出类型；内置 Atomic Memory 消费所有可用于生成的 Source。是否支持 `"*"` 表示全部类型 |
| 内置专属能力 | Scope Prompt 管理、写入闸门与决策观测、模型用量统计目前只服务内置 family，插件无法接入。是否以及如何向插件开放 |
| Atomic Memory 专用接口的去留 | 通用合并接口上线后，Atomic Memory 的合并、遗忘、恢复同时有专用和通用两个入口，行为一致。专用接口是否保留，或改为通用接口的别名 |

# 后续可能

- 热加载与插件自定义 API（含插件页面）：建立在宿主进程之上，见 [RFC #2579](https://github.com/oceanbase/powercontext/pull/2579)。
- 独立的 Source 插件与采集：只提供 Source 类型的插件；采集目标的集中配置与设备拉取同步；在设备上运行采集并把 Source
  汇报到中心；大文件的传输。由后续提案定义，并取代旧 Source 插件框架。
- 后台调用也改用常驻进程，在保持接口不变的前提下复用连接和初始化结果。
- 更多插件类型，例如为 Recall 提供候选内容、为 Prepared Context 提供文本片段。
- 插件安装与依赖：从 PyPI、Git 地址或 wheel 安装插件，每个插件独立的依赖环境，以及记录各实例加载的插件版本的登记表。

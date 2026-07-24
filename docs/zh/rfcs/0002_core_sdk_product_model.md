- Proposal Name: `core_sdk_product_model`
- Start Date: 2026-07-07
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Last Amendment: 2026-07-16

# Summary

RFC 0001 将 PowerContext 定义为面向人和 Agent 的工作上下文层。本 RFC 只定义第一版 Python Core SDK 的
产品和架构边界：

- Source 保存外部工作材料的稳定引用；
- Artifact 是可继续维护的派生上下文，通过不可变 Revision 演进；
- Core 记录 Artifact Revision 直接使用的 Source 和上游 Artifact Revision；
- Memory 和 Handoff 是 Core 提供固定组合的 Artifact Family；
- SQLAlchemy、fsspec 和 Pydantic AI 保留原生对象与生命周期，Core 不复制平行抽象。

RFC 0001 仍将 Trigger 视为产品概念，本 RFC 不定义它的公共 SDK 形状。第一版中用于后台提炼的内部条件和
调度机制是实现策略，不是公共 Trigger 契约。

本 RFC 是已接受的设计记录，不是实现状态文档。当前 API 可用性应记录在源码和
[Core Protocol 集成导读](../development/core-protocol.md)中。

# Motivation

普通用户需要一条足够短的 Source 接入和上下文检索路径。这不等于 Core 需要接管当前 session、Agent
framework、调度、workflow 或存储后端。

PowerContext 提供持久化、跨 session 的上下文支持，但不作为当前 session 的权威状态。Agent harness
仍然拥有当前消息、tool state、即时上下文和模型调用生命周期。

# Guide-level explanation

## 日常路径

对一般调用方，日常写入可以只到 Source：

```python
pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source = await pc.sources.add(source_input)
```

`Sources.add()` 成功返回只承诺 Source 已提交，不承诺 Memory 或其他 Artifact 已产生。Core 可以
通过内置策略聚合已提交 Source 并在后续提炼 Artifact，但策略何时运行、选择哪些输入以及是否产生
新 Revision，都不是 `Sources.add()` 的 postcondition。

Agent harness 可以在模型调用前 best-effort 检索当时已可见的 Memory 或其他 Artifact 作为补充，
但不能假设最近提交的 Source 已经被提炼。

## 显式生成

当宿主必须在当前工作流中等待 Artifact 生成完成时，可以显式调用领域操作：

```python
from powercontext import GitCommit, PowerContext

pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source_input = await pc.sources.resolve(GitCommit(repository=repository, revision="HEAD"))
    source = await pc.sources.add(source_input)
    decision = await pc.artifacts.add(
        "decision",
        {"summary": "Use SQLite through SQLAlchemy."},
        sources=(source,),
    )
    memory = await pc.memory.remember(
        sources=(source,),
        artifacts=(decision,),
    )
    artifacts = () if memory is None else (memory,)
    handoff = await pc.handoff.prepare(
        "Continue the implementation",
        sources=(source,),
        artifacts=artifacts,
    )
    document = pc.handoff.render(handoff, audience="human")
```

显式调用用于对生成时机有明确要求的工作流，不是普通 Source ingestion 必须执行的步骤。

# Reference-level explanation

## 产品对象

| 对象 | 语义 | 边界 |
| --- | --- | --- |
| Source | 外部工作材料的稳定引用和索引信息 | 原始正文仍归 provider 或宿主所有 |
| Artifact | 由 Source 或其他 Artifact 派生、可检索和继续维护的上下文 | 通过不可变 Revision 演进 |
| Memory | 面向后续任务复用的 Artifact Family | 不代替当前 session state |
| Handoff | 面向下一个参与者的 Artifact Family | human 和 agent view 来自同一 Revision |

Memory 和 Handoff 继承 Artifact 的 identity、Revision 和血缘语义。它们的服务复用 Artifact 读取接口，写入和
搜索行为仍由各 Family 定义。

## Core 不变量

- Source 在一个 Catalog 范围内以 `(source_type, uri)` 幂等提交；
- Artifact Revision 一经提交不可修改，`ArtifactRef` 始终指向精确 Revision；
- `revise()` 对 base Revision 做 optimistic concurrency check，拒绝 stale write；
- 每个 Revision 记录本次计算直接使用的 Source 和上游 Artifact Revision；
- 血缘从调用方传入的完整已持久化对象派生，不从 trace、调用图或 workflow topology 推断；
- 首次 Memory 计算没有可保存变化时返回 `None`，已有 Memory 没有语义变化时返回原 Revision；
- Handoff 的 `objective` 由调用参数决定，模型输出不能改写。

## 公共动作

| 范围 | 动作 |
| --- | --- |
| Source | `resolve`, `read`, `add`, `get`, `list` |
| Artifact | `add`, `revise`, `get`, `revisions`, `list`, `search` |
| Memory | `remember`, `forget`, `organize`, `get`, `revisions`, `list`, `search` |
| Handoff | `prepare`, `get`, `revisions`, `list`, `render` |

该表记录已接受的产品动作。当前可用性和精确签名应记录在源码文档中，而不是本 RFC。

## Composition root 与上游对象

- `await PowerContext.open()` 面向本地使用，创建并拥有 SQLite `AsyncEngine`；
- `await PowerContext.from_backends()` 接收调用方拥有的 SQLAlchemy `AsyncEngine` 和 fsspec
  `AbstractFileSystem`；
- PowerContext 原样暴露注入的 filesystem，不增加文件 facade 或通用 storage lifecycle；
- file-backed Artifact Family 自行定义路径或 URI 的产品语义，Artifact 本身不感知文件存储；
- `model` 和 `embedding_model` 使用 Pydantic AI 原生模型对象或名称，Core 不另外定义 API key、
  base URL 或 provider 配置抽象。

第一版 Catalog 使用 SQLite，只定义 schema version `1`，不包含 schema migration。

## 扩展边界

Typed `SourceProvider[T]` 是第一版唯一开放的 Core 数据获取扩展点。Provider 将原生输入解析为
`SourceInput`，并在 Core 需要时读取已提交 Source 的瞬时文本表示。Provider 在 `PowerContext` 构造时
注册并固定。

Memory extraction、Memory consolidation 和 Handoff generation 使用 Core 固定的内部 pipeline。调用方
可以选择模型，但第一版不定义替换 generation pipeline 的公共协议。

每个 Catalog backend 按自身能力实现检索。Search mode 和排序行为不是已稳定的跨后端契约。

## 后台处理与 Trigger

Core 可以使用内置策略在 Source 提交后提炼 Artifact，例如基于累积数量或周期条件发起处理。具体
阈值、批次、目标 Artifact identity、并发和失败处理仍是待收敛的内部策略。

这些内部条件不实现 RFC 0001 中面向用户的 Trigger 概念，也不构成公共 hook 或 scheduler 扩展契约。

# Drawbacks

- 后台提炼是 best-effort，不提供 Source-to-Artifact read-after-write 保证；
- 不同 Artifact Family 具有不同领域动作，调用方需要理解具体 Family 语义；
- 检索能力可能因 Catalog backend 不同而存在差异；
- 每个 Agent framework 仍需要一个小型原生 adapter。

# Rationale and alternatives

Core 通过具体 Family 服务暴露产品对象，接受上游原生对象，并且只开放 typed Source provider。Memory
和 Handoff 使用固定 generation pipeline。因此 Core 只需维护工作上下文不变量。

Repository、Scheduler、Agent Protocol 和 generation graph 仍归对应上游系统所有。相关能力和生命周期
尚未稳定，现在就定义 Core 抽象只会固化未经验证的边界。

# Unresolved questions

- 内置后台提炼策略如何选择 Source batch、目标 Artifact identity 并暴露失败状态；
- `Artifacts.search()` 是否具有足够稳定的跨 Family 语义；
- Memory search 需要哪些跨 backend 保证，哪些 mode 应保留为 backend-specific capability；
- `producer` 是否应继续作为公共 Artifact 写入参数；
- RFC 0001 的产品 Trigger 何时具备足够明确的用户动作和生命周期，可以单独定义 SDK 契约。

# Future possibilities

后续 RFC 可以定义新的 Artifact Family、Catalog backend、远程 fsspec backend，以及独立的 Trigger 或 durable
processing 方案。每项新能力都需要对应明确的产品动作，并说明为什么上游抽象不足以直接承接。

# Post-acceptance amendments

## 2026-07-16：Core Protocol 实现边界

本次修订收窄 RFC 0002 的实现边界。RFC 0001 的产品方向和前文 Guide-level 提议的 `PowerContext` 使用形式保持
不变。若原 RFC 对职责的分配与本节冲突，以本节为准。

面向内部开发者的 Core Protocol 集成导读通过一个扩展示例展开这些边界。导读提供实现背景，本 RFC 仍是规范性
决策记录；具体 Runtime 行为由生产集成 profile 验证，不再维护一套仅用于测试的平行应用。

### Core Protocol 契约

Core 包含三组领域契约和一个组合边界：

| 概念 | Core contracts | 修订后的约束 |
| --- | --- | --- |
| Source | `Source`, `SourceAdapter`, `SourceCatalog`, `SourceStore` | 将 typed native input 解析为可读取的 evidence object，再显式持久化 |
| Artifact | `ArtifactDraft`, `Artifact`, `ArtifactCatalog`, `ArtifactStore` | 从完整 content 和直接 evidence 提交不可变 Revision |
| Trigger | `Trigger`, `PolicyTransition` | 将 Signal 和 State 映射为新 State 与零个或多个 Action，不执行 I/O |
| Composition | `Sources`, `Artifacts`, `PowerContext` | 绑定显式选择的组件，不接管其生命周期 |

每个 Source adapter 对应一个 exact native input type、一个稳定的 `source_type` 和一个 concrete Source class。
Source 需要声明其可读取 value 是 captured 还是 referenced。一个 `SourceCatalog` 内的 adapter route 必须无歧义。
Source identity 和 deduplication 由具体集成决定；`(source_type, uri)` 不是通用 Core key。

Artifact store 持久化完整 Draft。`add()` 提交首个 Revision，`revise()` 使用精确 base Artifact 执行 optimistic
concurrency。Lineage 记录调用方提供的 Source 和精确上游 Artifact Revision。上下文检索、模型调用和下游
generation 都在 store 之外完成。

Trigger 对应 RFC 0001 产品 Trigger 中的 policy evaluation。它保持 sans-I/O，并将 State 和 Action 作为数据返回。
Integration runtime 负责生成 Signal、划分并持久化 State，以及执行 Action。

### 产品 facade 与 Artifact Family

Guide-level 中的 `PowerContext.open()`、`pc.memory` 和 `pc.handoff` 仍是提议的高层 API。具体集成可以基于最小
Core Protocol 提供这些便捷 service。

Memory 和 Handoff 复用 Artifact lifecycle，但其 content model、generation handler、projection 和 query 属于各自
Artifact Family 与 integration runtime。具体集成可以将其作为 built-in product service，无需给通用
`ArtifactCatalog` 增加 Memory generation 或 Handoff query。

前文 Public actions 表描述目标产品 facade。它不定义最小 Core package 的导出项，也不要求所有 Catalog backend
以相同方式实现这些 action。

### 执行与所有权

Integration runtime 负责：

- Memory 和 Handoff generation，包括模型调用；
- family-specific scope、retrieval、ranking 和 projection；
- Trigger Signal production 与应用定义的 State partition；
- State persistence、Action execution、worker 和 transaction boundary；
- scheduling、retry、recovery、approval 和 observability。

具体集成继续管理 database、scheduler、filesystem、model client 和 Agent object。SQLAlchemy、fsspec、
Pydantic AI、SQLite 和 APScheduler 都是可选实现，不是 Core Protocol 要求的抽象。

`Sources.add()` 和 `Artifacts.add()` 只负责持久化输入，不保证由 Source 派生的 Artifact 可以立即读取。集成
runtime 发起定时或事件驱动的工作，评估已配置的 Trigger，并执行返回的 Action。

### 被取代的实现假设

本次修订取代原 RFC 中的以下实现假设：

- 将 `SourceInput` 和 `SourceProvider[T]` 作为必需的底层 Source 扩展模型；
- 将 `(source_type, uri)` 幂等性作为通用 Source contract；
- 将通用 Artifact `list` 和 `search` 作为跨 Family 的必选 Core behavior；
- 由 Core 固定拥有 Memory extraction、consolidation 和 Handoff generation pipeline；
- Source 提交后由 Core 拥有后台派生；
- 将 SQLAlchemy、fsspec、Pydantic AI 或 schema version 作为必需的 Core composition contract。

产品 runtime 仍可采用其中任何一种方案，但这些方案属于实现选择，不是共享 Core Protocol contract。

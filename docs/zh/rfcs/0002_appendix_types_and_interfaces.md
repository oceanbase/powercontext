- Proposal Name: `core_sdk_types_and_interfaces`
- Start Date: 2026-07-10
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Parent RFC: [RFC 0002：Core SDK 产品模型](0002_core_sdk_product_model.md)
- Related Appendix: [执行与集成指南](0002_appendix_advanced_execution_and_integration.md)

# Status

这份 API sketch 不具有规范性。它展示 RFC 0002 在当前原型中的接口形状，供 PMC 评估。RFC 接受前，
名称、参数、默认值和返回类型都可以调整。如果附件与实现不一致，源码代表原型现状，主 RFC 代表待评审的
设计决策。

# Summary

当前原型暴露 `PowerContext`、四个领域服务、持久化值对象和 typed Source provider。
Memory 和 Handoff generation 使用固定内部 pipeline；内部生成 request、change set、调度条件和 Catalog 记录
都不是公共类型。

# Guide-level explanation

普通调用方只需创建 `PowerContext` 并提交 Source：

```python
pc = await PowerContext.open("powercontext.db", model=model)
async with pc:
    source = await pc.sources.add(source_input)
```

这一路径不承诺立即产生 Artifact。当应用必须等待生成时，再显式调用 `memory.remember()` 或
`handoff.prepare()`。

# Prototype interface sketch

## Package root

普通调用方从 `powercontext` package root 导入 composition root、主要值对象、内置输入和稳定产品错误。
Provider 作者也可从 package root 导入 `SourceInput` 和 `SourceProvider`。领域服务通过 `PowerContext`
属性获取，不由普通调用方构造。

## Composition root

```python
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from fsspec import AbstractFileSystem
from pydantic_ai.embeddings import EmbeddingModel, KnownEmbeddingModelName
from pydantic_ai.models import KnownModelName, Model
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext import SourceProvider


class PowerContext:
    filesystem: AbstractFileSystem
    sources: Sources
    artifacts: Artifacts
    memory: Memories
    handoff: Handoffs

    @classmethod
    async def from_backends(
        cls,
        *,
        engine: AsyncEngine,
        filesystem: AbstractFileSystem,
        model: Model | KnownModelName | str | None = None,
        embedding_model: EmbeddingModel | KnownEmbeddingModelName | str | None = None,
        source_providers: Iterable[SourceProvider[Any]] = (),
    ) -> Self: ...

    @classmethod
    async def open(
        cls,
        path: str | Path,
        *,
        model: Model | KnownModelName | str | None = None,
        embedding_model: EmbeddingModel | KnownEmbeddingModelName | str | None = None,
        source_providers: Iterable[SourceProvider[Any]] = (),
    ) -> Self: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
```

`open()` 创建并拥有 SQLite engine；`from_backends()` 不接管调用方注入的 engine 和 filesystem。
`filesystem` 是原生 fsspec 对象，不经过 PowerContext facade。

`model` 与 `embedding_model` 接受 Pydantic AI 原生 model input。通用 credential 可以由环境变量提供；
需要显式 credential、endpoint 或 client 时，调用方构造上游 Model 对象。

## Value objects

| 类型 | 主要字段 | 语义 |
| --- | --- | --- |
| `SourceInput` | `source_type`, `uri`, `observed_at`, `metadata` | provider 解析完成、尚未提交的 Source 输入 |
| `Source` | `id` 加 `SourceInput` 字段和 `created_at` | 已提交的稳定外部材料引用 |
| `ArtifactRef` | `artifact_id`, `revision` | 精确 Artifact Revision 引用 |
| `Artifact[T]` | `id`, `revision`, `family`, `content`, `producer`, `created_at`, `source_ids`, `dependencies` | 具体 Revision snapshot |
| `ArtifactSummary` | identity、latest Revision 和时间字段 | `list()` 的轻量结果 |
| `ArtifactHit` | `artifact_ref`, `family`, `score`, `excerpt` | 当前原型的通用 Artifact search 结果 |
| `Memory` | `Artifact[MemoryContent]` | Memory Family 的 Revision snapshot |
| `MemoryHit` | `memory_ref`, `entry_id`, `text` | 有序 Memory search 结果 |
| `Handoff` | `Artifact[HandoffContent]` | Handoff Family 的 Revision snapshot |

`Metadata` 和 Artifact content 使用 Pydantic `JsonValue` 序列化边界。Frozen dataclass 不承诺递归冻结调用方
传入的容器。

## Domain services

```python
from collections.abc import Sequence


class Sources:
    async def resolve(self, value: object) -> SourceInput: ...

    async def add(self, source_input: SourceInput) -> Source: ...

    async def get(self, source_id: str) -> Source: ...

    async def list(
        self,
        *,
        source_type: str | None = None,
        limit: int = 100,
    ) -> tuple[Source, ...]: ...

    async def read(self, source: Source) -> str: ...


class Artifacts:
    async def add(
        self,
        family: str,
        content: ContentT,
        *,
        producer: str = "powercontext",
        sources: Sequence[Source] = (),
        dependencies: Sequence[Artifact[object]] = (),
    ) -> Artifact[ContentT]: ...

    async def revise(
        self,
        artifact: Artifact[object],
        content: ContentT,
        *,
        producer: str = "powercontext",
        sources: Sequence[Source] = (),
        dependencies: Sequence[Artifact[object]] = (),
    ) -> Artifact[ContentT]: ...

    async def get(self, artifact: str | ArtifactRef) -> Artifact[object]: ...

    async def revisions(self, artifact_id: str) -> tuple[Artifact[object], ...]: ...

    async def list(
        self,
        *,
        family: str | None = None,
        limit: int | None = 100,
    ) -> tuple[ArtifactSummary, ...]: ...

    async def search(
        self,
        query: str,
        *,
        family: str | None = None,
        limit: int = 10,
    ) -> tuple[ArtifactHit, ...]: ...


class Memories:
    async def remember(
        self,
        *,
        memory: Memory | None = None,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
    ) -> Memory | None: ...

    async def forget(self, memory: Memory, entry_ids: Sequence[str]) -> Memory: ...

    async def organize(self, memory: Memory) -> Memory: ...

    async def get(self, memory: str | ArtifactRef) -> Memory: ...

    async def revisions(self, memory_id: str) -> tuple[Memory, ...]: ...

    async def list(self, *, limit: int | None = 100) -> tuple[ArtifactSummary, ...]: ...

    async def search(
        self,
        query: str,
        *,
        memory: ArtifactRef | None = None,
        limit: int = 10,
        mode: MemorySearchMode = "auto",
    ) -> tuple[MemoryHit, ...]: ...


class Handoffs:
    async def prepare(
        self,
        objective: str,
        *,
        sources: Sequence[Source] = (),
        artifacts: Sequence[Artifact[object]] = (),
    ) -> Handoff: ...

    async def get(self, handoff: str | ArtifactRef) -> Handoff: ...

    async def revisions(self, handoff_id: str) -> tuple[Handoff, ...]: ...

    async def list(self, *, limit: int | None = 100) -> tuple[ArtifactSummary, ...]: ...

    def render(
        self,
        handoff: Handoff,
        *,
        audience: Literal["human", "agent"],
    ) -> str: ...
```

Memory 和 Handoff 值对象继承 Artifact 的字段和引用语义，其服务提供 `get`、`revisions` 和 `list`。
Memory 使用 `remember`、`forget` 和 `organize` 写入，Handoff 使用 `prepare`。搜索接口可以随 backend 和
Family 调整。

`get(id)` 读取 latest Revision，`get(ref)` 读取精确 Revision。正常语义写入传入完整已持久化对象，
Core 从对象派生并校验血缘。

`remember()` 的首次 no-op 返回 `None`；已有 Memory 的 no-op 返回原 Revision。`render()` 是纯 projection，
不调用模型、不创建新 Artifact。

## Typed Source provider

```python
from typing import Protocol, TypeVar


SourceT = TypeVar("SourceT")


class SourceProvider(Protocol[SourceT]):
    @property
    def input_type(self) -> type[SourceT]: ...

    @property
    def source_type(self) -> str: ...

    async def resolve(self, value: SourceT) -> SourceInput: ...

    async def read(self, source: Source) -> str: ...
```

Provider 使用 exact input type 和 `source_type` 路由。`resolve()` 可以执行上游 I/O，但不提交 Source；
`read()` 返回 Core generation 当次需要的瞬时文本表示。输入类型或 `source_type` 重复注册应在构造时
失败。

# Behavioral guidelines

- 除原生 `filesystem` 和纯 projection 外，Core 公共 I/O 操作使用 async API；
- `Sources.add()` 只提交 Source，不提供 Source-to-Artifact read-after-write 保证；
- file-backed Family 直接使用 `PowerContext.filesystem`，并在自己的 content model 中定义路径或 URI；
- Memory/Handoff generation 是固定内部 pipeline，内部 request 和 change set 不是扩展契约；
- 除非 Core 明确转换为稳定产品错误，上游异常保留上游类型和 cause。

# Provisional prototype details

以下形状反映当前 SQLite 原型，不是本 RFC 已确定的跨后端契约：

- `Artifacts.search()` 的通用语义和 score；
- `MemorySearchMode = Literal["auto", "lexical", "semantic", "hybrid"]`；
- 缺少搜索能力时的 capability error 粒度；
- `producer` 是否仍由普通 Artifact 写入调用方指定。
